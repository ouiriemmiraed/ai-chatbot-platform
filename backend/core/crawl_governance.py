"""Backpressure + auto-retry for the crawler.

Two things this module owns:

1. **Backpressure** — `check_crawl_capacity()` returns the number of jobs
   queued on the `crawl` queue. The router uses it to refuse new crawls
   with 429 + Retry-After when the system is at capacity.

2. **Auto-retry of failed crawls** — `retry_failed_crawls()` is a periodic
   sync function (called from `core.scheduler`) that finds chatbots whose
   `crawl_status == "failed"` and `crawl_retry_count < MAX_RETRIES`,
   re-enqueues a new crawl with exponential backoff, and marks the chatbot
   `failed_permanent` once retries are exhausted.

The default knobs are tuned for the small VPS:
- backpressure threshold: 10 jobs already queued on `crawl` (we have 1 worker)
- max retries: 3
- backoff: 5 min, 30 min, 2 h
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


CRAWL_QUEUE_NAME = os.getenv("CRAWL_QUEUE_NAME", "crawl")
CRAWL_BACKPRESSURE_THRESHOLD = int(os.getenv("CRAWL_BACKPRESSURE_THRESHOLD", "10"))
CRAWL_MAX_RETRIES = int(os.getenv("CRAWL_MAX_RETRIES", "3"))
# Exponential-ish backoff per retry attempt, in seconds.
CRAWL_RETRY_BACKOFFS = [5 * 60, 30 * 60, 2 * 3600]


def _redis_sync():
    """Sync redis client — same pattern as conversation_buffer."""
    import redis
    return redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True, socket_timeout=2,
    )


def queue_depth(queue_name: str = CRAWL_QUEUE_NAME) -> int:
    """How many jobs are waiting on the given Celery queue.

    Celery serialises Redis-broker queues as plain Redis lists named after
    the queue, so LLEN gives us the depth without any Celery dependency
    in this hot path."""
    try:
        return int(_redis_sync().llen(queue_name))
    except Exception as e:
        logger.warning(f"queue_depth({queue_name}) failed: {e}")
        return -1  # unknown


def check_crawl_capacity() -> tuple[bool, int]:
    """Returns (can_accept, retry_after_seconds).

    can_accept=True  → enqueue normally
    can_accept=False → caller should respond 429 with Retry-After headers
    """
    depth = queue_depth(CRAWL_QUEUE_NAME)
    if depth < 0:
        # Redis unreachable — be permissive (Celery enqueue would also fail
        # anyway, but at least we won't block legitimate requests).
        return True, 0
    if depth >= CRAWL_BACKPRESSURE_THRESHOLD:
        # Worker takes ~10 min per crawl on average; estimate retry-after.
        retry_after = max(60, depth * 600 // 2)
        return False, retry_after
    return True, 0


def retry_failed_crawls(*, db=None) -> int:
    """Scheduler-friendly periodic job: re-queue failed crawls.

    Called from core.scheduler every 5 min. Sync-friendly: opens its own
    pymongo client when no db is passed, so the existing thread-pool
    based scheduler can call it directly.
    """
    if db is None:
        try:
            from pymongo import MongoClient
            client = MongoClient(
                os.getenv("MONGODB_URL", "mongodb://localhost:27017"),
                serverSelectionTimeoutMS=3000,
            )
            db = client[os.getenv("DATABASE_NAME", "chatbot_db")]
        except Exception as e:
            logger.warning(f"retry_failed_crawls: cannot reach mongo: {e}")
            return 0

    try:
        # Candidates: status==failed, retry count under cap, last attempt
        # older than the configured backoff for the next attempt.
        now = datetime.utcnow()
        candidates = list(db.chatbot_configs.find(
            {
                "crawl_status": "failed",
                "$or": [
                    {"crawl_retry_count": {"$exists": False}},
                    {"crawl_retry_count": {"$lt": CRAWL_MAX_RETRIES}},
                ],
            },
            {"_id": 1, "scraped_url": 1, "crawl_retry_count": 1, "crawl_failed_at": 1},
        ))
    except Exception as e:
        logger.warning(f"retry_failed_crawls: query failed: {e}")
        return 0

    enqueued = 0
    for c in candidates:
        retry_count = int(c.get("crawl_retry_count") or 0)
        backoff = CRAWL_RETRY_BACKOFFS[min(retry_count, len(CRAWL_RETRY_BACKOFFS) - 1)]
        last_failed = c.get("crawl_failed_at")
        if isinstance(last_failed, datetime) and (now - last_failed).total_seconds() < backoff:
            continue  # too soon

        url = c.get("scraped_url")
        if not url:
            continue

        try:
            # Lazy-import worker so this module can also be imported by the
            # backend (which doesn't import worker normally).
            from worker import celery_app
            celery_app.send_task(
                "services.crawler_service.crawl_website_task",
                args=[str(c["_id"]), url, None],
                queue="crawl",
            )
            db.chatbot_configs.update_one(
                {"_id": c["_id"]},
                {
                    "$set": {
                        "crawl_status": "crawling",
                        "crawl_retried_at": now,
                    },
                    "$inc": {"crawl_retry_count": 1},
                },
            )
            enqueued += 1
            logger.info(
                f"retry_failed_crawls: re-queued {c['_id']} (attempt "
                f"{retry_count + 1}/{CRAWL_MAX_RETRIES})"
            )
        except Exception as e:
            logger.warning(f"retry_failed_crawls: send_task failed for {c['_id']}: {e}")

    # Mark exhausted retries as permanent failures so the UI can stop showing
    # a spinner forever.
    try:
        permanent = db.chatbot_configs.update_many(
            {
                "crawl_status": "failed",
                "crawl_retry_count": {"$gte": CRAWL_MAX_RETRIES},
            },
            {"$set": {"crawl_status": "failed_permanent"}},
        )
        if permanent.modified_count:
            logger.info(f"retry_failed_crawls: {permanent.modified_count} chatbots → failed_permanent")
    except Exception as e:
        logger.warning(f"retry_failed_crawls: failed_permanent update failed: {e}")

    return enqueued
