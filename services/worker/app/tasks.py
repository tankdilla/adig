import os
import asyncio
import time
import json
from datetime import datetime, timezone, timedelta, date

from agents.engagement.comments import generate_comment
from agents.engagement.scheduler import schedule_actions

from celery_app import celery
from agents.safety import guardrails_ok
from agents.content_intel.pipeline import run_content_intel

from agents.scrape import fetch_page_text
from agents.broll.pexels import get_broll_for_keywords

from agents.content_intel.shoot_pack import generate_shoot_pack
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from db_models import EngagementAction, EngagementActionType, EngagementStatus
from db_models import PostDraft
from db_models import Creator, OutreachDraft, OutreachCampaign, ApprovalStatus, OutreachStatus, OutreachEvent

from agents.llm import draft

from agents.outreach.discovery import discover_from_hashtags
from agents.outreach.phase1_discovery import discover_phase1, score_candidate
from agents.outreach.personalization import build_personalized_dm
from agents.outreach.fraud_detection import assess_fraud, is_excludable
from agents.outreach.intel_engine import snapshot_creator, update_growth_fields, compute_niche_signals, best_partner_similarity
from agents.graph.builder import ensure_creator, extract_mentions, upsert_edge, build_similarity_edges
from agents.analytics.viral_patterns import build_report

from db_models import CreatorRelationship, CreatorRelationshipStatus, CreatorEdgeType, CreatorPost, ViralPatternReport

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

### Database session maker
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

OUTREACH_SYSTEM = """You write short, friendly influencer outreach DMs for Hello To Natural (H2N), a natural body care + wellness brand.
Rules:
- Human, warm, concise.
- No medical cure claims.
- Respectful, not spammy.
- Include a clear next step.
Return ONLY the message text.
"""

FOLLOWUP_SYSTEM = """Write a short, friendly follow-up DM for an Instagram creator.
Rules:
- One short paragraph.
- No guilt, no pressure.
- Clear CTA: ask if they'd like details.
- No medical claims.
Return ONLY the message text.
"""

CREATOR_SCORE_SYSTEM = """You score influencer fit for Hello To Natural (H2N), a natural body care + wellness brand.
Return JSON ONLY:
{"score": <0-100>, "niche_tags": ["..."], "note": "short reason"}
Consider:
- skincare/body care/wellness alignment
- authenticity (not spammy)
- audience relevance
- brand-safe content
"""

log = structlog.get_logger(__name__)


def _phase1_default_queries() -> list[str]:
    return [
        "natural skincare influencers",
        "herbal wellness creators",
        "christian lifestyle creators",
        "black wellness creators",
    ]


def _append_unique_lines(existing: str | None, additions: list[str]) -> str | None:
    lines = [line.strip() for line in (existing or "").splitlines() if line.strip()]
    for item in additions:
        item = (item or "").strip()
        if item and item not in lines:
            lines.append(item)
    return "\n".join(lines)[:2000] if lines else None

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

