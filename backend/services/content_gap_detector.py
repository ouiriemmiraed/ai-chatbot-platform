"""Content-gap detector — Sprint D Item 2.

Pulls every conversation tagged `outcome=unanswered` over the last N days,
embeds the user question, runs a simple radius-based clustering, and
writes one document per cluster of >= MIN_CLUSTER_SIZE similar questions.

Output collection: `content_gaps`
  one document per cluster
  fields: chatbot_id, cluster_id (sha256 of representative), question_count,
          example_questions[], suggested_topic, detected_at,
          status: "open" | "addressed" | "ignored"

Used by the admin dashboard's "Anomalies / Suggestions" widget so the
chatbot owner sees actionable content debt at a glance.

Algorithm
---------
We don't bring in scikit-learn for one clustering step. The radius-based
greedy approach below has O(N^2) worst case — fine for N up to a few
thousand pending questions per chatbot per week.

1. Load the embedding model that the RAG service already uses (cached).
2. Compute one embedding per question (with the e5 "query: " prefix).
3. Sort questions by length desc so the "representative" of each cluster
   is the most informative variant.
4. For each unassigned question, build a cluster from all unassigned
   questions whose cosine similarity to it >= SIMILARITY_THRESHOLD.
5. Drop clusters smaller than MIN_CLUSTER_SIZE.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from celery import shared_task

logger = logging.getLogger(__name__)


SIMILARITY_THRESHOLD = float(os.getenv("CONTENT_GAP_SIMILARITY", "0.83"))
MIN_CLUSTER_SIZE = int(os.getenv("CONTENT_GAP_MIN_SIZE", "5"))
LOOKBACK_DAYS = int(os.getenv("CONTENT_GAP_LOOKBACK_DAYS", "7"))
MAX_QUESTIONS_PER_RUN = int(os.getenv("CONTENT_GAP_MAX_Q", "1000"))


def _get_sync_db():
    from pymongo import MongoClient
    return MongoClient(
        os.getenv("MONGODB_URL", "mongodb://localhost:27017"),
        serverSelectionTimeoutMS=5000,
    )[os.getenv("DATABASE_NAME", "chatbot_db")]


def _last_user_question(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "user" and m.get("content"):
            return m["content"].strip()
    return ""


def _embed_questions(questions: List[str]) -> "np.ndarray":
    """Re-use the singleton RAG embedding model so we don't load a 1.2 GB
    blob a second time."""
    import numpy as np
    from services.rag_service import rag_service
    return np.array(rag_service._get_embeddings(questions, is_query=True))


def _cosine_matrix(M):
    """Pairwise cosine similarity. Returns an N×N matrix in [-1, 1]."""
    import numpy as np
    norms = np.linalg.norm(M, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Mn = M / norms
    return Mn @ Mn.T


def _cluster(questions: List[str]) -> List[List[int]]:
    """Greedy radius-based clustering. Returns a list of clusters, each a
    list of indices into `questions`."""
    if not questions:
        return []
    M = _embed_questions(questions)
    sim = _cosine_matrix(M)

    # Sort by length desc so the longest (most descriptive) candidate
    # becomes the cluster seed.
    order = sorted(range(len(questions)), key=lambda i: -len(questions[i]))
    assigned = [False] * len(questions)
    clusters: List[List[int]] = []

    for i in order:
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(len(questions)):
            if i == j or assigned[j]:
                continue
            if sim[i][j] >= SIMILARITY_THRESHOLD:
                cluster.append(j)
                assigned[j] = True
        clusters.append(cluster)

    return clusters


def _suggest_topic(example_questions: List[str]) -> str:
    """Cheap LLM call to produce a one-line topic name + suggested article
    title via the fallback chain (Gemini → OpenRouter → Ollama). Falls back
    to the longest question if every provider is unavailable."""
    if not example_questions:
        return "untitled"
    from core.llm_complete import complete_sync

    joined = "\n".join(f"- {q[:200]}" for q in example_questions[:10])
    prompt = (
        "Donne un titre court (max 12 mots) pour un article qui répondrait "
        "à toutes ces questions de visiteurs. Réponds UNIQUEMENT avec le titre, "
        "pas de guillemets, pas d'explication.\n\n"
        f"{joined}"
    )
    try:
        _provider, text = complete_sync(
            prompt,
            temperature=0.2,
            max_tokens=80,
            json_mode=False,  # plain-text title, not JSON
            gemini_model=os.getenv("CLASSIFIER_MODEL", "gemini-2.5-flash-lite"),
        )
        title = (text or "").strip().strip('"').strip("'")
        return title[:120] or example_questions[0][:120]
    except Exception as e:
        logger.warning(f"_suggest_topic LLM failed (all providers): {e}")
        return example_questions[0][:120]


@shared_task(name="services.content_gap_detector.detect_content_gaps")
def detect_content_gaps(chatbot_id: str = None) -> Dict[str, Any]:
    """Detect clusters of unanswered questions for one chatbot (or every
    chatbot if `chatbot_id` is None — periodic mode).

    Stores one row per cluster in `content_gaps`. Existing rows for the
    same chatbot are wiped before the new run so the UI always shows the
    current week.
    """
    db = _get_sync_db()
    cutoff = datetime.utcnow() - timedelta(days=LOOKBACK_DAYS)

    # 1. Find chatbots to scan
    if chatbot_id:
        targets = [chatbot_id]
    else:
        targets = db.conversations.distinct(
            "chatbot_id", {"updated_at": {"$gte": cutoff}}
        )
    summary = {"scanned_chatbots": 0, "clusters_written": 0}

    for cb in targets:
        # 2. Collect unanswered questions from tagged conversations
        unanswered_convs = list(db.conversation_tags.find(
            {"chatbot_id": cb, "outcome": "unanswered", "classified_at": {"$gte": cutoff}},
            {"conversation_id": 1},
        ).limit(MAX_QUESTIONS_PER_RUN))

        if len(unanswered_convs) < MIN_CLUSTER_SIZE:
            # Not enough signal to cluster.
            db.content_gaps.delete_many({"chatbot_id": cb})  # clear stale
            summary["scanned_chatbots"] += 1
            continue

        conv_ids = [c["conversation_id"] for c in unanswered_convs]
        convs = db.conversations.find({"_id": {"$in": conv_ids}}, {"messages": 1})

        # Deduplicate identical questions (normalized) before clustering.
        # Duplicates trivially self-match at cosine 1.0, so without this a few
        # repeated questions could manufacture a cluster past MIN_CLUSTER_SIZE
        # and inflate the reported question_count.
        questions = []
        _seen = set()
        for c in convs:
            q = _last_user_question(c.get("messages") or [])
            if not q:
                continue
            norm = " ".join(q.lower().split())
            if norm in _seen:
                continue
            _seen.add(norm)
            questions.append(q)
        if len(questions) < MIN_CLUSTER_SIZE:
            db.content_gaps.delete_many({"chatbot_id": cb})
            summary["scanned_chatbots"] += 1
            continue

        # 3. Cluster
        try:
            clusters = _cluster(questions)
        except Exception as e:
            logger.error(f"clustering failed for chatbot {cb}: {e}")
            summary["scanned_chatbots"] += 1
            continue

        # 4. Persist clusters above the threshold; replace prior batch
        db.content_gaps.delete_many({"chatbot_id": cb})
        for cluster in clusters:
            if len(cluster) < MIN_CLUSTER_SIZE:
                continue
            example_questions = [questions[idx] for idx in cluster][:10]
            cluster_id = hashlib.sha256(
                (cb + "|" + example_questions[0]).encode()
            ).hexdigest()[:16]
            suggested = _suggest_topic(example_questions)
            db.content_gaps.insert_one({
                "chatbot_id": cb,
                "cluster_id": cluster_id,
                "question_count": len(cluster),
                "example_questions": example_questions,
                "suggested_topic": suggested,
                "detected_at": datetime.utcnow(),
                "status": "open",
            })
            summary["clusters_written"] += 1

        summary["scanned_chatbots"] += 1

    logger.info(
        f"detect_content_gaps: scanned {summary['scanned_chatbots']} chatbot(s), "
        f"wrote {summary['clusters_written']} clusters"
    )
    return summary
