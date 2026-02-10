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
    "creator-discovery-weekly": {
        "task": "tasks.creator_discovery_weekly",
        "schedule": crontab(day_of_week="sun", hour=16, minute=0),
    },
    # Live execution should be OFF by default; only enable after proving safety
    # "engagement-execute": {
    #     "task": "tasks.engagement_execute",
    #     "schedule": crontab(minute="*/30"),
    # },
}
