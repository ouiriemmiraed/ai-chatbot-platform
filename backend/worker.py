import os
from celery import Celery
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get Redis URL from env or default to localhost (for local dev without docker networking)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "chatbot_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["services.crawler_service", "services.conversation_classifier", "services.quality_judge", "services.content_gap_detector"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Performance settings
    worker_prefetch_multiplier=1,  # Prevent worker from grabbing too many tasks
    task_acks_late=True,  # ACK after completion (prevents task loss on crash)
    worker_max_tasks_per_child=50,  # Restart worker after 50 tasks (prevents memory leaks)
    task_soft_time_limit=int(os.getenv("CELERY_SOFT_TIME_LIMIT", "5400")),   # 1.5h soft limit (sends SoftTimeLimitExceeded)
    task_time_limit=int(os.getenv("CELERY_HARD_TIME_LIMIT", "10800")),       # 3h hard limit (SIGKILL)
    task_reject_on_worker_lost=True,  # Re-queue task if worker crashes
    # Two-queue topology: heavy crawling jobs go to `crawl` (single high-RAM
    # worker, exclusive Playwright + ML loaded), everything else (alerts,
    # webhooks, future emails) goes to `light` (multi-process, low RAM).
    # See docker-compose.yml — celery_crawl and celery_light services.
    task_default_queue="light",
    task_routes={
        "services.crawler_service.crawl_website_task": {"queue": "crawl"},
        # Future low-cost tasks default to "light" automatically.
    },
)


if __name__ == "__main__":
    celery_app.start()
