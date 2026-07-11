"""
Lightweight async scheduler that replaces Celery Beat.

Runs inside the FastAPI process as background tasks — no extra container,
no ML model loading, just MongoDB queries + Celery task dispatching.
Saves ~500 MiB RAM by eliminating the celery_beat container.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class Scheduler:
    """Simple async scheduler with named periodic jobs."""

    def __init__(self):
        self._tasks: list[asyncio.Task] = []
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        logger.info("Scheduler starting…")

        self._tasks.append(asyncio.create_task(self._run_loop(
            name="check-rescrape",
            interval_seconds=15 * 60,  # every 15 min
            func=_check_rescrape_schedules,
        )))
        self._tasks.append(asyncio.create_task(self._run_loop(
            name="check-page-health",
            interval_seconds=7 * 24 * 3600,  # weekly
            func=_check_page_health,
            run_at_hour=2,    # 2:00 AM
            run_at_weekday=0, # Monday
        )))
        # Sprint C: auto-retry of failed crawls. Re-queues `crawl_status:failed`
        # chatbots with exponential backoff until CRAWL_MAX_RETRIES is hit.
        from core.crawl_governance import retry_failed_crawls
        self._tasks.append(asyncio.create_task(self._run_loop(
            name="retry-failed-crawls",
            interval_seconds=5 * 60,  # every 5 min
            func=retry_failed_crawls,
        )))

        # Sprint D: analytics background jobs. We dispatch via Celery
        # (not run inline) so the heavy work runs on the light worker.
        def _enqueue_classifier_backfill():
            from worker import celery_app
            celery_app.send_task(
                "services.conversation_classifier.backfill_classifications",
                args=[200], queue="light",
            )

        def _enqueue_gap_detection():
            from worker import celery_app
            celery_app.send_task(
                "services.content_gap_detector.detect_content_gaps",
                args=[None], queue="light",
            )

        self._tasks.append(asyncio.create_task(self._run_loop(
            name="classifier-backfill",
            interval_seconds=10 * 60,  # every 10 min
            func=_enqueue_classifier_backfill,
        )))
        self._tasks.append(asyncio.create_task(self._run_loop(
            name="content-gap-detection",
            interval_seconds=60 * 60,  # hourly
            func=_enqueue_gap_detection,
        )))

        logger.info("Scheduler started with 5 jobs")

    async def stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Scheduler stopped")

    async def _run_loop(
        self,
        name: str,
        interval_seconds: int,
        func,
        run_at_hour: int | None = None,
        run_at_weekday: int | None = None,
    ):
        """Run *func* periodically. If run_at_hour/weekday are set, wait
        until the next matching slot before the first execution."""
        try:
            # For weekly jobs, sleep until the right day/hour
            if run_at_hour is not None and run_at_weekday is not None:
                await self._sleep_until(run_at_weekday, run_at_hour)

            while self._running:
                try:
                    logger.info(f"[scheduler] Running {name}")
                    await asyncio.get_event_loop().run_in_executor(None, func)
                    logger.info(f"[scheduler] {name} completed")
                except Exception as e:
                    logger.error(f"[scheduler] {name} failed: {e}", exc_info=True)

                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            pass

    @staticmethod
    async def _sleep_until(weekday: int, hour: int):
        """Sleep until the next occurrence of *weekday* at *hour*:00 UTC."""
        now = datetime.utcnow()
        days_ahead = (weekday - now.weekday()) % 7
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
        if target <= now:
            target += timedelta(weeks=1)
        delta = (target - now).total_seconds()
        logger.info(f"[scheduler] Weekly job sleeping {delta/3600:.1f}h until {target}")
        await asyncio.sleep(delta)


# ──────────────────────────────────────────────────────────────────
# Job implementations (run in thread via run_in_executor)
# ──────────────────────────────────────────────────────────────────

def _check_rescrape_schedules():
    """Check all chatbots with rescrape_interval_hours and dispatch crawl tasks if due."""
    from pymongo import MongoClient
    from worker import celery_app

    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "chatbot_db")

    client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    try:
        db = client[db_name]
        now = datetime.utcnow()

        # Smart incremental re-scraping: when SMART_RESCRAPE_ENABLED is on, scheduled
        # re-scrapes run in smart mode (force_rescrape=False) so the engine skips
        # pinned / unchanged pages (ETag, content-hash, intervals). Default off keeps
        # the full re-crawl behaviour. Manual "Re-scraper" crawls are unaffected.
        _smart_enabled = os.getenv("SMART_RESCRAPE_ENABLED", "false").lower() in ("1", "true", "yes", "on")
        _force_rescrape = not _smart_enabled

        query = {
            "rescrape_interval_hours": {"$exists": True, "$ne": None, "$gt": 0},
            "scraped_url": {"$exists": True, "$ne": None},
            "crawl_status": {"$nin": ["crawling"]},
        }

        count = 0
        skipped = 0

        for config in db.chatbot_configs.find(query):
            try:
                chatbot_id = str(config["_id"])
                interval = config.get("rescrape_interval_hours")
                last_scraped = config.get("last_scraped_at") or config.get("crawl_completed_at")

                if not last_scraped:
                    skipped += 1
                    continue

                if now - last_scraped < timedelta(hours=interval):
                    skipped += 1
                    continue

                url = config["scraped_url"]
                max_pages = config.get("max_pages_to_crawl")

                logger.info(
                    f"Triggering auto re-scrape for chatbot {chatbot_id}: "
                    f"URL={url}, interval={interval}h"
                )

                celery_app.send_task(
                    "services.crawler_service.crawl_website_task",
                    args=[chatbot_id, url, max_pages],
                    kwargs={"force_rescrape": _force_rescrape},
                )
                count += 1

            except Exception as e:
                logger.error(f"Error processing chatbot {config.get('_id')}: {e}", exc_info=True)

        logger.info(f"Rescrape check: {count} dispatched, {skipped} skipped")
    finally:
        client.close()


def _check_page_health():
    """Check page health for broken links, 404s, 500s."""
    from pymongo import MongoClient

    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "chatbot_db")

    client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    try:
        db = client[db_name]

        from services.smart_rescrape_service import smart_rescrape_engine
        from services.alert_service import alert_service

        six_days_ago = datetime.utcnow() - timedelta(days=6)
        query = {
            "$or": [
                {"last_health_check": {"$exists": False}},
                {"last_health_check": None},
                {"last_health_check": {"$lt": six_days_ago}},
            ]
        }

        pages = list(db.scraped_pages.find(query).limit(1000))

        checked = broken = recovered = alerts = 0
        logger.info(f"Starting health check for {len(pages)} pages")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        for page in pages:
            try:
                url = page["url"]
                chatbot_id = page["chatbot_id"]
                was_broken = page.get("is_broken", False)

                health = loop.run_until_complete(
                    smart_rescrape_engine.check_page_health(url)
                )
                checked += 1

                update = {
                    "is_broken": health["is_broken"],
                    "http_status": health["status"],
                    "last_health_check": health["checked_at"],
                }

                if health["is_broken"] and not was_broken:
                    update["broken_since"] = health["checked_at"]
                    broken += 1
                    try:
                        loop.run_until_complete(
                            alert_service.send_broken_link_alert(
                                chatbot_id, page, health["status"], health.get("error")
                            )
                        )
                        alerts += 1
                    except Exception as e:
                        logger.error(f"Alert failed for {url}: {e}")
                elif not health["is_broken"] and was_broken:
                    update["broken_since"] = None
                    recovered += 1

                db.scraped_pages.update_one({"_id": page["_id"]}, {"$set": update})
            except Exception as e:
                logger.error(f"Health check error for {page.get('url')}: {e}")

        loop.close()
        logger.info(
            f"Health check done: {checked} checked, {broken} broken, "
            f"{recovered} recovered, {alerts} alerts"
        )
    finally:
        client.close()


# Singleton
scheduler = Scheduler()
