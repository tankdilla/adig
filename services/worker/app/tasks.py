import os
import asyncio
import time
from celery_app import celery
from agents.safety import guardrails_ok
from agents.content_intel.pipeline import run_content_intel
# from agents.creator_discovery import run_creator_discovery
# from agents.engagement_queue import build_engagement_queue
from agents.scrape import fetch_page_text
import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

log = structlog.get_logger(__name__)

@celery.task(name="tasks.content_intel_daily")
def content_intel_daily():
    clear_contextvars()

    # Safely bind task id
    task_id = getattr(content_intel_daily.request, "id", None)
    bind_contextvars(task="content_intel_daily", task_id=task_id)

    start = time.time()
    log.info("task_started")

    try:
        result = run_content_intel()
        duration_ms = int((time.time() - start) * 1000)

        log.info(
            "task_finished",
            duration_ms=duration_ms,
            result_summary=result,
        )
        return result

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)

        log.exception(
            "task_failed",
            duration_ms=duration_ms,
            error=str(e),
        )
        raise

# @celery.task(name="tasks.creator_discovery_weekly")
# def creator_discovery_weekly():
#     return run_creator_discovery()

# @celery.task(name="tasks.engagement_queue_daily")
# def engagement_queue_daily():
#     # This generates a queue of suggested comments/targets,
#     # NOT actioning them unless ACTION_MODE=live and guardrails pass.
#     return build_engagement_queue()

@celery.task(name="tasks.engagement_execute")
def engagement_execute():
    if not guardrails_ok():
        return {"status": "blocked_by_safety"}
    # call action layer here only when you explicitly flip live mode
    return {"status": "live_execution_not_implemented"}

@celery.task(name="tasks.scrape_test")
def scrape_test(url: str):
    return asyncio.run(fetch_page_text(url))[:2000]