@celery.task(name="tasks.build_outreach_batch")
def build_outreach_batch(campaign_id: int, limit: int = 20):
    clear_contextvars()
    task_id = getattr(build_outreach_batch.request, "id", None)
    bind_contextvars(task="build_outreach_batch", task_id=task_id, campaign_id=campaign_id)
    log.info("task_started", limit=limit)

    db = SessionLocal()
    try:
        campaign = db.query(OutreachCampaign).filter(OutreachCampaign.id == campaign_id).first()
        if not campaign:
            log.error("campaign_not_found")
            return {"ok": False, "error": "campaign_not_found"}

        # pick top creators (simple MVP: highest score, not already drafted in campaign)
        existing_creator_ids = {
            x.creator_id for x in db.query(OutreachDraft.creator_id).filter(OutreachDraft.campaign_id == campaign_id).all()
        }

        # Default targeting: micro/mid creators, scored, exclude brands/spam/mega
        creators = (
            db.query(Creator)
            .filter(Creator.score >= 50)
            .filter(Creator.is_brand.is_(False))
            .filter(Creator.is_spam.is_(False))
            .filter(Creator.fraud_score < 70)
            .filter(Creator.outreach_status.notin_(["excluded", "do_not_contact"]))
            .order_by(Creator.score.desc(), Creator.created_at.desc())
            .limit(400)
            .all()
        )

        picked = []
        for c in creators:
            if c.id in existing_creator_ids:
                continue
            # hard excludes
            ex, _reason = is_excludable(c)
            if ex:
                continue
            # Avoid re-contact if relationship exists and already declined/blocked/partnered
            rel = db.query(CreatorRelationship).filter(CreatorRelationship.creator_id == c.id).first()
            if rel and rel.status in [CreatorRelationshipStatus.declined, CreatorRelationshipStatus.blocked, CreatorRelationshipStatus.partnered]:
                continue
            # Prefer 5k-80k if follower estimate exists
            if c.followers_est is not None and not (5_000 <= c.followers_est <= 80_000):
                continue
            picked.append(c)
            if len(picked) >= limit:
                break

        created = 0
        for c in picked:
            # default is deterministic personalized DM; LLM can be layered later
            msg = build_personalized_dm(c, campaign_name=campaign.name)
            if not msg:
                msg = f"Hey @{c.handle}! Thank you for all you share. I’m with Hello To Natural—would you be open to a gifted collab + optional affiliate code if it feels aligned? If yes, I can send quick details."

            od = OutreachDraft(
                creator_id=c.id,
                message=msg,
                offer_type="gifted+affiliate",
                campaign_name=campaign.name,
                campaign_id=campaign_id,
                status=ApprovalStatus.pending,
                outreach_status=OutreachStatus.pending,
                created_at=datetime.utcnow(),
            )
            db.add(od)
            db.flush()  # get id for event log

            # Ensure relationship row exists
            rel = db.query(CreatorRelationship).filter(CreatorRelationship.creator_id == c.id).first()
            if not rel:
                rel = CreatorRelationship(creator_id=c.id, status=CreatorRelationshipStatus.new, updated_at=datetime.utcnow())
                db.add(rel)

            db.add(OutreachEvent(outreach_draft_id=od.id, event_type="generated", note=None, created_at=datetime.utcnow()))
            created += 1

        db.commit()
        log.info("task_finished", created=created)
        return {"ok": True, "created": created, "campaign_id": campaign_id}

    except Exception as e:
        db.rollback()
        log.exception("task_failed", error=str(e))
        raise
    finally:
        db.close()

@celery.task(name="tasks.engagement_queue_daily")
def engagement_queue_daily():
    return build_engagement_queue()

