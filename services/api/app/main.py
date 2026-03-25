import os
import logging
import structlog
import csv, io

from celery import Celery
from fastapi import FastAPI, Depends, Header, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Float, func, cast, and_, or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime, date, time, timedelta

from db import Base, engine, get_db
from settings import settings
import json

from auth import sign_session, verify_session, COOKIE_NAME
from pathlib import Path
from db_models import (
    Setting,
    DailyPlan,
    PostDraft,
    EngagementQueueItem,
    OutreachDraft,
    OutreachCampaign,
    OutreachEvent,
    OutreachStatus,
    Creator,
    CreatorEdge,
    CreatorEdgeType,
    CreatorRelationship,
    CreatorPost,
    ViralPatternReport,
    ApprovalStatus,
    ContentType,
    ActionMode,
    AppLog,
    EngagementAction,
    EngagementStatus,
    EngagementActionType
)
### Init app

app = FastAPI(title="H2N Agent Control Plane", version="0.1.0")

### Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

### Logging middleware wire-in ###

from middleware.request_id import RequestIDMiddleware
app.add_middleware(RequestIDMiddleware)

from middleware.access_log import AccessLogMiddleware
app.add_middleware(AccessLogMiddleware)

### Logging init ###

from logging_setup import configure_structured_logging
from db_log_handler import DBLogHandler

SERVICE_NAME = os.getenv("SERVICE_NAME", "api")

configure_structured_logging(SERVICE_NAME)

# Attach DB handler to root logger for admin visibility
db_handler = DBLogHandler()
db_handler.setLevel(os.getenv("DB_LOG_LEVEL", "INFO").upper())
logging.getLogger().addHandler(db_handler)

log = structlog.get_logger(__name__)
log.info("api_startup", service=SERVICE_NAME)

###

Base.metadata.create_all(bind=engine)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CONTENT_INTEL_TASK = os.getenv("CONTENT_INTEL_TASK", "tasks.content_intel_daily")
BUILD_SHOOT_PACK_TASK = os.getenv("BUILD_SHOOT_PACK_TASK", "tasks.build_shoot_pack")
BUILD_BROLL_PACK_TASK = os.getenv("BUILD_BROLL_PACK_TASK", "tasks.build_broll_pack")
BUILD_ENGAGEMENT_QUEUE_TASK = os.getenv("BUILD_ENGAGEMENT_QUEUE_TASK", "tasks.build_engagement_queue")
BUILD_OUTREACH_BATCH_TASK = os.getenv("BUILD_OUTREACH_BATCH_TASK", "tasks.build_outreach_batch")
BUILD_OUTREACH_FOLLOWUPS_TASK = os.getenv("BUILD_OUTREACH_FOLLOWUPS_TASK", "tasks.build_outreach_followups")
SCORE_CREATORS_TASK = os.getenv("SCORE_CREATORS_TASK", "tasks.score_creators")
CREATOR_DISCOVERY_TASK = os.getenv("CREATOR_DISCOVERY_TASK", "tasks.creator_discovery_phase1")
CREATOR_GRAPH_TASK = os.getenv("CREATOR_GRAPH_TASK", "tasks.creator_graph_update")
VIRAL_PATTERNS_TASK = os.getenv("VIRAL_PATTERNS_TASK", "tasks.viral_patterns_daily")

celery_client = Celery("h2n_api_client", broker=REDIS_URL, backend=REDIS_URL)

def now_utc():
    return datetime.utcnow()

def _append_unique_lines(existing: str | None, additions: list[str]) -> str | None:
    lines = [line.strip() for line in (existing or "").splitlines() if line.strip()]
    for item in additions:
        item = (item or "").strip()
        if item and item not in lines:
            lines.append(item)
    return "\n".join(lines)[:2000] if lines else None


def _apply_creator_review_decision(creator: Creator, decision: str, reason: str, user: str):
    reason = (reason or "").strip()
    flags = creator.fraud_flags or {}
    flags["discovery_review_status"] = decision
    flags["discovery_reviewed_by"] = user
    flags["discovery_reviewed_at"] = datetime.utcnow().isoformat()
    if reason:
        flags["discovery_review_reason"] = reason[:280]
    elif decision == "pending":
        flags.pop("discovery_review_reason", None)
    creator.fraud_flags = dict(flags)
    flag_modified(creator, "fraud_flags")

    if decision == "approved":
        creator.outreach_status = "eligible"
        if creator.outreach_exclude_reason and creator.outreach_exclude_reason.lower().startswith("discovery review"):
            creator.outreach_exclude_reason = None
    elif decision == "rejected":
        creator.outreach_status = "excluded"
        creator.outreach_exclude_reason = f"Discovery review: {(reason or 'not a fit')[:240]}"

    note_line = f"Discovery review: {decision} by {user}"
    if reason:
        note_line += f" ({reason[:180]})"
    creator.notes = _append_unique_lines(creator.notes, [note_line])

def _creator_review_meta(creator: Creator) -> dict:
    flags = creator.fraud_flags or {}
    reasons = list(flags.get("phase1_reasons") or [])
    source_urls = list(flags.get("phase1_sources") or [])
    source_platforms = list(flags.get("phase1_source_platforms") or [])
    emails = list(flags.get("phase1_emails") or [])
    website_url = flags.get("phase1_website_url")
    confidence = flags.get("phase1_confidence")
    review_status = flags.get("discovery_review_status", "pending")
    reviewed_by = flags.get("discovery_reviewed_by")
    reviewed_at = flags.get("discovery_reviewed_at")
    review_reason = flags.get("discovery_review_reason")

    notes_lines = [line.strip() for line in (creator.notes or "").splitlines() if line.strip()]
    score_line = next((line for line in notes_lines if line.lower().startswith("phase 1 discovery score:")), None)
    discovery_score = None
    if score_line:
        try:
            discovery_score = int(score_line.split(":", 1)[1].strip())
        except Exception:
            discovery_score = None
    if not reasons:
        reason_line = next((line for line in notes_lines if line.lower().startswith("discovery reasons:")), None)
        if reason_line:
            reasons = [part.strip() for part in reason_line.split(":", 1)[1].split(",") if part.strip() and part.strip().lower() != "none"]
    if not source_platforms:
        platform_line = next((line for line in notes_lines if line.lower().startswith("discovery sources:")), None)
        if platform_line:
            source_platforms = [part.strip() for part in platform_line.split(":", 1)[1].split(",") if part.strip() and part.strip().lower() != "none"]

    return {
        "discovery_score": discovery_score if discovery_score is not None else creator.score,
        "reasons": reasons,
        "source_urls": source_urls,
        "source_platforms": source_platforms,
        "emails": emails,
        "website_url": website_url,
        "confidence": confidence,
        "review_status": review_status,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "review_reason": review_reason,
    }

def _creator_has_discovery_meta(creator: Creator) -> bool:
    meta = _creator_review_meta(creator)
    return bool(meta["source_urls"] or meta["source_platforms"] or meta["reasons"] or "Phase 1 discovery score:" in (creator.notes or ""))

def _draft_workflow_summary(drafts: list[OutreachDraft]) -> dict:
    summary = {
        "total": len(drafts),
        "pending": 0,
        "approved": 0,
        "sent": 0,
        "replied": 0,
        "booked": 0,
        "declined": 0,
        "ghosted": 0,
    }
    for draft in drafts:
        status_value = getattr(getattr(draft, "outreach_status", None), "value", getattr(draft, "outreach_status", None))
        if status_value in summary:
            summary[status_value] += 1
        elif getattr(getattr(draft, "status", None), "value", getattr(draft, "status", None)) == "pending":
            summary["pending"] += 1
    return summary


