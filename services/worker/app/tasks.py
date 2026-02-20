import os
import asyncio
import time
import json

from celery_app import celery
from agents.safety import guardrails_ok
from agents.content_intel.pipeline import run_content_intel
# from agents.creator_discovery import run_creator_discovery
# from agents.engagement_queue import build_engagement_queue
from agents.scrape import fetch_page_text
from agents.broll.pexels import get_broll_for_keywords

from agents.content_intel.shoot_pack import generate_shoot_pack
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db_models import PostDraft  # or shared import path you’re using in worker

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

### Database session maker
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

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

### Build shoot pack

@celery.task(name="tasks.build_shoot_pack")
def build_shoot_pack(post_draft_id: int):
    clear_contextvars()
    task_id = getattr(build_shoot_pack.request, "id", None)
    bind_contextvars(task="build_shoot_pack", task_id=task_id, post_draft_id=post_draft_id)
    log.info("task_started")

    db = SessionLocal()
    try:
        pd = db.query(PostDraft).filter(PostDraft.id == post_draft_id).first()
        if not pd:
            log.error("not_found")
            return {"ok": False, "error": "PostDraft not found"}

        pack = generate_shoot_pack(
            hook=pd.hook or "",
            caption=pd.caption or "",
            hashtags=pd.hashtags,
            media_notes=pd.media_notes,
        )

        # store as pretty JSON text
        pd.shoot_pack = json.dumps(pack, indent=2, ensure_ascii=False)
        db.add(pd)
        db.commit()

        log.info("task_finished")
        return {"ok": True, "post_draft_id": post_draft_id}

    except Exception as e:
        db.rollback()
        log.exception("task_failed", error=str(e))
        raise
    finally:
        db.close()

### Build B-roll pack

@celery.task(name="tasks.build_broll_pack")
def build_broll_pack(post_draft_id: int):
    clear_contextvars()
    task_id = getattr(build_broll_pack.request, "id", None)
    bind_contextvars(task="build_broll_pack", task_id=task_id, post_draft_id=post_draft_id)
    log.info("task_started")

    db = SessionLocal()
    try:
        pd = db.query(PostDraft).filter(PostDraft.id == post_draft_id).first()
        if not pd:
            log.error("not_found")
            return {"ok": False, "error": "PostDraft not found"}

        if not pd.shoot_pack:
            return {"ok": False, "error": "Shoot pack not generated yet"}

        pack = json.loads(pd.shoot_pack)
        keywords = pack.get("broll") or pack.get("broll_keywords") or []
        # Normalize to a small list of strings
        keywords = [str(x).strip() for x in keywords if str(x).strip()]
        keywords = keywords[:6]  # keep it tight

        manifest = get_broll_for_keywords(post_draft_id, keywords)

        pd.broll_manifest = json.dumps(manifest, indent=2, ensure_ascii=False)
        pd.broll_dir = manifest.get("out_dir")
        db.add(pd)
        db.commit()

        log.info("task_finished")
        return {"ok": True, "post_draft_id": post_draft_id, "clips": len(manifest["clips"])}

    except Exception as e:
        db.rollback()
        log.exception("task_failed", error=str(e))
        raise
    finally:
        db.close()