@celery.task(name="tasks.build_engagement_queue")
def build_engagement_queue():
    """
    Builds a controlled queue from 'targets' stored in DB.
    MVP: targets are provided via admin UI as EngagementAction rows with action_type=comment and no proposed_text yet.
    """
    clear_contextvars()
    task_id = getattr(build_engagement_queue.request, "id", None)
    bind_contextvars(task="build_engagement_queue", task_id=task_id)
    log.info("task_started")

    db = SessionLocal()
    try:
        # Find comment actions missing text
        pending = (
            db.query(EngagementAction)
            .filter(EngagementAction.action_type == EngagementActionType.comment)
            .filter(EngagementAction.status == EngagementStatus.pending)
            .filter((EngagementAction.proposed_text == None) | (EngagementAction.proposed_text == ""))  # noqa: E711
            .order_by(EngagementAction.created_at.asc())
            .limit(60)  # cap
            .all()
        )

        if not pending:
            log.info("no_targets")
            return {"ok": True, "generated": 0}

        # Grab recent generated comments to avoid repetition
        recent = (
            db.query(EngagementAction.proposed_text)
            .filter(EngagementAction.action_type == EngagementActionType.comment)
            .filter(EngagementAction.proposed_text != None)  # noqa: E711
            .order_by(EngagementAction.created_at.desc())
            .limit(30)
            .all()
        )
        recent_comments = [r[0] for r in recent if r and r[0]]

        # schedule times (default start now)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        scheduled_times = schedule_actions(len(pending), start_at=now, per_hour=25)

        generated = 0
        failed = 0

        for i, row in enumerate(pending):
            target = {
                "caption": row.target_caption or "",
                "author": row.target_handle or "",
                "url": row.target_url or "",
                "topic_hint": "natural wellness, skincare, herbal living",
            }
            comment = generate_comment(target, recent_comments=recent_comments)
            if not comment:
                row.status = EngagementStatus.failed
                row.notes = "LLM output failed quality rules"
                failed += 1
                continue

            row.proposed_text = comment
            row.scheduled_for = scheduled_times[i]
            # stays pending until admin approves
            generated += 1
            recent_comments.append(comment)

        db.commit()
        log.info("task_finished", generated=generated, failed=failed)
        return {"ok": True, "generated": generated, "failed": failed}

    except Exception as e:
        db.rollback()
        log.exception("task_failed", error=str(e))
        raise
    finally:
        db.close()

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

@celery.task(name="tasks.build_outreach_followups")
def build_outreach_followups(campaign_id: int | None = None, days: int = 3, limit: int = 25):
    clear_contextvars()
    task_id = getattr(build_outreach_followups.request, "id", None)
    bind_contextvars(task="build_outreach_followups", task_id=task_id, campaign_id=campaign_id, days=days, limit=limit)
    log.info("task_started")

    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=int(days))

        q = db.query(OutreachDraft).join(Creator, OutreachDraft.creator_id == Creator.id)

        # Sent, no response, older than cutoff, not too many followups
        q = q.filter(OutreachDraft.outreach_status == OutreachStatus.sent)
        q = q.filter(OutreachDraft.last_response_at.is_(None))
        q = q.filter(OutreachDraft.sent_at.is_not(None))
        q = q.filter(OutreachDraft.sent_at <= cutoff)
        q = q.filter(OutreachDraft.followups_sent < 2)

        if campaign_id:
            q = q.filter(OutreachDraft.campaign_id == campaign_id)

        targets = q.order_by(OutreachDraft.sent_at.asc()).limit(limit).all()

        made = 0
        for d in targets:
            handle = d.creator.handle if d.creator else ""
            prev = (d.message or "").strip()

            prompt = f"""
We previously sent this DM to @{handle}:

{prev}

Write a follow-up DM to send now.
"""

            msg = (draft(prompt.strip(), system=FOLLOWUP_SYSTEM, temperature=0.6) or "").strip()
            if not msg:
                msg = f"Hey @{handle}! Just circling back—would you be open to a gifted collab + optional affiliate code with Hello To Natural? If so I can send quick details. 🙂"

            # Record as an event (this is what you’ll export / copy manually)
            ev = OutreachEvent(
                outreach_draft_id=d.id,
                event_type="followup_generated",
                note=msg,
                created_at=datetime.utcnow(),
            )
            db.add(ev)

            d.followups_sent += 1
            db.add(d)
            made += 1

        db.commit()
        log.info("task_finished", followups_generated=made)
        return {"ok": True, "followups_generated": made}

    except Exception as e:
        db.rollback()
        log.exception("task_failed", error=str(e))
        raise
    finally:
        db.close()

