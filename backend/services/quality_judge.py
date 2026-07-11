"""Score the quality of each assistant turn — Sprint D Item 3.

For every (user_question, assistant_answer, rag_sources) triple we ask a
second LLM (the "judge") to rate:
  - groundedness        : was the answer supported by the RAG passages?
  - relevance           : did it actually address the user's question?
  - tone                : warm / professional / appropriate?
  - hallucination_risk  : low / medium / high

Output collection: `quality_scores`
  one document per assistant turn, identified by (conversation_id, turn_index).

Triggered by `score_conversation` (one Celery task per conversation,
scores every assistant turn). The classifier and judge run in parallel —
neither blocks the user-facing response.

Why a separate task and not folded into classifier
--------------------------------------------------
Classification is per-conversation; quality is per-turn. Splitting them
keeps each task small + retriable, and lets us judge a turn without
re-classifying a long conversation.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from celery import shared_task

logger = logging.getLogger(__name__)


def _build_judge_prompt(question: str, answer: str, rag_excerpts: List[str]) -> str:
    """Compact, JSON-mode-friendly prompt for the judge model."""
    sources_block = "\n".join(f"- {s[:400]}" for s in (rag_excerpts or [])[:5]) or "(no sources)"
    return (
        "Tu es un juge de qualité pour les réponses d'un chatbot. "
        "Lis la question, la réponse, et les passages source RAG. "
        "Renvoie UNIQUEMENT un JSON strict:\n"
        "{\n"
        '  "groundedness": float 0..1,    // la réponse est-elle ancrée dans les sources ?\n'
        '  "relevance": float 0..1,       // adresse-t-elle vraiment la question ?\n'
        '  "tone": float 0..1,            // chaleureux et professionnel ?\n'
        '  "hallucination_risk": "low" | "medium" | "high",\n'
        '  "rationale": string             // 1 phrase max\n'
        "}\n\n"
        "Règles:\n"
        "- groundedness=0.0 si la réponse invente des faits non présents dans les sources.\n"
        "- groundedness=1.0 si chaque affirmation est dans les sources.\n"
        "- Si aucune source n'est fournie, juge groundedness uniquement sur la cohérence interne.\n"
        "- hallucination_risk=high UNIQUEMENT quand la réponse affirme des faits faux ou inventés.\n"
        "- IMPORTANT — un REFUS poli ou un \"je ne dispose pas de cette information\" (sans inventer "
        "aucun fait) N'EST PAS une hallucination : c'est le comportement SÛR attendu. Dans ce cas "
        "hallucination_risk=\"low\" et groundedness>=0.8 (refuser correctement = bien ancré).\n\n"
        f"QUESTION:\n{question[:1500]}\n\n"
        f"REPONSE:\n{answer[:2000]}\n\n"
        f"SOURCES RAG:\n{sources_block}\n"
    )


_REFUSAL_MARKERS = (
    "je ne dispose pas", "ne dispose pas d'inform", "je suis désolé, mais je ne",
    "je ne peux pas vous fournir", "je n'ai pas d'information", "je ne dispose d'aucune",
    "i don't have", "i do not have", "i'm sorry, but i don", "i cannot provide",
)


def _is_refusal(answer: str) -> bool:
    """A polite 'I don't have that info' answer invents nothing — it must NOT be
    scored as a hallucination. Detected deterministically so the metric is right
    even if the judge LLM mis-rates it."""
    a = (answer or "").lower()
    return any(m in a for m in _REFUSAL_MARKERS) and "momentan" not in a


def _validate_score(raw: Dict[str, Any]) -> Dict[str, Any]:
    def clamp01(v, default=0.5):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, f))

    risk = (raw.get("hallucination_risk") or "").strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = "medium"

    return {
        "groundedness": clamp01(raw.get("groundedness")),
        "relevance": clamp01(raw.get("relevance")),
        "tone": clamp01(raw.get("tone"), default=0.7),
        "hallucination_risk": risk,
        "rationale": (raw.get("rationale") or "").strip()[:300],
    }


def _call_judge_llm(prompt: str) -> Optional[Dict[str, Any]]:
    """Score answer quality via the LLM fallback chain (Gemini → OpenRouter
    → Ollama). Returns None on total failure (caller skips this row)."""
    from core.llm_complete import complete_sync

    try:
        provider, text = complete_sync(
            prompt,
            temperature=0.0,
            max_tokens=300,
            json_mode=True,
            gemini_model=os.getenv("JUDGE_MODEL", "gemini-2.5-flash-lite"),
        )
    except Exception as e:
        logger.warning(f"Judge LLM call failed (all providers): {e}")
        return None

    text = (text or "").strip()
    if not text:
        return None
    first, last = text.find("{"), text.rfind("}")
    if first == -1 or last == -1:
        return None
    try:
        return json.loads(text[first : last + 1])
    except json.JSONDecodeError:
        return None


def _get_sync_db():
    from pymongo import MongoClient
    return MongoClient(
        os.getenv("MONGODB_URL", "mongodb://localhost:27017"),
        serverSelectionTimeoutMS=5000,
    )[os.getenv("DATABASE_NAME", "chatbot_db")]


def _aggregate_score(scores: Dict[str, Any]) -> float:
    """Single 0..1 quality number for the dashboard sparkline."""
    return round(
        0.45 * scores["groundedness"]
        + 0.35 * scores["relevance"]
        + 0.20 * scores["tone"],
        3,
    )


@shared_task(name="services.quality_judge.score_conversation", bind=True, max_retries=2)
def score_conversation(self, conversation_id: str, last_n_turns: int = 1) -> Dict[str, Any]:
    """Judge the last N assistant turns of a conversation. Default 1 (only
    the most recent turn) — keeps cost predictable. Set last_n_turns=999
    to backfill the whole conversation.
    """
    db = _get_sync_db()
    try:
        oid = ObjectId(conversation_id)
    except Exception:
        return {"ok": False, "reason": "bad_id"}

    conv = db.conversations.find_one({"_id": oid})
    if not conv:
        return {"ok": False, "reason": "not_found"}

    messages = conv.get("messages") or []
    if not messages:
        return {"ok": False, "reason": "no_messages"}

    # Pair user/assistant turns. (msg[i] user → msg[i+1] assistant.)
    pairs = []
    for i in range(len(messages) - 1):
        if messages[i].get("role") == "user" and messages[i + 1].get("role") == "assistant":
            pairs.append((i, messages[i], messages[i + 1]))

    if not pairs:
        return {"ok": False, "reason": "no_paired_turns"}

    pairs = pairs[-last_n_turns:]
    chatbot_id = conv.get("chatbot_id")
    # #2: RAG passages retrieved for the most recent turn, persisted by the chat
    # endpoint. Used to judge groundedness against real context (not just
    # internal coherence). Only the latest turn carries them.
    latest_rag = conv.get("latest_rag_sources") or []
    last_turn_idx = pairs[-1][0] if pairs else -1
    judged = 0

    for turn_idx, user_msg, assistant_msg in pairs:
        # Skip if already judged for this turn
        existing = db.quality_scores.find_one(
            {"conversation_id": oid, "turn_index": turn_idx}, {"_id": 1}
        )
        if existing:
            continue

        answer_text = assistant_msg.get("content", "")
        # #2: judge groundedness against the real retrieved context for the
        # latest turn (older turns fall back to internal-coherence scoring).
        rag_sources = assistant_msg.get("rag_sources") or (
            latest_rag if turn_idx == last_turn_idx else []
        )

        prompt = _build_judge_prompt(
            user_msg.get("content", ""),
            answer_text,
            rag_sources,
        )
        raw = _call_judge_llm(prompt)
        if raw is None:
            continue
        scored = _validate_score(raw)

        # #1: a correct refusal fabricates nothing → force it OUT of the
        # hallucination bucket regardless of what the judge LLM returned.
        if _is_refusal(answer_text):
            scored["hallucination_risk"] = "low"
            scored["groundedness"] = max(scored["groundedness"], 0.85)
            if not scored.get("rationale"):
                scored["rationale"] = "Refus poli correct — aucun fait inventé."

        scored["aggregate"] = _aggregate_score(scored)

        db.quality_scores.insert_one({
            "conversation_id": oid,
            "chatbot_id": chatbot_id,
            # Denormalised conversation creation time → analytics windowing
            # without an $in over the conversation set.
            "conv_created_at": conv.get("created_at"),
            "turn_index": turn_idx,
            "scored_at": datetime.utcnow(),
            "model": os.getenv("JUDGE_MODEL", "gemini-2.5-flash-lite"),
            **scored,
        })
        judged += 1

    logger.info(f"score_conversation {conversation_id}: judged {judged} turns")
    return {"ok": True, "judged": judged}
