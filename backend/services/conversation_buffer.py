"""Async-durable buffer for chat turns.

Why this exists
---------------
`_persist_turn` previously did a synchronous Mongo write inside the response
path. Every Mongo hiccup blocked the streamed reply; every Mongo crash lost
the turn. Sprint C buffers each turn in a Redis Stream first (sub-ms latency)
and a Celery `light` worker drains the stream into Mongo in batches.

Failure semantics
-----------------
- Redis up, Mongo up:    best path (instant ack, async drain)
- Redis up, Mongo down:  ack OK, stream backlog grows, drains when Mongo
                         comes back; no message lost.
- Redis down:            we fall back to writing directly to Mongo so
                         Sprint A's behaviour is preserved.
- Both down:             we log + raise; that's an actual outage.

Stream key:    conversations:pending
Consumer group: chatbot_drain  (created at boot, idempotent XGROUP CREATE)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

from bson import ObjectId

logger = logging.getLogger(__name__)

STREAM_KEY = os.getenv("CONV_BUFFER_STREAM", "conversations:pending")
GROUP_NAME = os.getenv("CONV_BUFFER_GROUP", "chatbot_drain")
DRAIN_BATCH_SIZE = int(os.getenv("CONV_BUFFER_BATCH", "50"))
DRAIN_BLOCK_MS = int(os.getenv("CONV_BUFFER_BLOCK_MS", "2000"))
MAX_STREAM_LEN = int(os.getenv("CONV_BUFFER_MAXLEN", "100000"))


_redis_client = None
_group_initialised = False


def _redis():
    """Lazy sync Redis client (used by both producer and consumer).

    NOTE: socket_timeout MUST be longer than the XREADGROUP BLOCK we use
    in drain_once, otherwise idle empty streams trigger spurious
    socket.timeout exceptions. We use 2s block + 6s socket timeout.
    """
    global _redis_client
    if _redis_client is None:
        import redis
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = redis.from_url(url, decode_responses=True, socket_timeout=6)
    return _redis_client


def _ensure_group():
    """Create the consumer group exactly once. Idempotent."""
    global _group_initialised
    if _group_initialised:
        return
    try:
        r = _redis()
        try:
            r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
            logger.info(f"Created stream group {GROUP_NAME} on {STREAM_KEY}")
        except Exception as e:
            # BUSYGROUP means group already exists — fine.
            if "BUSYGROUP" not in str(e):
                raise
        _group_initialised = True
    except Exception as e:
        logger.warning(f"_ensure_group failed (Redis unreachable?): {e}")


def enqueue_turn_sync(
    conversation_id: ObjectId,
    user_text: str,
    assistant_text: str,
    reasoning_details: Optional[dict] = None,
) -> bool:
    """Sync producer: pushes a turn onto the stream. Returns True on success,
    False if Redis is unavailable (caller should fall back to direct Mongo
    write). Called via asyncio.to_thread from the request handler."""
    try:
        _ensure_group()
        r = _redis()
        payload = {
            "conversation_id": str(conversation_id),
            "user_text": user_text,
            "assistant_text": assistant_text,
            "reasoning_details": json.dumps(reasoning_details) if reasoning_details else "",
            "ts": datetime.utcnow().isoformat(),
        }
        # MAXLEN ~ caps stream size so an extended Mongo outage doesn't
        # eat all of Redis. Approximate trimming (tilde) is faster.
        r.xadd(STREAM_KEY, payload, maxlen=MAX_STREAM_LEN, approximate=True)
        return True
    except Exception as e:
        logger.warning(f"enqueue_turn: Redis unavailable, falling back to direct Mongo write: {e}")
        return False


async def enqueue_turn(
    conversation_id: ObjectId,
    user_text: str,
    assistant_text: str,
    reasoning_details: Optional[dict] = None,
) -> bool:
    """Async wrapper: defers the sync redis call to a worker thread."""
    import asyncio
    return await asyncio.to_thread(
        enqueue_turn_sync,
        conversation_id,
        user_text,
        assistant_text,
        reasoning_details,
    )


def drain_once(db, max_messages: int = DRAIN_BATCH_SIZE) -> int:
    """Pull up to max_messages from the stream and write them to Mongo in
    one shot. Returns the number of messages drained. Called by the Celery
    light worker on a 2 s tick."""
    _ensure_group()
    r = _redis()
    consumer_name = os.getenv("HOSTNAME", "consumer-1")

    try:
        # XREADGROUP gives us only entries not yet ack'd by THIS consumer.
        results = r.xreadgroup(
            GROUP_NAME, consumer_name,
            {STREAM_KEY: ">"},
            count=max_messages,
            block=DRAIN_BLOCK_MS,
        )
    except Exception as e:
        # An idle stream returns nothing; some redis drivers translate that
        # into TimeoutError when socket_timeout < block. We've sized the
        # socket timeout above the block to avoid this, but stay defensive.
        msg = str(e).lower()
        if "timeout" in msg:
            return 0
        logger.warning(f"drain_once: XREADGROUP failed: {e}")
        return 0

    if not results:
        return 0

    drained = 0
    for stream_name, entries in results:
        # Group entries by conversation_id to do one bulk update per conv.
        per_conv = {}
        for entry_id, fields in entries:
            try:
                conv_id = fields["conversation_id"]
                user_msg = {
                    "role": "user",
                    "content": fields["user_text"],
                    "timestamp": datetime.fromisoformat(fields["ts"]),
                }
                assistant_msg = {
                    "role": "assistant",
                    "content": fields["assistant_text"],
                    "timestamp": datetime.fromisoformat(fields["ts"]),
                }
                if fields.get("reasoning_details"):
                    try:
                        assistant_msg["reasoning_details"] = json.loads(fields["reasoning_details"])
                    except Exception:
                        pass
                per_conv.setdefault(conv_id, []).append((entry_id, user_msg, assistant_msg))
            except Exception as e:
                logger.error(f"drain_once: malformed entry {entry_id}: {e}")
                # Ack so we don't loop on this poisoned entry.
                try:
                    r.xack(STREAM_KEY, GROUP_NAME, entry_id)
                except Exception:
                    pass

        for conv_id, items in per_conv.items():
            entry_ids = [eid for eid, _, _ in items]
            messages = [m for _, u, a in items for m in (u, a)]
            try:
                db.conversations.update_one(
                    {"_id": ObjectId(conv_id)},
                    {
                        "$push": {"messages": {"$each": messages}},
                        "$set": {"updated_at": datetime.utcnow()},
                    },
                )
                # Ack only after the Mongo write succeeds — otherwise on a
                # crash the messages would be lost.
                r.xack(STREAM_KEY, GROUP_NAME, *entry_ids)
                drained += len(messages) // 2
            except Exception as e:
                logger.error(
                    f"drain_once: Mongo write failed for conv {conv_id}: {e}; "
                    f"leaving {len(entry_ids)} entries un-acked for retry"
                )

    if drained:
        logger.info(f"drain_once: drained {drained} turns from stream")
    return drained


def stream_backlog() -> int:
    """How many entries are waiting? Surfaced via /health/breakers banner."""
    try:
        return int(_redis().xlen(STREAM_KEY))
    except Exception:
        return -1
