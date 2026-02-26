import os
from celery import Celery
from celery.schedules import crontab

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery = Celery("h2n_beat", broker=REDIS_URL)
celery.conf.timezone = "America/Chicago"

celery.conf.beat_schedule = {
    "content-intel-daily": {
        "task": "tasks.content_intel_daily",
        "schedule": crontab(hour=7, minute=15),
    },
    "engagement-queue-daily": {
        "task": "tasks.engagement_queue_daily",
        "schedule": crontab(hour=9, minute=10),
    },
    "creator-discovery": {
        "task": "tasks.creator_discovery_hashtags",
        "schedule": crontab(minute=0, hour="*/6"),
        "args": (200, 4),
    },
    "creator-related-expansion": {
        "task": "tasks.creator_discovery_hashtags",
        "schedule": crontab(minute=30, hour="*/12"),
        "args": (200, 4),
    },
    "creator-intel": {
        "task": "tasks.creator_intel_daily",
        "schedule": crontab(minute=0, hour=3),
    },
}
