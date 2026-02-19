import os
import logging
import structlog
from celery import Celery

from logging_setup import configure_structured_logging
from db_log_handler import DBLogHandler

### Logging setup ###

SERVICE_NAME = os.getenv("SERVICE_NAME", "worker")

configure_structured_logging(SERVICE_NAME)

db_handler = DBLogHandler()
db_handler.setLevel(os.getenv("DB_LOG_LEVEL", "INFO").upper())
logging.getLogger().addHandler(db_handler)

log = structlog.get_logger(__name__)
log.info("worker_startup", service=SERVICE_NAME)

###

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
