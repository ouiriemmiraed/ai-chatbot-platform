"""Auto-classify each conversation into the schema documented in the
Sprint D plan.

Triggered after every assistant turn (best-effort enqueue from chat.py);
also runs in batch via the scheduler so historical conversations get
backfilled.

The classifier asks Gemini Flash Lite (cheapest fast model) for a strict
JSON object. We constrain the schema and validate it; on a parse failure
we fall back to a "neutral" tag so analytics don't break.

Output collection: `conversation_tags`
  one document per conversation, upserted on (conversation_id)
  fields: intent, sentiment, outcome, topic_keywords, language,
          is_lead_quality, summary, classified_at, model
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bson import ObjectId
from celery import shared_task

logger = logging.getLogger(__name__)

# Single source of truth for the schema. Used both in the prompt
# (so the LLM knows what to emit) and in validation.
ENUM_INTENT = (
    "support_billing", "support_technical", "sales", "info_seek", "abuse", "other",
)
ENUM_SENTIMENT = ("positive", "neutral", "frustrated", "angry")
ENUM_OUTCOME = ("resolved", "partial", "escalated", "abandoned", "unanswered")
ENUM_LEAD = ("high", "medium", "low", "none")


def _build_prompt(messages: List[Dict[str, Any]]) -> str:
    """Compact transcript + strict-JSON instructions."""
    transcript_lines = []
    for m in messages[:30]:  # cap context
        role = m.get("role", "?")
        content = (m.get("content") or "").strip().replace("\n", " ")[:600]
        transcript_lines.append(f"[{role}] {content}")
    transcript = "\n".join(transcript_lines) or "(empty conversation)"

    return (
        "Tu es un classificateur strict de conversations chatbot. Lis la "
        "conversation et renvoie UNIQUEMENT un JSON valide avec exactement ces "
        "champs (aucun texte autour):\n"
        "{\n"
        f'  "intent": one of {list(ENUM_INTENT)},\n'
        f'  "sentiment": one of {list(ENUM_SENTIMENT)},\n'
        f'  "outcome": one of {list(ENUM_OUTCOME)},\n'
        '  "topic_keywords": [string, ...]  // 1 to 5 mots-clés courts en minuscules,\n'
        '  "language": ISO 639-1 (e.g. "fr", "en", "ar"),\n'
        f'  "is_lead_quality": one of {list(ENUM_LEAD)},\n'
        '  "summary": string  // 1 phrase décrivant ce qui s\'est passé\n'
        "}\n\n"
        "Règles importantes:\n"
        "- outcome=unanswered si l'assistant a refusé / dit qu'il ne sait pas / pas pu répondre.\n"
        "- is_lead_quality=high si l'utilisateur a partagé son email + intention d'achat claire.\n"
        "- topic_keywords doivent être en anglais ou français selon la langue de la conversation.\n\n"
        "CONVERSATION:\n"
        f"{transcript}\n"
    )


def _validate_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise + validate; coerce unknown enums to safe defaults so a
    flaky LLM never crashes the analytics pipeline."""
    def pick(key, allowed, default):
        v = (raw.get(key) or "").strip().lower()
        return v if v in allowed else default

    keywords = raw.get("topic_keywords") or []
    if not isinstance(keywords, list):
        keywords = [str(keywords)]
    keywords = [str(k).strip().lower()[:32] for k in keywords if str(k).strip()][:5]

    lang = (raw.get("language") or "").strip().lower()[:5] or "und"

    summary = (raw.get("summary") or "").strip()[:500]

    return {
        "intent": pick("intent", ENUM_INTENT, "other"),
        "sentiment": pick("sentiment", ENUM_SENTIMENT, "neutral"),
        "outcome": pick("outcome", ENUM_OUTCOME, "partial"),
        "topic_keywords": keywords,
        "language": lang,
        "is_lead_quality": pick("is_lead_quality", ENUM_LEAD, "none"),
        "summary": summary,
    }