@celery.task(name="tasks.creator_intel_daily")
def creator_intel_daily(limit: int = 300):
    """
    Daily intel refresh:
    - snapshot followers/posts (growth)
    - compute niche signals
    - compute similarity to partners (lexical)
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(Creator)
            .filter(func.coalesce(Creator.is_brand, False).is_(False))
            .filter(func.coalesce(Creator.is_spam, False).is_(False))
            .order_by(
                Creator.last_intel_run_at.asc().nullsfirst(),
                Creator.created_at.desc(),
            )
            .limit(limit)
            .all()
        )

        async def _run():
            for i, c in enumerate(rows, start=1):
                await snapshot_creator(db, c)

                niche = await compute_niche_signals(db, c)
                c.niche_score = niche

                update_growth_fields(db, c)

                # stash similarity into fraud_flags to avoid a new column for now
                sim = best_partner_similarity(db, c)
                ff = c.fraud_flags or {}
                ff["partner_similarity"] = float(sim)
                c.fraud_flags = ff
                c.last_intel_run_at = datetime.utcnow()

                if i % 25 == 0:
                    db.flush()

        asyncio.run(_run())
        db.commit()
        return {"ok": True, "updated": len(rows)}
    except Exception as e:
        db.rollback()
        raise
    finally:
        db.close()

@celery.task(name="tasks.score_creators")
def score_creators(limit: int = 200):
    clear_contextvars()
    task_id = getattr(score_creators.request, "id", None)
    bind_contextvars(task="score_creators", task_id=task_id, limit=limit)
    log.info("task_started")

    db = SessionLocal()
    try:
        creators = db.query(Creator).filter(Creator.score == 0).order_by(Creator.created_at.desc()).limit(int(limit)).all()

        updated = 0
        for c in creators:
            prompt = f"""
Creator:
- handle: @{c.handle}
- platform: {c.platform}
- followers_est: {c.followers_est}
- niche_tags: {c.niche_tags}
- notes: {c.notes}