def _creator_contact_channels(creator: Creator) -> list[dict]:
    meta = _creator_review_meta(creator)
    channels = [{
        "label": "Instagram",
        "value": f"@{creator.handle}",
        "url": f"https://www.instagram.com/{creator.handle}",
        "kind": "instagram",
    }]
    for email in meta.get("emails") or []:
        email = str(email).strip()
        if email:
            channels.append({"label": "Email", "value": email, "url": f"mailto:{email}", "kind": "email"})
    website_url = (meta.get("website_url") or "").strip()
    if website_url:
        channels.append({"label": "Website", "value": website_url, "url": website_url, "kind": "website"})
    deduped = []
    seen = set()
    for item in channels:
        key = (item["kind"], item["value"].lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _creator_next_outreach_step(creator: Creator, drafts: list[OutreachDraft]) -> str:
    if (creator.outreach_status or "").strip().lower() == "do_not_contact":
        return "Do not contact. Keep for reference only."
    if (creator.outreach_status or "").strip().lower() == "excluded":
        return "Excluded from outreach. Review fit before creating another draft."

    latest = drafts[0] if drafts else None
    if latest is None:
        channels = _creator_contact_channels(creator)
        preferred = next((c for c in channels if c["kind"] == "email"), None) or next((c for c in channels if c["kind"] == "instagram"), None)
        if preferred and preferred["kind"] == "email":
            return f"Create a first-touch email draft to {preferred['value']}."
        return "Create a first-touch Instagram DM draft."

    latest_status = getattr(getattr(latest, "outreach_status", None), "value", getattr(latest, "outreach_status", None))
    latest_approval = getattr(getattr(latest, "status", None), "value", getattr(latest, "status", None))
    if latest_approval == "pending":
        return "Review and approve the latest outreach draft before sending."
    if latest_status == "approved":
        return "Latest draft is approved. Send it and record the thread link."
    if latest_status == "sent":
        return "Waiting on response. Generate a follow-up if enough time has passed."
    if latest_status == "replied":
        return "A reply is recorded. Update relationship notes and choose booked, declined, or next follow-up."
    if latest_status == "booked":
        return "Creator is booked. Move details into campaign and fulfillment workflow."
    if latest_status == "declined":
        return "Creator declined. Capture learnings before trying a different offer later."
    if latest_status == "ghosted":
        return "No response recorded. Try one final follow-up or mark do not contact."
    return "Review the outreach history and decide the next manual step."


def _build_followup_message(creator: Creator, draft: OutreachDraft, tone: str = "gentle") -> str:
    tone = (tone or "gentle").strip().lower()
    if tone not in {"gentle", "warm", "direct"}:
        tone = "gentle"
    offer = (draft.offer_type or "a gifted collaboration").strip()
    greeting = f"Hey @{creator.handle}!"
    openers = {
        "gentle": "Just wanted to gently follow up on my earlier note.",
        "warm": "Wanted to circle back because I really think this could be a lovely fit.",
        "direct": "Following up on my earlier note to see if you're open to chatting.",
    }
    closer = {
        "gentle": "Happy to share details if you're interested. No pressure either way 💛",
        "warm": "If you'd like, I can send the details and product options. No pressure at all 💛",
        "direct": "If this sounds interesting, I can send the details and next steps.",
    }
    signature = "Mary, Hello To Natural"
    return "\n".join([greeting, "", openers[tone], f"We'd still love to explore {offer} with you.", closer[tone], "", signature])


def _coerce_due_at(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                parsed = datetime.combine(parsed.date(), time(hour=9))
            return parsed
        except ValueError:
            continue
    return None


def _preferred_outreach_channel(creator: Creator) -> str:
    channels = _creator_contact_channels(creator)
    if any(channel["kind"] == "email" for channel in channels):
        return "email"
    return "instagram_dm"


def _build_first_touch_message(creator: Creator, offer_type: str, send_channel: str) -> str:
    offer = (offer_type or "a gifted collaboration").strip()
    if (send_channel or "").strip().lower() == "email":
        return "\n".join([
            f"Hi @{creator.handle},",
            "",
            "I’m Mary from Hello To Natural. I came across your content and really enjoyed your voice and overall brand fit.",
            f"I’d love to see if you might be open to {offer}.",
            "If that sounds interesting, I can send over the details, product options, and timeline.",
            "",
            "Warmly,",
            "Mary",
            "Hello To Natural",
        ])
    return "\n".join([
        f"Hey @{creator.handle}! I’m Mary from Hello To Natural 🌿",
        "",
        "I love your content and I think your audience would really connect with our brand.",
        f"Would you be open to {offer}?",
        "If so, I can share details + shipping info. No pressure either way 💛",
        "",
        "— Mary, Hello To Natural",
    ])


def _build_sequence_followup_message(creator: Creator, draft: OutreachDraft, step: str) -> str:
    offer = (draft.offer_type or "a gifted collaboration").strip()
    channel = (draft.send_channel or "instagram_dm").strip().lower()
    if step == "follow_up_2":
        if channel == "email":
            return "\n".join([
                f"Hi @{creator.handle},",
                "",
                "Wanted to send one last quick follow up on my earlier note.",
                f"We would still be glad to explore {offer} with you if the timing feels right.",
                "If now is not a fit, no worries at all. I just wanted to close the loop respectfully.",
                "",
                "Warmly,",
                "Mary",
                "Hello To Natural",
            ])
        return "\n".join([
            f"Hey @{creator.handle}! Just sending one last quick follow up.",
            "",
            f"We would still love to explore {offer} with you if the timing feels right.",
            "If now is not a fit, no worries at all. I just wanted to close the loop respectfully 💛",
            "",
            "Mary, Hello To Natural",
        ])
    tone = "warm" if step == "follow_up_1" else "gentle"
    return _build_followup_message(creator, draft, tone=tone)


def _sequence_step_definitions(start_at: datetime) -> list[dict]:
    base = start_at.replace(second=0, microsecond=0)
    return [
        {"step": "first_touch", "label": "First Touch", "offset_days": 0, "due_at": base},
        {"step": "follow_up_1", "label": "Follow Up 1", "offset_days": 4, "due_at": base + timedelta(days=4)},
        {"step": "follow_up_2", "label": "Follow Up 2", "offset_days": 9, "due_at": base + timedelta(days=9)},
    ]


def _sequence_step_label(step: str | None) -> str:
    mapping = {
        "first_touch": "First Touch",
        "follow_up_1": "Follow Up 1",
        "follow_up_2": "Follow Up 2",
    }
    return mapping.get((step or "").strip(), (step or "Unplanned").replace("_", " ").title())


def _creator_sequence_rows(creator: Creator, drafts: list[OutreachDraft]) -> list[dict]:
    sequence_drafts = [d for d in drafts if (d.sequence_step or "").strip()]
    by_step = {d.sequence_step: d for d in sorted(sequence_drafts, key=lambda item: item.created_at or datetime.min, reverse=True)}
    first_due = next((d.due_at for d in sequence_drafts if d.due_at), None) or now_utc().replace(hour=9, minute=0, second=0, microsecond=0)
    rows = []
    for meta in _sequence_step_definitions(first_due):
        draft = by_step.get(meta["step"])
        rows.append({
            **meta,
            "draft": draft,
            "status": getattr(getattr(draft, "outreach_status", None), "value", getattr(draft, "outreach_status", None)) if draft else "not_created",
            "approval": getattr(getattr(draft, "status", None), "value", getattr(draft, "status", None)) if draft else "not_created",
            "due_at": (draft.due_at if draft and draft.due_at else meta["due_at"]),
            "is_due": bool((draft.due_at if draft and draft.due_at else meta["due_at"]) and (draft.due_at if draft and draft.due_at else meta["due_at"]) <= now_utc()),
        })
    return rows


def _outreach_inbox_bucket(draft: OutreachDraft, reference: datetime | None = None) -> str:
    reference = reference or now_utc()
    if not draft.due_at:
        return "undated"
    due_date = draft.due_at.date()
    today = reference.date()
    if due_date < today:
        return "overdue"
    if due_date == today:
        return "today"
    if due_date <= (today + timedelta(days=7)):
        return "next_7_days"
    return "later"


def _normalize_outreach_inbox_filters(
    bucket: str = "actionable",
    channel: str = "any",
    approval_state: str = "any",
    page: int = 1,
):
    bucket = (bucket or "actionable").strip().lower()
    if bucket not in {"actionable", "overdue", "today", "next_7_days", "later", "undated", "all"}:
        bucket = "actionable"

    channel = (channel or "any").strip().lower()
    if channel not in {"any", "email", "instagram_dm"}:
        channel = "any"

    approval_state = (approval_state or "any").strip().lower()
    if approval_state not in {"any", "pending", "approved"}:
        approval_state = "any"

    try:
        page = max(int(page), 1)
    except Exception:
        page = 1

    return {"bucket": bucket, "channel": channel, "approval_state": approval_state, "page": page}


def _outreach_inbox_querystring(filters: dict, *, include_page: bool = True) -> str:
    parts = []
    if filters.get("bucket") and filters["bucket"] != "actionable":
        parts.append(f"bucket={filters['bucket']}")
    if filters.get("channel") and filters["channel"] != "any":
        parts.append(f"channel={filters['channel']}")
    if filters.get("approval_state") and filters["approval_state"] != "any":
        parts.append(f"approval_state={filters['approval_state']}")
    if include_page:
        parts.append(f"page={filters.get('page', 1)}")
    return "&".join(parts)


def _outreach_redirect_target(return_to: str | None, creator_id: int) -> str:
    value = (return_to or "").strip()
    if value.startswith("/admin/outreach/inbox") or value.startswith("/admin/creators/"):
        return value
    return f"/admin/creators/{creator_id}"


def _open_outreach_drafts(db: Session) -> list[OutreachDraft]:
    return (
        db.query(OutreachDraft)
        .filter(OutreachDraft.sequence_step.isnot(None))
        .filter(OutreachDraft.outreach_status.in_([OutreachStatus.pending, OutreachStatus.approved]))
        .order_by(OutreachDraft.due_at.asc().nullsfirst(), OutreachDraft.created_at.asc())
        .all()
    )


def _outreach_inbox_rows(db: Session, filters: dict) -> tuple[list[dict], dict]:
    reference = now_utc()
    rows = []
    counts = {"actionable": 0, "overdue": 0, "today": 0, "next_7_days": 0, "later": 0, "undated": 0, "all": 0}
    for draft in _open_outreach_drafts(db):
        bucket = _outreach_inbox_bucket(draft, reference=reference)
        counts["all"] += 1
        counts[bucket] += 1
        if bucket in {"overdue", "today"}:
            counts["actionable"] += 1

        approval_value = getattr(getattr(draft, "status", None), "value", getattr(draft, "status", None)) or "pending"
        channel_value = (draft.send_channel or "instagram_dm").strip().lower()

        if filters["bucket"] != "all":
            if filters["bucket"] == "actionable":
                if bucket not in {"overdue", "today"}:
                    continue
            elif bucket != filters["bucket"]:
                continue
        if filters["channel"] != "any" and channel_value != filters["channel"]:
            continue
        if filters["approval_state"] != "any" and approval_value != filters["approval_state"]:
            continue

        creator = draft.creator or db.get(Creator, draft.creator_id)
        meta = _creator_review_meta(creator) if creator else {}
        step_label = _sequence_step_label(draft.sequence_step)
        next_action = "Approve draft" if approval_value == "pending" else ("Send email" if channel_value == "email" else "Send DM")
        rows.append({
            "draft": draft,
            "creator": creator,
            "bucket": bucket,
            "step_label": step_label,
            "approval_value": approval_value,
            "channel_label": "Email" if channel_value == "email" else "Instagram DM",
            "next_action": next_action,
            "review_status": meta.get("review_status", "pending"),
            "discovery_score": meta.get("discovery_score") if meta else None,
            "primary_email": (meta.get("emails") or [None])[0] if meta else None,
        })

    if filters["bucket"] == "actionable":
        bucket_rank = {"overdue": 0, "today": 1, "next_7_days": 2, "later": 3, "undated": 4}
        rows.sort(key=lambda item: (bucket_rank.get(item["bucket"], 9), item["draft"].due_at or datetime.max, item["creator"].handle if item["creator"] else ""))
    else:
        rows.sort(key=lambda item: (item["draft"].due_at or datetime.max, item["creator"].handle if item["creator"] else ""))
    return rows, counts


def _create_outreach_sequence_plan(
    db: Session,
    creator: Creator,
    start_at: datetime,
    offer_type: str = "",
    campaign_name: str = "",
    campaign_id: str = "",
    send_channel: str = "",
) -> list[OutreachDraft]:
    channel = (send_channel or "").strip() or _preferred_outreach_channel(creator)
    offer = (offer_type or "").strip() or "a gifted collaboration"
    campaign_name = (campaign_name or "").strip() or None
    sequence_name = "standard_3_touch"
    existing = {
        draft.sequence_step: draft
        for draft in db.query(OutreachDraft)
        .filter(OutreachDraft.creator_id == creator.id, OutreachDraft.sequence_name == sequence_name)
        .all()
        if (draft.sequence_step or "").strip()
    }
    created = []
    for item in _sequence_step_definitions(start_at):
        if item["step"] in existing:
            continue
        if item["step"] == "first_touch":
            message = _build_first_touch_message(creator, offer, channel)
        else:
            anchor = existing.get("first_touch") or (created[0] if created else None)
            if anchor is None:
                anchor = OutreachDraft(creator_id=creator.id, message=_build_first_touch_message(creator, offer, channel), offer_type=offer, send_channel=channel)
            message = _build_sequence_followup_message(creator, anchor, item["step"])
        draft = OutreachDraft(
            creator_id=creator.id,
            message=message,
            offer_type=offer,
            campaign_name=campaign_name,
            send_channel=channel,
            sequence_name=sequence_name,
            sequence_step=item["step"],
            due_at=item["due_at"],
            status=ApprovalStatus.pending,
            outreach_status=OutreachStatus.pending,
        )
        if (campaign_id or "").strip():
            try:
                draft.campaign_id = int(campaign_id)
            except ValueError:
                pass
        db.add(draft)
        db.flush()
        existing[item["step"]] = draft
        created.append(draft)
    return created


def _normalize_review_filters(
    status: str = "pending",
    page: int = 1,
    min_score: int | None = None,
    source_type: str = "any",
    email_state: str = "any",
    confidence_band: str = "any",
    sort_by: str = "newest",
) -> dict:
    status = (status or "pending").strip().lower()
    if status not in {"pending", "approved", "rejected", "all"}:
        status = "pending"

    source_type = (source_type or "any").strip().lower()
    if source_type not in {"any", "youtube", "web", "podcast", "blog", "newsletter"}:
        source_type = "any"

    email_state = (email_state or "any").strip().lower()
    if email_state not in {"any", "yes", "no"}:
        email_state = "any"

    confidence_band = (confidence_band or "any").strip().lower()
    if confidence_band not in {"any", "high", "medium", "low", "unknown"}:
        confidence_band = "any"

    sort_by = (sort_by or "newest").strip().lower()
    if sort_by not in {"newest", "score_desc", "score_asc", "confidence_desc", "handle_asc"}:
        sort_by = "newest"

    try:
        page = max(int(page), 1)
    except Exception:
        page = 1

    if min_score in (None, ""):
        normalized_min_score = None
    else:
        try:
            normalized_min_score = max(int(min_score), 0)
        except Exception:
            normalized_min_score = None

    return {
        "status": status,
        "page": page,
        "min_score": normalized_min_score,
        "source_type": source_type,
        "email_state": email_state,
        "confidence_band": confidence_band,
        "sort_by": sort_by,
    }


def _review_confidence_band(confidence) -> str:
    try:
        value = float(confidence)
    except Exception:
        return "unknown"
    if value >= 0.8:
        return "high"
    if value >= 0.55:
        return "medium"
    return "low"


def _review_queue_querystring(filters: dict, *, include_page: bool = True) -> str:
    parts = [f"status={filters['status']}"]
    if include_page:
        parts.append(f"page={filters['page']}")
    if filters.get("min_score") is not None:
        parts.append(f"min_score={filters['min_score']}")
    for key in ("source_type", "email_state", "confidence_band", "sort_by"):
        value = filters.get(key)
        if value and value != "any" and not (key == "sort_by" and value == "newest"):
            parts.append(f"{key}={value}")
    return "&".join(parts)

def get_session_user(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = verify_session(token)
    if not payload:
        return None
    return payload.get("u")

def require_admin(request: Request, x_admin_token: str | None = Header(default=None)):
    # 1) Header token (great for curl / scripts)
    if x_admin_token == settings.admin_token:
        return "header_admin"

    # 2) Signed cookie session (great for phone/browser)
    user = get_session_user(request)
    if user:
        return user

    raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/health")
def health():
    return {"ok": True}

# ---- Login / Logout ----

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None},
    )

@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    token: str = Form(...),
):
    if token != settings.admin_token:
        return templates.TemplateResponse(
            request,
            "login.html", 
            {"error": "Invalid token."}
        )

    session = sign_session(username.strip()[:80] or "admin")
    resp = RedirectResponse(url="/admin", status_code=303)
    # 7 days
    resp.set_cookie(
        key=COOKIE_NAME,
        value=session,
        httponly=True,
        samesite="lax",
        secure=False,  # set True if you put this behind HTTPS later
        max_age=60 * 60 * 24 * 7,
    )
    return resp