def _call_classifier_llm(prompt: str) -> Optional[Dict[str, Any]]:
    """Classify via the LLM fallback chain (Gemini → OpenRouter → Ollama).

    Returns None on total failure; the caller writes a neutral tag in that
    case so the conversation is still indexed and is retryable later.
    """
    from core.llm_complete import complete_sync

    try:
        provider, text = complete_sync(
            prompt,
            temperature=0.1,
            max_tokens=512,
            json_mode=True,
            gemini_model=os.getenv("CLASSIFIER_MODEL", "gemini-2.5-flash-lite"),
        )
    except Exception as e:
        logger.warning(f"Classifier LLM call failed (all providers): {e}")
        return None

    text = (text or "").strip()
    if not text:
        return None
    # JSON mode should give us a clean object, but be defensive across
    # providers: extract the outermost {...}.
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace == -1 or last_brace == -1:
        logger.warning(f"Classifier returned non-JSON via {provider}: {text[:200]!r}")
        return None
    try:
        return json.loads(text[first_brace : last_brace + 1])
    except json.JSONDecodeError as e:
        logger.warning(f"Classifier JSON decode failed ({provider}): {e} — {text[:200]!r}")
        return None


def _get_sync_db():
    from pymongo import MongoClient
    return MongoClient(
        os.getenv("MONGODB_URL", "mongodb://localhost:27017"),
        serverSelectionTimeoutMS=5000,
    )[os.getenv("DATABASE_NAME", "chatbot_db")]


@shared_task(name="services.conversation_classifier.classify_conversation", bind=True, max_retries=2)
def classify_conversation(self, conversation_id: str) -> Dict[str, Any]:
    """Classify one conversation and upsert the result into conversation_tags.

    Triggered from the chat router after every successful answer. Idempotent —
    re-classifying overwrites the prior tag.
    """
    db = _get_sync_db()
    try:
        oid = ObjectId(conversation_id)
    except Exception:
        logger.warning(f"classify_conversation: bad ObjectId {conversation_id!r}")
        return {"ok": False, "reason": "bad_id"}

    conv = db.conversations.find_one({"_id": oid})
    if not conv:
        logger.warning(f"classify_conversation: conv {conversation_id} not found")
        return {"ok": False, "reason": "not_found"}

    messages = conv.get("messages") or []
    if not messages:
        # Don't tag empty conversations; the caller may retry once messages exist.
        return {"ok": False, "reason": "no_messages"}

    prompt = _build_prompt(messages)
    raw = _call_classifier_llm(prompt)

    if raw is None:
        # Neutral tag so the document exists and the LLM can be retried
        # by the backfill job. Marked for retry by classified_at=null.
        tag = {
            "intent": "other", "sentiment": "neutral", "outcome": "partial",
            "topic_keywords": [], "language": "und", "is_lead_quality": "none",
            "summary": "(classification pending)",
        }
        classified_at = None
    else:
        tag = _validate_payload(raw)
        classified_at = datetime.utcnow()

    db.conversation_tags.update_one(
        {"conversation_id": oid},
        {
            "$set": {
                "conversation_id": oid,
                "chatbot_id": conv.get("chatbot_id"),
                # Denormalised so analytics can window by the conversation's
                # creation time without an $in over the conversation set.
                "conv_created_at": conv.get("created_at"),
                "message_count": len(messages),
                "classified_at": classified_at,
                "model": os.getenv("CLASSIFIER_MODEL", "gemini-2.5-flash-lite"),
                **tag,
            }
        },
        upsert=True,
    )
    logger.info(
        f"classified {conversation_id}: intent={tag['intent']} "
        f"outcome={tag['outcome']} sentiment={tag['sentiment']}"
    )
    return {"ok": True, "tag": tag}


@shared_task(name="services.conversation_classifier.backfill_classifications")
def backfill_classifications(limit: int = 200) -> Dict[str, Any]:
    """Periodic catch-up: tag conversations that have messages but no
    classified_at (either never seen or LLM call failed before)."""
    db = _get_sync_db()
    # Conversations that exist in `conversations` but have no entry, OR have
    # a tag with classified_at=null (= prior failure).
    pending = list(db.conversations.find(
        {
            "messages.0": {"$exists": True},
            "updated_at": {"$gte": datetime.utcnow() - timedelta(days=14)},
        },
        {"_id": 1},
    ).limit(limit * 5))

    enqueued = 0
    for conv in pending:
        existing = db.conversation_tags.find_one({"conversation_id": conv["_id"]}, {"classified_at": 1})
        if existing and existing.get("classified_at"):
            continue
        from worker import celery_app
        celery_app.send_task(
            "services.conversation_classifier.classify_conversation",
            args=[str(conv["_id"])],
            queue="light",
        )
        enqueued += 1
        if enqueued >= limit:
            break
    logger.info(f"backfill_classifications: enqueued {enqueued} conversations")
    return {"enqueued": enqueued}