Score fit for H2N.
"""

            raw = (draft(prompt.strip(), system=CREATOR_SCORE_SYSTEM, temperature=0.4) or "").strip()

            score = None
            tags = None
            note = None

            try:
                obj = json.loads(raw)
                score = int(obj.get("score", 0))
                tags = obj.get("niche_tags") or []
                note = obj.get("note") or ""
            except Exception:
                # fallback heuristic if model returns non-JSON
                score = c.score or 0
                note = "score_creators: model output not JSON"

            score = max(0, min(100, score))

            c.score = score
            if tags:
                # store as comma separated
                c.niche_tags = ", ".join([t.strip() for t in tags if t and t.strip()])[:2000]
            if note:
                # append a short reason without bloating
                existing = (c.notes or "")[:1800]
                c.notes = (existing + ("\n" if existing else "") + f"Score note: {note}")[:2000]

            # fraud / exclude heuristics
            fraud_score, flags = assess_fraud(c)
            c.fraud_score = fraud_score
            c.fraud_flags = flags

            # brand/spam hard signals (conservative)
            handle_l = (c.handle or "").lower()
            if any(x in handle_l for x in ["shop", "store", "boutique", "official", "brand"]):
                c.is_brand = True
            notes_l = (c.notes or "").lower()
            if any(x in notes_l for x in ["dm for promo", "forex", "crypto", "whatsapp", "telegram", "giveaway page"]):
                c.is_spam = True

            db.add(c)
            updated += 1

        db.commit()
        log.info("task_finished", updated=updated)
        return {"ok": True, "updated": updated}

    except Exception as e:
        db.rollback()
        log.exception("task_failed", error=str(e))
        raise
    finally:
        db.close()



@celery.task(name="tasks.creator_discovery_phase1")
def creator_discovery_phase1(limit: int = 100, queries: list[str] | None = None, max_google_results: int = 5):
    """Discover creators from YouTube search plus curated web lists and apply a deterministic score."""
    clear_contextvars()
    task_id = getattr(creator_discovery_phase1.request, "id", None)
    bind_contextvars(task="creator_discovery_phase1", task_id=task_id, limit=limit)
    log.info("task_started", limit=limit, queries=queries, max_google_results=max_google_results)

    db = SessionLocal()
    try:
        normalized_queries = [q.strip() for q in (queries or _phase1_default_queries()) if q and q.strip()]
        candidates = discover_phase1(queries=normalized_queries, max_google_results=max_google_results)

        created = 0
        updated = 0
        skipped = 0

        ranked = []
        for candidate in candidates:
            score, reasons = score_candidate(candidate)
            ranked.append((score, reasons, candidate))

        ranked.sort(key=lambda item: (item[0], item[2].confidence_score, len(item[2].source_platforms)), reverse=True)

        for score, reasons, candidate in ranked:
            if created >= int(limit) and not db.query(Creator).filter(Creator.handle == candidate.handle).first():
                skipped += 1
                continue

            existing = db.query(Creator).filter(Creator.handle == candidate.handle).first()
            tags_csv = ", ".join(sorted(candidate.niche_tags))[:1500] or None
            note_lines = [
                f"Phase 1 discovery score: {score}",
                f"Discovery reasons: {', '.join(reasons)}" if reasons else "Discovery reasons: none",
                f"Discovery sources: {', '.join(sorted(candidate.source_platforms))}" if candidate.source_platforms else "Discovery sources: none",
            ]
            if candidate.emails:
                note_lines.append(f"Emails: {', '.join(sorted(candidate.emails))}")
            if candidate.website_url:
                note_lines.append(f"Website: {candidate.website_url}")
            note_lines.extend(candidate.notes[:4])

            discovery_meta = {
                "phase1_sources": sorted(candidate.source_urls),
                "phase1_source_platforms": sorted(candidate.source_platforms),
                "phase1_reasons": reasons,
                "phase1_confidence": float(candidate.confidence_score or 0.0),
                "phase1_emails": sorted(candidate.emails),
                "phase1_website_url": candidate.website_url,
            }

            if existing:
                existing.score = max(existing.score or 0, score)
                if tags_csv:
                    existing_tags = {t.strip() for t in (existing.niche_tags or '').split(',') if t.strip()}
                    existing_tags |= {t.strip() for t in tags_csv.split(',') if t.strip()}
                    existing.niche_tags = ", ".join(sorted(existing_tags))[:1500]
                if candidate.followers_est is not None:
                    existing.followers_est = max(existing.followers_est or 0, candidate.followers_est)
                ff = existing.fraud_flags or {}
                ff["phase1_sources"] = sorted(set(ff.get("phase1_sources", [])) | set(candidate.source_urls))
                ff["phase1_source_platforms"] = sorted(set(ff.get("phase1_source_platforms", [])) | set(candidate.source_platforms))
                ff["phase1_reasons"] = sorted(set(ff.get("phase1_reasons", [])) | set(reasons))
                ff["phase1_confidence"] = max(float(ff.get("phase1_confidence", 0.0)), float(candidate.confidence_score or 0.0))
                ff["phase1_emails"] = sorted(set(ff.get("phase1_emails", [])) | set(candidate.emails))
                if candidate.website_url and not ff.get("phase1_website_url"):
                    ff["phase1_website_url"] = candidate.website_url
                ff.setdefault("discovery_review_status", "pending")
                existing.fraud_flags = dict(ff)
                flag_modified(existing, "fraud_flags")
                existing.notes = _append_unique_lines(existing.notes, note_lines)
                updated += 1
                continue

            creator = Creator(
                handle=candidate.handle,
                platform="instagram",
                followers_est=candidate.followers_est,
                niche_tags=tags_csv,
                score=score,
                notes="".join(note_lines)[:2000],
                created_at=datetime.utcnow(),
                fraud_flags={**discovery_meta, "discovery_review_status": "pending"},
            )
            fraud_score, flags = assess_fraud(creator)
            creator.fraud_score = fraud_score
            creator.fraud_flags = {**(creator.fraud_flags or {}), **(flags or {})}
            db.add(creator)
            created += 1

        db.commit()
        result = {
            "ok": True,
            "queries": normalized_queries,
            "discovered": len(candidates),
            "created": created,
            "updated": updated,
            "skipped": skipped,
        }
        log.info("task_finished", result=result)
        return result
    except Exception as e:
        db.rollback()
        log.exception("task_failed", error=str(e))
        raise
    finally:
        db.close()

@celery.task(name="tasks.creator_discovery_hashtags")
def creator_discovery_hashtags(limit: int = 200, rotate: int = 4):
    """Discover creators from hashtag pages and insert into creators table."""
    clear_contextvars()
    task_id = getattr(creator_discovery_hashtags.request, "id", None)
    bind_contextvars(task="creator_discovery_hashtags", task_id=task_id)
    log.info("task_started", limit=limit, rotate=rotate)

    db = SessionLocal()
    try:
        result = asyncio.run(discover_from_hashtags(db=db, limit=limit, rotate=rotate))
        db.commit()
        log.info("task_finished", result=result)
        return result
    except Exception as e:
        db.rollback()
        log.exception("task_failed", error=str(e))
        raise
    finally:
        db.close()

@celery.task(name="tasks.creator_graph_update")
def creator_graph_update(limit_creators: int = 200, similarity_top_k: int = 25):
    """Build/refresh creator graph edges: mentions + similarity."""
    clear_contextvars()
    task_id = getattr(creator_graph_update.request, "id", None)
    bind_contextvars(task="creator_graph_update", task_id=task_id)
    log.info("task_started", limit_creators=limit_creators)

    db = SessionLocal()
    try:
        creators = (
            db.query(Creator)
            .order_by(Creator.created_at.desc())
            .limit(limit_creators)
            .all()
        )

        # Similarity edges across this pool (cheap & useful)
        for c in creators:
            build_similarity_edges(db, c, creators, top_k=similarity_top_k)

        # Mention edges (best-effort scraping)
        for c in creators:
            try:
                url = f"https://www.instagram.com/{c.handle}/"
                text = asyncio.run(fetch_page_text(url))
            except Exception:
                continue

            mentions = extract_mentions(text)
            # ignore self
            mentions.discard(c.handle.lower())
            for mh in list(mentions)[:30]:
                mc = ensure_creator(db, mh, platform="instagram")
                upsert_edge(
                    db,
                    source_id=c.id,
                    target_id=mc.id,
                    edge_type=CreatorEdgeType.mention,
                    weight=0.6,
                    metadata={"source": "profile_text"},
                )

        db.commit()
        log.info("task_finished", creators=len(creators))
        return {"ok": True, "creators": len(creators)}
    except Exception as e:
        db.rollback()
        log.exception("task_failed", error=str(e))
        raise
    finally:
        db.close()


@celery.task(name="tasks.viral_patterns_daily")
def viral_patterns_daily(limit_posts: int = 500):
    """Create/update a daily viral pattern report from cached creator_posts."""
    clear_contextvars()
    task_id = getattr(viral_patterns_daily.request, "id", None)
    bind_contextvars(task="viral_patterns_daily", task_id=task_id)
    log.info("task_started", limit_posts=limit_posts)

    db = SessionLocal()
    try:
        rows = (
            db.query(CreatorPost)
            .order_by(CreatorPost.created_at.desc())
            .limit(limit_posts)
            .all()
        )
        posts = [{"extracted": r.extracted or {}} for r in rows]
        report = build_report(posts)
        today = date.today().isoformat()
        existing = (
            db.query(ViralPatternReport)
            .filter(ViralPatternReport.report_date == today)
            .filter(ViralPatternReport.scope == "instagram")
            .first()
        )
        if existing:
            existing.report = report
            db.add(existing)
        else:
            db.add(ViralPatternReport(report_date=today, scope="instagram", report=report, created_at=datetime.utcnow()))
        db.commit()
        log.info("task_finished")
        return {"ok": True, "date": today, "report": report}
    except Exception as e:
        db.rollback()
        log.exception("task_failed", error=str(e))
        raise
    finally:
        db.close()