@app.post("/logout")
def logout(request: Request):
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp

# ---- Admin page ----

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    # Friendly behavior: send to admin if logged in, else login
    user = get_session_user(request)
    if user:
        return RedirectResponse("/admin", status_code=303)
    return RedirectResponse("/login", status_code=303)

@app.get("/admin", response_class=HTMLResponse)
def admin_home(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    pending_posts = db.query(PostDraft).filter(PostDraft.status == ApprovalStatus.pending).count()
    pending_eng = db.query(EngagementQueueItem).filter(EngagementQueueItem.status == ApprovalStatus.pending).count()
    pending_out = db.query(OutreachDraft).filter(OutreachDraft.status == ApprovalStatus.pending).count()
    settings_map = {s.key: s.value for s in db.query(Setting).all()}

    return templates.TemplateResponse(
        request,
        "admin.html", 
        {"pending_posts": pending_posts,
        "pending_eng": pending_eng,
        "pending_out": pending_out,
        "settings": settings_map,
        "user": user,
        }
    )


# --- Creators (Discovery + Scoring + Graph) ---

@app.get("/admin/creators", response_class=HTMLResponse)
def admin_creators(
    request: Request,
    min_score: int = 50,
    max_fraud: int = 70,
    page: int = 1,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    page = max(page, 1)
    page_size = 200
    offset = (page - 1) * page_size

    query = (
        db.query(Creator)
        .filter(func.coalesce(Creator.score, 0) >= min_score)
        .filter(func.coalesce(Creator.is_brand, False).is_(False))
        .filter(func.coalesce(Creator.is_spam, False).is_(False))
        .filter(func.coalesce(Creator.fraud_score, 0) < max_fraud)
        # .order_by(Creator.score.desc().nullslast(), Creator.created_at.desc())
        .order_by(Creator.created_at.desc(), Creator.score.desc().nullslast())
    )

    # total = query.count()
    total = query.order_by(None).count()
    creators = query.offset(offset).limit(page_size).all()
    creator_review_meta = {c.id: _creator_review_meta(c) for c in creators}

    return templates.TemplateResponse(
        request,
        "creators.html",
        {
            "user": user,
            "creators": creators,
            "total": total,
            "page": page,
            "page_size": page_size,
            "min_score": min_score,
            "max_fraud": max_fraud,
            "creator_review_meta": creator_review_meta,
        },
    )

@app.post("/admin/creators/import", response_class=HTMLResponse)
def admin_import_creators(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    raw = file.file.read()
    text = raw.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))

    created, updated, skipped = 0, 0, 0

    for row in reader:
        h = (row.get("handle") or "").strip().lstrip("@").lower()
        if not h:
            skipped += 1
            continue

        platform = (row.get("platform") or "instagram").strip().lower()
        notes = (row.get("notes") or "").strip()
        source = (row.get("source") or "h2n_top_followers").strip()

        existing = db.query(Creator).filter(Creator.handle == h).first()
        if existing:
            # light update only
            if notes:
                existing.notes = (existing.notes or "")
                if notes not in existing.notes:
                    existing.notes = (existing.notes + "\n" + notes).strip()
            ff = existing.fraud_flags or {}
            ff.setdefault("sources", [])
            if source and source not in ff["sources"]:
                ff["sources"].append(source)
            existing.fraud_flags = dict(ff)
            updated += 1
            continue

        c = Creator(
            handle=h,
            platform=platform,
            notes=notes or f"Imported from {source}",
            created_at=datetime.utcnow(),
            fraud_flags={"sources": [source]},
        )
        db.add(c)
        created += 1

    db.commit()

    min_score = 50
    max_fraud = 70

    query = (
        db.query(Creator)
        .filter(func.coalesce(Creator.score, 0) >= min_score)
        .filter(func.coalesce(Creator.is_brand, False).is_(False))
        .filter(func.coalesce(Creator.is_spam, False).is_(False))
        .filter(func.coalesce(Creator.fraud_score, 0) < max_fraud)
        .order_by(Creator.created_at.desc(), Creator.score.desc().nullslast())
    )

    # total = query.count()
    page = 1
    page_size = 200
    offset = (page - 1) * page_size
    total = query.order_by(None).count()
    creators = query.offset(offset).limit(page_size).all()

    return templates.TemplateResponse(
        request,
        "creators.html",
        {
            "user": user,
            "creators": creators,
            "total": total,
            "page": page,
            "page_size": page_size,
            "min_score": min_score,
            "max_fraud": max_fraud,
        }
    )

@app.get("/admin/creators/review", response_class=HTMLResponse)
def admin_creators_review_queue(
    request: Request,
    status: str = "pending",
    page: int = 1,
    min_score: int | None = None,
    source_type: str = "any",
    email_state: str = "any",
    confidence_band: str = "any",
    sort_by: str = "newest",
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    filters = _normalize_review_filters(status, page, min_score, source_type, email_state, confidence_band, sort_by)
    page_size = 50

    creators = db.query(Creator).order_by(Creator.created_at.desc(), Creator.score.desc().nullslast()).limit(1000).all()
    discovery_rows = []
    for creator in creators:
        if not _creator_has_discovery_meta(creator):
            continue
        meta = _creator_review_meta(creator)
        discovery_score = meta["discovery_score"] or 0
        row_confidence_band = _review_confidence_band(meta["confidence"])
        combined_platforms = {str(item).strip().lower() for item in (meta["source_platforms"] or []) if str(item).strip()}

        if filters["status"] != "all" and meta["review_status"] != filters["status"]:
            continue
        if filters["min_score"] is not None and discovery_score < filters["min_score"]:
            continue
        if filters["source_type"] != "any" and filters["source_type"] not in combined_platforms:
            continue
        if filters["email_state"] == "yes" and not meta["emails"]:
            continue
        if filters["email_state"] == "no" and meta["emails"]:
            continue
        if filters["confidence_band"] != "any" and row_confidence_band != filters["confidence_band"]:
            continue
        meta["confidence_band"] = row_confidence_band
        discovery_rows.append({"creator": creator, "meta": meta})

    if filters["sort_by"] == "score_desc":
        discovery_rows.sort(key=lambda row: ((row["meta"].get("discovery_score") or 0), row["creator"].created_at or datetime.min), reverse=True)
    elif filters["sort_by"] == "score_asc":
        discovery_rows.sort(key=lambda row: ((row["meta"].get("discovery_score") or 0), row["creator"].handle.lower()))
    elif filters["sort_by"] == "confidence_desc":
        discovery_rows.sort(key=lambda row: ((row["meta"].get("confidence") or -1), (row["meta"].get("discovery_score") or 0), row["creator"].created_at or datetime.min), reverse=True)
    elif filters["sort_by"] == "handle_asc":
        discovery_rows.sort(key=lambda row: row["creator"].handle.lower())
    else:
        discovery_rows.sort(key=lambda row: ((row["creator"].created_at or datetime.min), (row["meta"].get("discovery_score") or 0)), reverse=True)

    total = len(discovery_rows)
    offset = (filters["page"] - 1) * page_size
    discovery_rows = discovery_rows[offset:offset + page_size]

    return templates.TemplateResponse(
        request,
        "creator_review_queue.html",
        {
            "user": user,
            "rows": discovery_rows,
            "status": filters["status"],
            "page": filters["page"],
            "page_size": page_size,
            "total": total,
            "filters": filters,
            "queue_query": _review_queue_querystring(filters),
            "queue_query_no_page": _review_queue_querystring(filters, include_page=False),
        },
    )


@app.post("/admin/creators/{creator_id}/review")
def admin_creator_review_action(
    creator_id: int,
    request: Request,
    decision: str = Form(...),
    reason: str = Form(""),
    status: str = Form("pending"),
    page: int = Form(1),
    min_score: int | None = Form(None),
    source_type: str = Form("any"),
    email_state: str = Form("any"),
    confidence_band: str = Form("any"),
    sort_by: str = Form("newest"),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    creator = db.get(Creator, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    decision = (decision or "").strip().lower()
    if decision not in {"approved", "rejected", "pending"}:
        raise HTTPException(status_code=400, detail="Invalid review decision")

    _apply_creator_review_decision(creator, decision, reason, user)

    db.add(creator)
    db.commit()
    filters = _normalize_review_filters(status, page, min_score, source_type, email_state, confidence_band, sort_by)
    return RedirectResponse(url=f"/admin/creators/review?{_review_queue_querystring(filters)}", status_code=303)


@app.post("/admin/creators/review/bulk")
def admin_creator_bulk_review_action(
    request: Request,
    creator_ids: list[int] = Form(...),
    decision: str = Form(...),
    reason: str = Form(""),
    status: str = Form("pending"),
    page: int = Form(1),
    min_score: int | None = Form(None),
    source_type: str = Form("any"),
    email_state: str = Form("any"),
    confidence_band: str = Form("any"),
    sort_by: str = Form("newest"),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    decision = (decision or "").strip().lower()
    if decision not in {"approved", "rejected", "pending"}:
        raise HTTPException(status_code=400, detail="Invalid review decision")

    creators = db.query(Creator).filter(Creator.id.in_(creator_ids)).all()
    if not creators:
        raise HTTPException(status_code=400, detail="No creators selected")

    for creator in creators:
        _apply_creator_review_decision(creator, decision, reason, user)
        db.add(creator)

    db.commit()
    filters = _normalize_review_filters(status, page, min_score, source_type, email_state, confidence_band, sort_by)
    return RedirectResponse(url=f"/admin/creators/review?{_review_queue_querystring(filters)}", status_code=303)


@app.get("/admin/outreach/inbox", response_class=HTMLResponse)
def admin_outreach_inbox(
    request: Request,
    bucket: str = "actionable",
    channel: str = "any",
    approval_state: str = "any",
    page: int = 1,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    filters = _normalize_outreach_inbox_filters(bucket, channel, approval_state, page)
    page_size = 50
    all_rows, counts = _outreach_inbox_rows(db, filters)
    total = len(all_rows)
    offset = (filters["page"] - 1) * page_size
    rows = all_rows[offset: offset + page_size]
    base_qs = _outreach_inbox_querystring(filters, include_page=False)

    return templates.TemplateResponse(
        request,
        "outreach_inbox.html",
        {
            "user": user,
            "rows": rows,
            "total": total,
            "counts": counts,
            "filters": filters,
            "page": filters["page"],
            "page_size": page_size,
            "base_querystring": base_qs,
        },
    )


@app.get("/admin/creators/{creator_id}", response_class=HTMLResponse)
def admin_creator_profile(
    creator_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_admin),  # or _: None = Depends(require_admin) if you're using header auth
):
    creator = db.get(Creator, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    # Relationship (optional table)
    rel = (
        db.query(CreatorRelationship)
        .filter(CreatorRelationship.creator_id == creator_id)
        .first()
        if "CreatorRelationship" in globals()
        else None
    )

    # Recent outreach drafts + events (optional)
    drafts = []
    events = []
    try:
        drafts = (
            db.query(OutreachDraft)
            .filter(OutreachDraft.creator_id == creator_id)
            .order_by(OutreachDraft.created_at.desc())
            .limit(20)
            .all()
        )
    except Exception:
        drafts = []

    try:
        if drafts:
            draft_ids = [d.id for d in drafts]
            events = (
                db.query(OutreachEvent)
                .filter(OutreachEvent.outreach_draft_id.in_(draft_ids))
                .order_by(OutreachEvent.created_at.desc())
                .limit(50)
                .all()
            )
    except Exception:
        events = []

    # Neighbor edges (optional)
    edges = []
    try:
        edges = (
            db.query(CreatorEdge)
            .filter(
                or_(
                    CreatorEdge.source_creator_id == creator_id,
                    CreatorEdge.target_creator_id == creator_id,
                )
            )
            .order_by(CreatorEdge.weight.desc())
            .limit(50)
            .all()
        )
    except Exception:
        edges = []

    # Recent posts (optional)
    posts = []
    try:
        posts = (
            db.query(CreatorPost)
            .filter(CreatorPost.creator_id == creator_id)
            .order_by(CreatorPost.posted_at.desc().nullslast(), CreatorPost.created_at.desc())
            .limit(20)
            .all()
        )
    except Exception:
        posts = []

    campaigns = db.query(OutreachCampaign).order_by(OutreachCampaign.created_at.desc()).limit(50).all()
    review_meta = _creator_review_meta(creator)
    contact_channels = _creator_contact_channels(creator)
    workflow_summary = _draft_workflow_summary(drafts)
    latest_draft = drafts[0] if drafts else None
    next_step = _creator_next_outreach_step(creator, drafts)
    sequence_rows = _creator_sequence_rows(creator, drafts)
    default_sequence_start = now_utc().replace(hour=9, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")

    return templates.TemplateResponse(
        request,
        "creator_profile.html",
        {
            "creator": creator,
            "relationship": rel,
            "drafts": drafts,
            "events": events,
            "edges": edges,
            "posts": posts,
            "campaigns": campaigns,
            "review_meta": review_meta,
            "contact_channels": contact_channels,
            "workflow_summary": workflow_summary,
            "latest_draft": latest_draft,
            "next_step": next_step,
            "sequence_rows": sequence_rows,
            "default_sequence_start": default_sequence_start,
        },
    )

# @app.post("/admin/creators/{creator_id}/outreach_status")
# def admin_set_creator_outreach_status(
#     creator_id: int,
#     status: str = Form(...),   # eligible | excluded | do_not_contact
#     reason: str = Form(""),
#     db: Session = Depends(get_db),
#     user=Depends(require_admin),
# ):
#     c = db.get(Creator, creator_id)
#     if not c:
#         raise HTTPException(status_code=404, detail="Creator not found")

#     status = (status or "").strip().lower()
#     if status not in {"eligible", "excluded", "do_not_contact"}:
#         raise HTTPException(status_code=400, detail="Invalid status")

#     c.outreach_status = status
#     c.outreach_exclude_reason = (reason or "").strip()[:2000] or None
#     db.add(c)
#     db.commit()

#     return RedirectResponse(url=f"/admin/creators/{creator_id}", status_code=303)

@app.post("/admin/creators/{creator_id}/outreach_drafts")
def admin_create_outreach_draft(
    creator_id: int,
    message: str = Form(...),
    offer_type: str = Form(""),
    campaign_name: str = Form(""),
    campaign_id: str = Form(""),
    send_channel: str = Form("instagram_dm"),
    sequence_step: str = Form(""),
    due_at: str = Form(""),
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    creator = db.get(Creator, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    msg = (message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message is required")

    draft = OutreachDraft(
        creator_id=creator_id,
        message=msg,
        offer_type=(offer_type or "").strip() or None,
        campaign_name=(campaign_name or "").strip() or None,
        send_channel=(send_channel or "instagram_dm").strip(),
        sequence_step=(sequence_step or "").strip() or None,
        due_at=_coerce_due_at(due_at),
    )

    # optional FK
    if (campaign_id or "").strip():
        try:
            draft.campaign_id = int(campaign_id)
        except ValueError:
            pass

    db.add(draft)
    db.commit()

    return RedirectResponse(url=f"/admin/creators/{creator_id}", status_code=303)

@app.post("/admin/creators/{creator_id}/outreach_drafts/template")
def admin_create_outreach_draft_template(
    creator_id: int,
    offer_type: str = Form(""),
    campaign_name: str = Form(""),
    send_channel: str = Form(""),
    due_at: str = Form(""),
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    creator = db.get(Creator, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    offer = (offer_type or "").strip() or "a gifted product + feature"
    camp = (campaign_name or "").strip()

    intro = "Hey! I’m Mary from Hello To Natural 🌿"
    line1 = f"I love your content and I think your audience would really connect with our brand."
    line2 = f"Would you be open to {offer}?"
    line3 = "If so, I can share details + shipping info. No pressure either way 💛"
    close = "— Mary, Hello To Natural"

    msg = "\n".join([intro, "", line1, line2, line3, "", close])

    draft = OutreachDraft(
        creator_id=creator_id,
        message=msg,
        offer_type=(offer_type or "").strip() or None,
        campaign_name=camp or None,
        send_channel="instagram_dm",
    )
    db.add(draft)
    db.commit()

    return RedirectResponse(url=f"/admin/creators/{creator_id}", status_code=303)

@app.post("/admin/creators/{creator_id}/outreach_sequence")
def admin_create_outreach_sequence(
    creator_id: int,
    start_at: str = Form(""),
    offer_type: str = Form(""),
    campaign_name: str = Form(""),
    campaign_id: str = Form(""),
    send_channel: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    creator = db.get(Creator, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    parsed_start = _coerce_due_at(start_at) or now_utc().replace(hour=9, minute=0, second=0, microsecond=0)
    _create_outreach_sequence_plan(
        db=db,
        creator=creator,
        start_at=parsed_start,
        offer_type=offer_type,
        campaign_name=campaign_name,
        campaign_id=campaign_id,
        send_channel=send_channel,
    )
    db.commit()
    return RedirectResponse(url=f"/admin/creators/{creator_id}", status_code=303)


@app.post("/admin/outreach_drafts/{draft_id}/update")
def admin_update_outreach_draft(
    draft_id: int,
    message: str = Form(...),
    offer_type: str = Form(""),
    campaign_name: str = Form(""),
    send_channel: str = Form("instagram_dm"),
    thread_url: str = Form(""),
    sequence_step: str = Form(""),
    due_at: str = Form(""),
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    d = db.get(OutreachDraft, draft_id)
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")

    msg = (message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message is required")

    d.message = msg
    d.offer_type = (offer_type or "").strip() or None
    d.campaign_name = (campaign_name or "").strip() or None
    d.send_channel = (send_channel or "instagram_dm").strip() or "instagram_dm"
    d.thread_url = (thread_url or "").strip() or d.thread_url
    d.sequence_step = (sequence_step or "").strip() or None
    d.due_at = _coerce_due_at(due_at)
    db.add(d)
    db.commit()

    return RedirectResponse(url=_outreach_redirect_target(return_to, d.creator_id), status_code=303)

@app.post("/admin/outreach_drafts/{draft_id}/followup")
def admin_create_outreach_followup_from_creator(
    draft_id: int,
    tone: str = Form("gentle"),
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    d = db.get(OutreachDraft, draft_id)
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")

    creator = db.get(Creator, d.creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    inferred_step = "follow_up_1" if (d.sequence_step or "") == "first_touch" else "follow_up_2" if (d.sequence_step or "") == "follow_up_1" else None
    followup = OutreachDraft(
        creator_id=d.creator_id,
        message=_build_followup_message(creator, d, tone=tone),
        offer_type=d.offer_type,
        campaign_name=d.campaign_name,
        campaign_id=d.campaign_id,
        send_channel=d.send_channel,
        status=ApprovalStatus.pending,
        outreach_status=OutreachStatus.pending,
        sequence_name=d.sequence_name,
        sequence_step=inferred_step,
        due_at=(d.due_at + timedelta(days=4) if d.due_at else None),
    )
    db.add(followup)
    if d.outreach_status == OutreachStatus.sent:
        d.followups_sent = (d.followups_sent or 0) + 1
        db.add(d)
    db.commit()

    return RedirectResponse(url=_outreach_redirect_target(return_to, d.creator_id), status_code=303)

@app.post("/admin/outreach_drafts/{draft_id}/stage")
def admin_set_outreach_stage_from_creator(
    draft_id: int,
    stage: str = Form(...),
    note: str = Form(""),
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    d = db.get(OutreachDraft, draft_id)
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")

    stage = (stage or "").strip().lower()
    note = (note or "").strip() or None
    actor = getattr(user, "email", None) or getattr(user, "sub", None) or str(user) or "admin"

    if stage == "pending":
        d.status = ApprovalStatus.pending
        d.outreach_status = OutreachStatus.pending
        d.approved_by = None
        d.approved_at = None
    elif stage == "approved":
        d.status = ApprovalStatus.approved
        d.approved_by = actor
        d.approved_at = datetime.utcnow()
        d.outreach_status = OutreachStatus.approved
    elif stage == "sent":
        d.status = ApprovalStatus.approved
        d.outreach_status = OutreachStatus.sent
        d.sent_at = datetime.utcnow()
        d.sent_by = actor
        if note and (note.startswith("http://") or note.startswith("https://")):
            d.thread_url = note
    elif stage in {"replied", "booked", "declined", "ghosted"}:
        d.last_response_at = datetime.utcnow()
        d.last_response_text = note
        d.outreach_status = OutreachStatus(stage)
    else:
        raise HTTPException(status_code=400, detail="Invalid outreach stage")

    db.add(d)
    db.flush()
    db.add(OutreachEvent(outreach_draft_id=d.id, event_type=f"profile:{stage}", note=note, created_at=datetime.utcnow()))
    db.commit()

    return RedirectResponse(url=_outreach_redirect_target(return_to, d.creator_id), status_code=303)

@app.post("/admin/outreach_drafts/{draft_id}/approve")
def admin_approve_outreach_draft(
    draft_id: int,
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    d = db.get(OutreachDraft, draft_id)
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")

    d.status = ApprovalStatus.approved
    d.approved_by = getattr(user, "email", None) or getattr(user, "sub", None) or "admin"
    d.approved_at = datetime.utcnow()
    db.add(d)
    db.commit()

    return RedirectResponse(url=_outreach_redirect_target(return_to, d.creator_id), status_code=303)


@app.post("/admin/outreach_drafts/{draft_id}/unapprove")
def admin_unapprove_outreach_draft(
    draft_id: int,
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    d = db.get(OutreachDraft, draft_id)
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")

    d.status = ApprovalStatus.pending
    d.approved_by = None
    d.approved_at = None
    db.add(d)
    db.commit()

    return RedirectResponse(url=_outreach_redirect_target(return_to, d.creator_id), status_code=303)


@app.post("/admin/outreach_drafts/{draft_id}/mark_sent")
def admin_mark_outreach_sent(
    draft_id: int,
    thread_url: str = Form(""),
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    d = db.get(OutreachDraft, draft_id)
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")

    d.outreach_status = OutreachStatus.sent
    d.sent_at = datetime.utcnow()
    d.sent_by = getattr(user, "email", None) or getattr(user, "sub", None) or "admin"
    d.thread_url = (thread_url or "").strip() or None

    db.add(d)
    db.commit()

    return RedirectResponse(url=_outreach_redirect_target(return_to, d.creator_id), status_code=303)


@app.post("/admin/outreach_drafts/{draft_id}/record_reply")
def admin_record_outreach_reply(
    draft_id: int,
    last_response_text: str = Form(""),
    return_to: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    d = db.get(OutreachDraft, draft_id)
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")

    txt = (last_response_text or "").strip()
    if txt:
        d.last_response_text = txt
        d.last_response_at = datetime.utcnow()
        d.outreach_status = OutreachStatus.replied

    db.add(d)
    db.commit()

    return RedirectResponse(url=_outreach_redirect_target(return_to, d.creator_id), status_code=303)

@app.post("/admin/creators/{creator_id}/relationship")
def admin_set_creator_relationship(
    creator_id: int,
    status: str = Form(...),  # new/contacted/replied/partnered/declined/blocked
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user=Depends(require_admin),
):
    c = db.get(Creator, creator_id)
    if not c:
        raise HTTPException(status_code=404, detail="Creator not found")

    rel = db.query(CreatorRelationship).filter(CreatorRelationship.creator_id == creator_id).first()
    if not rel:
        rel = CreatorRelationship(creator_id=creator_id)

    rel.status = status
    rel.notes = (notes or "").strip()[:4000] or None
    rel.updated_at = datetime.utcnow()

    db.add(rel)
    db.commit()

    return RedirectResponse(url=f"/admin/creators/{creator_id}", status_code=303)

@app.post("/admin/creators/discover")
def admin_creators_discover(
    request: Request,
    limit: int = Form(100),
    rotate: int = Form(4),
    queries: str = Form("natural skincare influencers\nherbal wellness creators\nchristian lifestyle creators\nblack wellness creators"),
    max_google_results: int = Form(5),
    user: str = Depends(require_admin),
):
    normalized_queries = [line.strip() for line in (queries or "").splitlines() if line.strip()]
    kwargs = {"limit": int(limit)}
    if normalized_queries:
        kwargs["queries"] = normalized_queries
    if "phase1" in CREATOR_DISCOVERY_TASK:
        kwargs["max_google_results"] = int(max_google_results)
    else:
        kwargs["rotate"] = int(rotate)
    celery_client.send_task(CREATOR_DISCOVERY_TASK, kwargs=kwargs)
    return RedirectResponse(url="/admin/creators", status_code=303)

### Set creator outreach status
@app.post("/admin/creators/{creator_id}/outreach_status")
def set_creator_outreach_status(
    creator_id: int,
    status: str = Form(...),  # eligible | excluded | do_not_contact
    reason: str = Form(""),
    return_to: str = Form("/admin/creators"),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    c = db.get(Creator, creator_id)
    if not c:
        raise HTTPException(404, "Creator not found")

    status = status.strip().lower()
    if status not in {"eligible", "excluded", "do_not_contact"}:
        raise HTTPException(400, "Invalid status")

    c.outreach_status = status
    c.outreach_exclude_reason = (reason or "").strip()[:2000] or None
    db.add(c)
    db.commit()

    # return {"id": c.id, "handle": c.handle, "outreach_status": c.outreach_status}
    return RedirectResponse(url="/admin/creators", status_code=303)

@app.post("/admin/creators/score")
def admin_creators_score(
    request: Request,
    limit: int = Form(200),
    user: str = Depends(require_admin),
):
    celery_client.send_task(SCORE_CREATORS_TASK, kwargs={"limit": int(limit)})
    return RedirectResponse(url="/admin?msg=Scoring+creators...+refresh+in+30-90+seconds", status_code=303)


@app.post("/admin/creators/graph")
def admin_creators_graph(
    request: Request,
    limit_creators: int = Form(200),
    similarity_top_k: int = Form(25),
    user: str = Depends(require_admin),
):
    celery_client.send_task(
        CREATOR_GRAPH_TASK,
        kwargs={"limit_creators": int(limit_creators), "similarity_top_k": int(similarity_top_k)},
    )
    return RedirectResponse(url="/admin/creators", status_code=303)


@app.get("/admin/graph", response_class=HTMLResponse)
def admin_graph(
    request: Request,
    handle: str | None = None,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    creator = None
    neighbors = []

    if handle:
        h = handle.lstrip("@").strip().lower()
        creator = db.query(Creator).filter(Creator.handle == h).first()
        if creator:
            edges = (
                db.query(CreatorEdge)
                .filter(CreatorEdge.source_creator_id == creator.id)
                .order_by(CreatorEdge.weight.desc())
                .limit(75)
                .all()
            )
            # Load neighbor creators
            ids = [e.target_creator_id for e in edges]
            by_id = {c.id: c for c in db.query(Creator).filter(Creator.id.in_(ids)).all()} if ids else {}
            for e in edges:
                neighbors.append({
                    "edge": e,
                    "creator": by_id.get(e.target_creator_id),
                })

    return templates.TemplateResponse(
        request,
        "graph.html",
        {"user": user, "handle": handle or "", "creator": creator, "neighbors": neighbors},
    )


@app.get("/admin/patterns", response_class=HTMLResponse)
def admin_patterns(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    latest = db.query(ViralPatternReport).order_by(ViralPatternReport.id.desc()).first()
    return templates.TemplateResponse(
        request,
        "patterns.html",
        {"user": user, "latest": latest},
    )


@app.post("/admin/patterns/run")
def admin_patterns_run(
    request: Request,
    limit_posts: int = Form(500),
    user: str = Depends(require_admin),
):
    celery_client.send_task(VIRAL_PATTERNS_TASK, kwargs={"limit_posts": int(limit_posts)})
    return RedirectResponse(url="/admin/patterns", status_code=303)

# --- Admin Logs ---

@app.get("/admin/logs", response_class=HTMLResponse)
def admin_logs(
    request: Request,
    level: str | None = None,
    service: str | None = None,
    q: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    page = max(page, 1)
    page_size = 200
    offset = (page - 1) * page_size

    query = db.query(AppLog).order_by(AppLog.id.desc())

    if level:
        query = query.filter(AppLog.level == level.upper())
    if service:
        query = query.filter(AppLog.service == service)
    if q:
        query = query.filter(AppLog.message.ilike(f"%{q}%"))

    total = query.count()
    logs = query.offset(offset).limit(page_size).all()

    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "user": user,
            "logs": logs,
            "level": level,
            "service": service,
            "q": q,
            "page": page,
            "has_prev": page > 1,
            "has_next": offset + page_size < total,
        },
    )

# --- Admin UI: Posts ---

@app.get("/admin/posts", response_class=HTMLResponse)
def admin_posts(
    request: Request,
    view: str = "pending",
    q: str | None = None,
    status: ApprovalStatus = ApprovalStatus.pending,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    query = db.query(PostDraft)

    # quick views
    today = date.today()
    start = datetime.combine(today, time.min)
    end = start + timedelta(days=1)

    if view == "generated_today":
        query = query.filter(PostDraft.created_at >= start, PostDraft.created_at < end)
    elif view == "scheduled_today":
        query = query.filter(PostDraft.scheduled_for >= start, PostDraft.scheduled_for < end)
    elif view == "pending":
        query = query.filter(PostDraft.status == ApprovalStatus.pending)

    # status filter (works with the buttons ?status=pending/approved/rejected)
    if status:
        query = query.filter(PostDraft.status == status)

    if view != "all":
        query = query.filter(PostDraft.status == status)

    # optional text search
    if q:
        like = f"%{q}%"
        query = query.filter(or_(PostDraft.hook.ilike(like), PostDraft.caption.ilike(like)))

    posts = query.order_by(PostDraft.created_at.desc()).limit(200).all()

    return templates.TemplateResponse(
        request,
        "posts.html",
        {
            "items": posts,
            "view": view,
            "q": q or "",
            "status": status.value,
            "user": user,
        },
    )

# --- Admin UI: Engagement ---

@app.get("/admin/engagement", response_class=HTMLResponse)
def admin_engagement(
    request: Request,
    view: str = "pending",
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    q = db.query(EngagementAction).filter(EngagementAction.platform == "instagram")

    if view == "pending":
        q = q.filter(EngagementAction.status == EngagementStatus.pending)
    elif view == "approved":
        q = q.filter(EngagementAction.status == EngagementStatus.approved)
    elif view == "executed":
        q = q.filter(EngagementAction.status == EngagementStatus.executed)
    elif view == "failed":
        q = q.filter(EngagementAction.status == EngagementStatus.failed)

    items = q.order_by(EngagementAction.created_at.desc()).limit(250).all()

    return templates.TemplateResponse(
        request,
        "engagement.html",
        {"items": items, "view": view, "user": user},
    )

@app.post("/admin/engagement/targets")
def add_engagement_targets(
    request: Request,
    raw: str = Form(...),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    added = 0

    for ln in lines:
        parts = [p.strip() for p in ln.split("|")]
        url = parts[0]
        author = parts[1] if len(parts) > 1 else None
        caption = parts[2] if len(parts) > 2 else None

        row = EngagementAction(
            platform="instagram",
            target_url=url,
            target_handle=author,
            target_caption=caption,
            action_type=EngagementActionType.comment,
            status=EngagementStatus.pending,
        )
        db.add(row)
        added += 1

    db.commit()
    return RedirectResponse(url="/admin/engagement?view=pending", status_code=303)

@app.post("/admin/engagement/generate")
def generate_engagement_queue(
    user: str = Depends(require_admin),
):
    celery_client.send_task(BUILD_ENGAGEMENT_QUEUE_TASK, args=[])
    return RedirectResponse(url="/admin/engagement?view=pending", status_code=303)

### Approve engagement
@app.post("/engagement/{action_id}/approve")
def approve_engagement_action(
    action_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    a = db.query(EngagementAction).filter(EngagementAction.id == action_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Not found")

    a.status = EngagementStatus.approved
    a.approved_by = user
    a.approved_at = datetime.utcnow()
    db.add(a)
    db.commit()
    return RedirectResponse(url="/admin/engagement?view=pending", status_code=303)

### Skip engagement
@app.post("/engagement/{action_id}/skip")
def skip_engagement_action(
    action_id: int,
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    a = db.query(EngagementAction).filter(EngagementAction.id == action_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Not found")

    a.status = EngagementStatus.skipped
    a.notes = reason or "skipped"
    db.add(a)
    db.commit()
    return RedirectResponse(url="/admin/engagement?view=pending", status_code=303)

### Mark executed
@app.post("/engagement/{action_id}/executed")
def mark_engagement_executed(
    action_id: int,
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    a = db.query(EngagementAction).filter(EngagementAction.id == action_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Not found")

    a.status = EngagementStatus.executed
    a.executed_at = datetime.utcnow()
    if note:
        a.notes = note
    db.add(a)
    db.commit()
    return RedirectResponse(url="/admin/engagement?view=approved", status_code=303)

@app.get("/admin/intel", response_class=HTMLResponse)
def admin_intel(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    # Fastest growing (7d)
    fastest = (
        db.query(Creator)
        .filter(Creator.growth_7d.isnot(None))
        .order_by(Creator.growth_7d.desc())
        .limit(50)
        .all()
    )

    # Most niche-relevant
    niche = (
        db.query(Creator)
        .filter(Creator.niche_score.isnot(None))
        .order_by(Creator.niche_score.desc())
        .limit(50)
        .all()
    )

    # Similar to best partners (stored in fraud_flags.partner_similarity)
    # Works even if you don't add a new column.
    similar = (
        db.query(Creator)
        .order_by(
            func.coalesce(
                cast(Creator.fraud_flags["partner_similarity"].astext, Float),
                0.0
            ).desc()
        )
        .limit(50)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "intel.html",
        {"fastest": fastest, "niche": niche, "similar": similar},
    )

# --- Admin UI: Outreach ---

### List/review drafts
@app.get("/admin/outreach", response_class=HTMLResponse)
def admin_outreach(
    request: Request,
    campaign_id: int | None = None,
    view: str = "pending",
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    campaigns = db.query(OutreachCampaign).order_by(OutreachCampaign.created_at.desc()).limit(50).all()

    q = db.query(OutreachDraft)
    if campaign_id:
        q = q.filter(OutreachDraft.campaign_id == campaign_id)

    if view == "pending":
        q = q.filter(OutreachDraft.status == ApprovalStatus.pending)
    elif view == "approved":
        q = q.filter(OutreachDraft.status == ApprovalStatus.approved)
    elif view == "sent":
        q = q.filter(OutreachDraft.outreach_status == OutreachStatus.sent)
    elif view == "replied":
        q = q.filter(OutreachDraft.outreach_status == OutreachStatus.replied)
    elif view == "booked":
        q = q.filter(OutreachDraft.outreach_status == OutreachStatus.booked)

    drafts = q.order_by(OutreachDraft.created_at.desc()).limit(200).all()

    draft_ids = [d.id for d in drafts]
    events = []
    if draft_ids:
        events = (
            db.query(OutreachEvent)
            .filter(OutreachEvent.outreach_draft_id.in_(draft_ids))
            .filter(OutreachEvent.event_type == "followup_generated")
            .order_by(OutreachEvent.created_at.desc())
            .all()
        )

    latest_followup = {}
    for e in events:
        if e.outreach_draft_id not in latest_followup:
            latest_followup[e.outreach_draft_id] = e.note

    return templates.TemplateResponse(
        request,
        "outreach.html",
        {
            "user": user,
            "campaigns": campaigns,
            "campaign_id": campaign_id,
            "view": view,
            "drafts": drafts,
            "latest_followup": latest_followup,
        },
    )

### Create outreach campaign
@app.post("/admin/outreach/campaigns")
def create_outreach_campaign(
    name: str = Form(...),
    goal_outreaches: int = Form(20),
    goal_collabs: int = Form(5),
    notes: str = Form(None),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    c = OutreachCampaign(
        name=name,
        goal_outreaches=goal_outreaches,
        goal_collabs=goal_collabs,
        notes=notes,
        created_at=datetime.utcnow(),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return RedirectResponse(url=f"/admin/outreach?campaign_id={c.id}", status_code=303)

### Generate outreach drafts
@app.post("/admin/outreach/generate")
def admin_outreach_generate(
    campaign_id: int = Form(...),
    limit: int = Form(20),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    celery_client.send_task(BUILD_OUTREACH_BATCH_TASK, args=[campaign_id], kwargs={"limit": limit})
    return RedirectResponse(url=f"/admin/outreach?campaign_id={campaign_id}", status_code=303)

### Export outreach csv
@app.get("/admin/outreach/export.csv")
def export_outreach_csv(
    request: Request,
    campaign_id: int | None = None,
    view: str = "approved",  # approved/sent/replied/booked/all
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    q = db.query(OutreachDraft).join(Creator, OutreachDraft.creator_id == Creator.id)

    if campaign_id:
        q = q.filter(OutreachDraft.campaign_id == campaign_id)

    if view == "approved":
        q = q.filter(OutreachDraft.status == ApprovalStatus.approved).filter(OutreachDraft.outreach_status == OutreachStatus.approved)
    elif view == "sent":
        q = q.filter(OutreachDraft.outreach_status == OutreachStatus.sent)
    elif view == "replied":
        q = q.filter(OutreachDraft.outreach_status == OutreachStatus.replied)
    elif view == "booked":
        q = q.filter(OutreachDraft.outreach_status == OutreachStatus.booked)
    elif view == "pending":
        q = q.filter(OutreachDraft.status == ApprovalStatus.pending)

    rows = q.order_by(OutreachDraft.created_at.desc()).limit(2000).all()

    # CSV
    import csv
    from io import StringIO

    buf = StringIO()
    w = csv.writer(buf)
    w.writerow([
        "draft_id", "creator_handle", "platform", "campaign", "approval_status",
        "outreach_status", "message", "sent_at", "thread_url", "last_response_at", "last_response_text"
    ])

    for d in rows:
        handle = d.creator.handle if d.creator else ""
        platform = d.creator.platform if d.creator else "instagram"
        w.writerow([
            d.id,
            handle,
            platform,
            d.campaign_name or "",
            d.status.value,
            d.outreach_status.value if d.outreach_status else "",
            (d.message or "").replace("\n", " ").strip(),
            d.sent_at.isoformat() if d.sent_at else "",
            d.thread_url or "",
            d.last_response_at.isoformat() if d.last_response_at else "",
            (d.last_response_text or "").replace("\n", " ").strip(),
        ])

    csv_text = buf.getvalue()
    filename = f"outreach_{(campaign_id or 'all')}_{view}.csv"

    return PlainTextResponse(
        csv_text,
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )

### Generate outreach followups
@app.post("/admin/outreach/followups")
def admin_generate_followups(
    campaign_id: int = Form(None),
    days: int = Form(3),
    limit: int = Form(25),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    celery_client.send_task(
        BUILD_OUTREACH_FOLLOWUPS_TASK,
        args=[],
        kwargs={"campaign_id": campaign_id, "days": days, "limit": limit},
    )
    if campaign_id:
        return RedirectResponse(url=f"/admin/outreach?campaign_id={campaign_id}&view=sent", status_code=303)
    return RedirectResponse(url="/admin/outreach?view=sent", status_code=303)

### Approve outreach
@app.post("/outreach/{draft_id}/approve")
def approve_outreach(
    draft_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    d = db.query(OutreachDraft).filter(OutreachDraft.id == draft_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Not found")

    d.status = ApprovalStatus.approved
    d.approved_by = user
    d.approved_at = datetime.utcnow()
    d.outreach_status = OutreachStatus.approved
    db.add(d)
    db.flush()
    db.add(OutreachEvent(outreach_draft_id=d.id, event_type="approved", note=None, created_at=datetime.utcnow()))
    db.commit()
    return RedirectResponse(url="/admin/outreach?view=pending", status_code=303)

### Mark outreach sent
@app.post("/outreach/{draft_id}/sent")
def mark_outreach_sent(
    draft_id: int,
    sent_by: str = Form(None),
    thread_url: str = Form(None),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    d = db.query(OutreachDraft).filter(OutreachDraft.id == draft_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Not found")

    d.outreach_status = OutreachStatus.sent
    d.sent_at = datetime.utcnow()
    d.sent_by = sent_by or user
    if thread_url:
        d.thread_url = thread_url

    db.add(d)
    db.flush()
    db.add(OutreachEvent(outreach_draft_id=d.id, event_type="sent", note=thread_url, created_at=datetime.utcnow()))
    db.commit()
    return RedirectResponse(url="/admin/outreach?view=approved", status_code=303)

### Record outreach response
@app.post("/outreach/{draft_id}/response")
def record_outreach_response(
    draft_id: int,
    status: str = Form(...),   # replied/booked/declined/ghosted
    response_text: str = Form(None),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    d = db.query(OutreachDraft).filter(OutreachDraft.id == draft_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Not found")

    d.last_response_at = datetime.utcnow()
    d.last_response_text = response_text
    try:
        d.outreach_status = OutreachStatus(status)
    except Exception:
        d.outreach_status = OutreachStatus.replied

    db.add(d)
    db.flush()
    db.add(OutreachEvent(outreach_draft_id=d.id, event_type=f"response:{d.outreach_status.value}", note=response_text, created_at=datetime.utcnow()))
    db.commit()
    return RedirectResponse(url="/admin/outreach?view=sent", status_code=303)

@app.post("/admin/generate-today")
def generate_today(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    # Simple click-spam guard: 5 minute cooldown
    key = "CONTENT_INTEL_LAST_REQUESTED_AT"
    last = db.get(Setting, key)

    # from datetime import datetime, timedelta
    now = datetime.utcnow()

    if last:
        try:
            last_dt = datetime.fromisoformat(last.value)
            if now - last_dt < timedelta(minutes=5):
                # Redirect back with message
                return RedirectResponse(url="/admin?msg=Please+wait+a+few+minutes+before+generating+again", status_code=303)
        except Exception:
            pass

    # record request time
    if not last:
        last = Setting(key=key, value=now.isoformat())
    else:
        last.value = now.isoformat()
    last.updated_at = now
    db.add(last)
    db.commit()

    # enqueue Celery task
    celery_client.send_task(CONTENT_INTEL_TASK)

    return RedirectResponse(url="/admin?msg=Generating+today%27s+ideas...+refresh+Posts+in+30-90+seconds", status_code=303)

# ---- Settings endpoints (example updates) ----

@app.post("/settings/kill-switch")
def set_kill_switch(
    request: Request,
    enabled: bool = Form(...),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    key = "KILL_SWITCH"
    val = "true" if enabled else "false"
    s = db.get(Setting, key) or Setting(key=key, value=val)
    s.value = val
    s.updated_at = now_utc()
    db.add(s)
    db.commit()
    return {"key": key, "value": val, "by": user}

@app.post("/settings/action-mode")
def set_action_mode(
    request: Request,
    mode: ActionMode = Form(...),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    key = "ACTION_MODE"
    s = db.get(Setting, key) or Setting(key=key, value=mode.value)
    s.value = mode.value
    s.updated_at = now_utc()
    db.add(s)
    db.commit()
    return {"key": key, "value": mode.value, "by": user}

@app.get("/settings")
def get_settings(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    items = db.query(Setting).all()
    return {"by": user, "settings": {i.key: i.value for i in items}}

# Keep the rest of your endpoints the same, but add:
# request: Request as first arg and user: str = Depends(require_admin)
# so header auth OR cookie auth works everywhere.

# ---- Daily plan ----

@app.get("/plan/today")
def plan_today(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    today = date.today().isoformat()
    plan = db.query(DailyPlan).filter(DailyPlan.plan_date == today).first()
    if not plan:
        return {"date": today, "summary": None}
    return {"date": plan.plan_date, "summary": plan.summary, "created_at": plan.created_at}

@app.post("/plan")
def upsert_plan(
    plan_date: str = Form(...),
    summary: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    plan = db.query(DailyPlan).filter(DailyPlan.plan_date == plan_date).first()
    if not plan:
        plan = DailyPlan(plan_date=plan_date, summary=summary)
    else:
        plan.summary = summary
    db.add(plan)
    db.commit()
    return {"id": plan.id, "plan_date": plan.plan_date}

# ---- Post drafts ----

@app.get("/posts/drafts")
def list_post_drafts(
    status: ApprovalStatus = ApprovalStatus.pending,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    q = db.query(PostDraft).filter(PostDraft.status == status).order_by(PostDraft.created_at.desc())
    items = q.limit(limit).all()
    return [{
        "id": p.id,
        "type": p.content_type.value,
        "hook": p.hook,
        "caption": p.caption,
        "hashtags": p.hashtags,
        "media_notes": p.media_notes,
        "status": p.status.value,
        "created_at": p.created_at,
    } for p in items]

### Approve post

@app.post("/posts/{post_id}/approve")
def approve_post(
    request: Request,
    post_id: int,
    approved: bool = Form(...),
    by: str = Form("Mary/Darrell"),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    p = db.get(PostDraft, post_id)
    if not p:
        raise HTTPException(404, "Not found")

    if approved:
        p.status = ApprovalStatus.approved
        p.approved_by = by or user
        p.approved_at = now_utc()
        p.rejection_reason = None
    else:
        p.status = ApprovalStatus.rejected
        p.rejection_reason = (reason or "Rejected").strip()[:280]

    db.add(p)
    db.commit()

    # UI-friendly redirect
    return RedirectResponse(url="/admin/posts", status_code=303)

### Mark posted

@app.post("/posts/{post_id}/posted")
def mark_posted(
    post_id: int,
    ig_url: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    pd = db.query(PostDraft).filter(PostDraft.id == post_id).first()
    if not pd:
        raise HTTPException(status_code=404, detail="PostDraft not found")

    pd.posted_at = datetime.utcnow()
    if ig_url:
        pd.ig_url = ig_url.strip()

    db.add(pd)
    db.commit()

    return RedirectResponse(url="/admin/queue", status_code=303)

### Unpost (in case of misclick, posted_at & ig_url = None)

@app.post("/posts/{post_id}/unposted")
def unpost(
    post_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    pd = db.query(PostDraft).filter(PostDraft.id == post_id).first()
    if not pd:
        raise HTTPException(status_code=404, detail="PostDraft not found")

    pd.posted_at = None
    pd.ig_url = None
    db.add(pd)
    db.commit()

    return RedirectResponse(url="/admin/queue", status_code=303)

### Admin queue

@app.get("/admin/queue", response_class=HTMLResponse)
def admin_queue(
    request: Request,
    day: str | None = None,                 # optional YYYY-MM-DD
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    # focus day (defaults today)
    if day:
        d = date.fromisoformat(day)
    else:
        d = date.today()

    start = datetime.combine(d, time.min)
    end = start + timedelta(days=1)

    items_today = (
        db.query(PostDraft)
        .filter(PostDraft.status == ApprovalStatus.approved)
        .filter(PostDraft.scheduled_for >= start, PostDraft.scheduled_for < end)
        .filter(PostDraft.posted_at.is_(None))  # <-- important
        .order_by(PostDraft.scheduled_for.asc())
        .all()
    )

    # for p in items_today:
    #     p.shoot_pack_obj = _shoot_pack_obj(p)

    backlog = (
        db.query(PostDraft)
        .filter(PostDraft.status == ApprovalStatus.approved)
        .filter(PostDraft.scheduled_for.is_(None))
        .filter(PostDraft.posted_at.is_(None))
        .order_by(PostDraft.created_at.desc())
        .limit(200)
        .all()
    )

    # for p in backlog:
    #     p.shoot_pack_obj = _shoot_pack_obj(p)

    # Recently posted
    posted_recent = (
        db.query(PostDraft)
        .filter(PostDraft.posted_at.is_not(None))
        .order_by(PostDraft.posted_at.desc())
        .limit(50)
        .all()
    )

    return templates.TemplateResponse(
        request,
        "queue.html",
        {
            "user": user,
            "day": d.isoformat(),
            "items_today": items_today,
            "backlog": backlog,
            "posted_recent": posted_recent,
        },
    )

@app.post("/posts/{post_id}/shoot-pack")
def trigger_shoot_pack(
    post_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    pd = db.query(PostDraft).filter(PostDraft.id == post_id).first()
    if not pd:
        raise HTTPException(status_code=404, detail="PostDraft not found")

    # enqueue task
    # build_shoot_pack.delay(post_id)
    # enqueue Celery task
    celery_client.send_task(
        BUILD_SHOOT_PACK_TASK,   # "tasks.build_shoot_pack"
        args=[post_id],
    )

    return RedirectResponse(url="/admin/queue", status_code=303)

### Trigger b-roll pack

@app.post("/posts/{post_id}/broll-pack")
def trigger_broll_pack(
    post_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    pd = db.query(PostDraft).filter(PostDraft.id == post_id).first()
    if not pd:
        raise HTTPException(status_code=404, detail="PostDraft not found")

    celery_client.send_task(BUILD_BROLL_PACK_TASK, args=[post_id])
    return RedirectResponse(url="/admin/queue", status_code=303)

### Schedule post (just sets schedule_for field)

@app.post("/posts/{post_id}/schedule")
def schedule_post(
    post_id: int,
    scheduled_for: str = Form(...),  # from <input type="datetime-local">
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    pd = db.query(PostDraft).filter(PostDraft.id == post_id).first()
    if not pd:
        raise HTTPException(status_code=404, detail="PostDraft not found")

    if not pd.shoot_pack:
        raise HTTPException(status_code=400, detail="Generate shoot pack before scheduling")

    # datetime-local comes in like "2026-02-20T14:30"
    try:
        dt = datetime.fromisoformat(scheduled_for)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scheduled_for datetime")

    pd.scheduled_for = dt
    db.add(pd)
    db.commit()

    return RedirectResponse(url="/admin/queue", status_code=303)

### Unschedule post (scheduled_for set to None)

@app.post("/posts/{post_id}/unschedule")
def unschedule_post(
    post_id: int,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    pd = db.query(PostDraft).filter(PostDraft.id == post_id).first()
    if not pd:
        raise HTTPException(status_code=404, detail="PostDraft not found")

    pd.scheduled_for = None
    db.add(pd)
    db.commit()

    return RedirectResponse(url="/admin/queue", status_code=303)

# ---- Engagement queue ----

@app.get("/engagement/queue")
def list_engagement_queue(
    status: ApprovalStatus = ApprovalStatus.pending,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    q = db.query(EngagementQueueItem).filter(EngagementQueueItem.status == status).order_by(EngagementQueueItem.created_at.desc())
    items = q.limit(limit).all()
    return [{
        "id": i.id,
        "target_handle": i.target_handle,
        "target_url": i.target_url,
        "like": i.action_like,
        "comment": i.action_comment,
        "suggested_comment": i.suggested_comment,
        "status": i.status.value,
        "created_at": i.created_at,
    } for i in items]

@app.post("/engagement/{item_id}/approve")
def approve_engagement(
    request: Request,
    item_id: int,
    approved: bool = Form(...),
    by: str = Form("Mary/Darrell"),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    i = db.get(EngagementQueueItem, item_id)
    if not i:
        raise HTTPException(404, "Not found")

    if approved:
        i.status = ApprovalStatus.approved
        i.approved_by = by or user
        i.approved_at = now_utc()
    else:
        i.status = ApprovalStatus.rejected

    db.add(i)
    db.commit()

    return RedirectResponse(url="/admin/engagement", status_code=303)

# ---- Outreach queue ----

@app.get("/outreach/queue")
def list_outreach_queue(
    status: ApprovalStatus = ApprovalStatus.pending,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    q = db.query(OutreachDraft).filter(OutreachDraft.status == status).order_by(OutreachDraft.created_at.desc())
    items = q.limit(limit).all()
    return [{
        "id": o.id,
        "creator_handle": o.creator.handle if o.creator else None,
        "message": o.message,
        "offer_type": o.offer_type,
        "campaign_name": o.campaign_name,
        "status": o.status.value,
        "created_at": o.created_at,
    } for o in items]
