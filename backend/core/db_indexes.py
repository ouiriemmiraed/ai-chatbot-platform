"""Idempotent MongoDB index creation, called from FastAPI startup.

Without these indexes the hot path of nearly every endpoint (login, list
conversations, dedup pages on re-crawl, lookup permissions) does a full
collection scan. With them, the same queries run in O(log n).

This module is safe to call repeatedly — Motor's `create_index` is a no-op
when an equivalent index already exists with the same name and key spec.
A mismatch on options (e.g. you change `unique=True` after the fact) will
raise `OperationFailure`; in that case we log and continue rather than
crash startup, so a deploy is never blocked by a legacy index.
"""
from __future__ import annotations

import logging
from typing import Any, List, Tuple, Optional, Dict

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure

logger = logging.getLogger(__name__)

# (collection, keys, options) — options unpack into create_index kwargs.
# `name` is given explicitly so re-runs match by name, not key fingerprint.
_INDEX_SPECS: List[Tuple[str, List[Tuple[str, int]], Dict[str, Any]]] = [
    # Auth — login does case-insensitive email lookups; collation strength=2
    # makes "Alice@x.com" == "alice@x.com" and lets us drop the slow $regex.
    (
        "users",
        [("email", ASCENDING)],
        {
            "unique": True,
            "name": "users_email_unique_ci",
            "collation": {"locale": "en", "strength": 2},
        },
    ),

    # Conversations — admin lists "latest first per chatbot".
    (
        "conversations",
        [("chatbot_id", ASCENDING), ("updated_at", DESCENDING)],
        {"name": "conversations_chatbot_updated"},
    ),
    # Analytics windows are anchored on created_at (dashboard + funnel + overview).
    (
        "conversations",
        [("chatbot_id", ASCENDING), ("created_at", DESCENDING)],
        {"name": "conversations_chatbot_created"},
    ),

    # Scraped pages — crawler dedupes by (chatbot_id, url) before re-embedding.
    (
        "scraped_pages",
        [("chatbot_id", ASCENDING), ("url", ASCENDING)],
        {"unique": True, "name": "scraped_pages_chatbot_url_unique"},
    ),
    (
        "scraped_pages",
        [("chatbot_id", ASCENDING), ("last_scraped_at", DESCENDING)],
        {"name": "scraped_pages_chatbot_lastscraped"},
    ),

    # Chatbot permissions — every chat call checks (chatbot_id, user_email).
    (
        "chatbot_permissions",
        [("chatbot_id", ASCENDING), ("user_email", ASCENDING)],
        {"unique": True, "name": "permissions_chatbot_email_unique"},
    ),

    # Leads — dedupe captured emails per chatbot.
    (
        "leads",
        [("chatbot_id", ASCENDING), ("email", ASCENDING)],
        {"unique": True, "name": "leads_chatbot_email_unique"},
    ),

    # Audit logs — both a recency index and a TTL index (90-day retention).
    (
        "audit_logs",
        [("timestamp", DESCENDING)],
        {"name": "audit_timestamp_desc"},
    ),
    (
        "audit_logs",
        [("timestamp", ASCENDING)],
        {"name": "audit_timestamp_ttl", "expireAfterSeconds": 90 * 24 * 3600},
    ),

    # Chatbot configs — admin lookups by owner.
    (
        "chatbot_configs",
        [("owner_email", ASCENDING)],
        {"name": "chatbot_configs_owner"},
    ),

    # Alerts — UI lists per chatbot, sorted recent-first.
    (
        "alerts",
        [("chatbot_id", ASCENDING), ("created_at", DESCENDING)],
        {"name": "alerts_chatbot_created"},
    ),

    # rag_chunks (Sprint C) — lexical fallback when ChromaDB is unreachable.
    # Compound (chatbot_id, _id) keeps deletes per-tenant fast.
    (
        "rag_chunks",
        [("chatbot_id", ASCENDING)],
        {"name": "rag_chunks_chatbot"},
    ),
    (
        "rag_chunks",
        [("content", "text")],
        {
            "name": "rag_chunks_text",
            "default_language": "none",   # multilingual content; disable stemming
        },
    ),

    # ── Sprint D: analytics ─────────────────────────────────────────────
    # conversation_tags — owner dashboard reads by (chatbot_id, classified_at).
    (
        "conversation_tags",
        [("conversation_id", ASCENDING)],
        {"unique": True, "name": "conv_tags_unique"},
    ),
    (
        "conversation_tags",
        [("chatbot_id", ASCENDING), ("classified_at", DESCENDING)],
        {"name": "conv_tags_chatbot_time"},
    ),
    (
        "conversation_tags",
        [("chatbot_id", ASCENDING), ("outcome", ASCENDING)],
        {"name": "conv_tags_chatbot_outcome"},
    ),
    # Analytics window by the denormalised conversation creation time.
    (
        "conversation_tags",
        [("chatbot_id", ASCENDING), ("conv_created_at", DESCENDING)],
        {"name": "conv_tags_chatbot_convcreated"},
    ),

    # quality_scores — sparkline + per-turn lookup.
    (
        "quality_scores",
        [("conversation_id", ASCENDING), ("turn_index", ASCENDING)],
        {"unique": True, "name": "quality_conv_turn_unique"},
    ),
    (
        "quality_scores",
        [("chatbot_id", ASCENDING), ("scored_at", DESCENDING)],
        {"name": "quality_chatbot_time"},
    ),
    (
        "quality_scores",
        [("chatbot_id", ASCENDING), ("conv_created_at", DESCENDING)],
        {"name": "quality_chatbot_convcreated"},
    ),

    # llm_costs — daily / per-provider rollups.
    (
        "llm_costs",
        [("chatbot_id", ASCENDING), ("timestamp", DESCENDING)],
        {"name": "llm_costs_chatbot_time"},
    ),
    (
        "llm_costs",
        [("timestamp", ASCENDING)],
        {"name": "llm_costs_ttl", "expireAfterSeconds": 365 * 24 * 3600},  # 1 year
    ),
    # Dashboard cost is summed by the in-window conversation set (created_at anchor).
    (
        "llm_costs",
        [("conversation_id", ASCENDING)],
        {"name": "llm_costs_conversation"},
    ),

    # message_feedback — satisfaction donut (by chatbot) + per-conversation
    # lookup & cascade delete. Had only the default _id index → full scans.
    (
        "message_feedback",
        [("chatbot_id", ASCENDING)],
        {"name": "feedback_chatbot"},
    ),
    (
        "message_feedback",
        [("conversation_id", ASCENDING)],
        {"name": "feedback_conversation"},
    ),

    # content_gaps — owner dashboard "anomalies" widget.
    (
        "content_gaps",
        [("chatbot_id", ASCENDING), ("status", ASCENDING)],
        {"name": "content_gaps_chatbot_status"},
    ),
]


async def ensure_indexes(db) -> None:
    """Create all expected indexes; tolerate already-existing equivalents.

    Logs each created index and skips any spec whose collection cannot be
    indexed (e.g. legacy duplicate values that violate a new uniqueness
    constraint) so startup never crashes on existing data.
    """
    if db is None:
        logger.warning("ensure_indexes: db is None, skipping")
        return

    created = 0
    skipped = 0
    for coll_name, keys, opts in _INDEX_SPECS:
        try:
            await db[coll_name].create_index(keys, **opts)
            created += 1
            logger.info(f"Index ready: {coll_name}.{opts.get('name', keys)}")
        except OperationFailure as e:
            # Most common: existing index with same name but different options,
            # or duplicate-key violation when promoting to unique.
            skipped += 1
            logger.warning(
                f"Index skip on {coll_name}.{opts.get('name')}: "
                f"{e.code or '?'} {e.details.get('errmsg', str(e))[:200] if hasattr(e, 'details') and e.details else str(e)[:200]}"
            )
        except Exception as e:
            skipped += 1
            logger.warning(f"Index skip on {coll_name}.{opts.get('name')}: {type(e).__name__}: {e}")

    logger.info(f"ensure_indexes: {created} created/verified, {skipped} skipped")
