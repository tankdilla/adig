import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery = Celery(
    "h2n_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks"],
)

celery.conf.update(
    task_routes={
        "tasks.content_intel_*": {"queue": "intel"},
        "tasks.creator_*": {"queue": "community"},
        "tasks.engagement_*": {"queue": "engagement"},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

celery.conf.update(
    task_default_queue="celery",
    task_track_started=True,
    timezone="UTC",
)
