import os
import logging
import structlog
from celery import Celery
from fastapi import FastAPI, Depends, Header, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session
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
CREATOR_DISCOVERY_TASK = os.getenv("CREATOR_DISCOVERY_TASK", "tasks.creator_discovery_hashtags")
CREATOR_GRAPH_TASK = os.getenv("CREATOR_GRAPH_TASK", "tasks.creator_graph_update")
VIRAL_PATTERNS_TASK = os.getenv("VIRAL_PATTERNS_TASK", "tasks.viral_patterns_daily")

celery_client = Celery("h2n_api_client", broker=REDIS_URL, backend=REDIS_URL)

def now_utc():
    return datetime.utcnow()

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
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    token: str = Form(...),
):
    if token != settings.admin_token:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid token."})

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

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "pending_posts": pending_posts,
        "pending_eng": pending_eng,
        "pending_out": pending_out,
        "settings": settings_map,
        "user": user,
    })


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

    # query = (
    #     db.query(Creator)
    #     .filter(Creator.score >= min_score)
    #     .filter(Creator.is_brand.is_(False))
    #     .filter(Creator.is_spam.is_(False))
    #     .filter(Creator.fraud_score < max_fraud)
    #     # .filter(Creator.outreach_status == "eligible")
    #     .order_by(Creator.score.desc(), Creator.created_at.desc())
    # )

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

    return templates.TemplateResponse(
        "creators.html",
        {
            "request": request,
            "user": user,
            "creators": creators,
            "total": total,
            "page": page,
            "page_size": page_size,
            "min_score": min_score,
            "max_fraud": max_fraud,
        },
    )

# make sure these models exist in your db_models import list
# Creator, CreatorEdge, CreatorRelationship, OutreachDraft, OutreachEvent, CreatorPost
# If your names differ, adjust accordingly.

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

    return templates.TemplateResponse(
        "creator_profile.html",
        {
            "request": request,
            "creator": creator,
            "relationship": rel,
            "drafts": drafts,
            "events": events,
            "edges": edges,
            "posts": posts,
            "campaigns": campaigns,
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

@app.post("/admin/outreach_drafts/{draft_id}/approve")
def admin_approve_outreach_draft(
    draft_id: int,
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

    return RedirectResponse(url=f"/admin/creators/{d.creator_id}", status_code=303)


@app.post("/admin/outreach_drafts/{draft_id}/unapprove")
def admin_unapprove_outreach_draft(
    draft_id: int,
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

    return RedirectResponse(url=f"/admin/creators/{d.creator_id}", status_code=303)


@app.post("/admin/outreach_drafts/{draft_id}/mark_sent")
def admin_mark_outreach_sent(
    draft_id: int,
    thread_url: str = Form(""),
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

    return RedirectResponse(url=f"/admin/creators/{d.creator_id}", status_code=303)


@app.post("/admin/outreach_drafts/{draft_id}/record_reply")
def admin_record_outreach_reply(
    draft_id: int,
    last_response_text: str = Form(""),
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

    return RedirectResponse(url=f"/admin/creators/{d.creator_id}", status_code=303)

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
    limit: int = Form(200),
    rotate: int = Form(4),
    user: str = Depends(require_admin),
):
    # Fire and forget celery task
    celery_client.send_task(CREATOR_DISCOVERY_TASK, kwargs={"limit": int(limit), "rotate": int(rotate)})
    return RedirectResponse(url="/admin/creators", status_code=303)

### Set creator outreach status
@app.post("/admin/creators/{creator_id}/outreach_status")
def set_creator_outreach_status(
    creator_id: int,
    status: str = Form(...),  # eligible | excluded | do_not_contact
    reason: str = Form(""),
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
        "graph.html",
        {"request": request, "user": user, "handle": handle or "", "creator": creator, "neighbors": neighbors},
    )


@app.get("/admin/patterns", response_class=HTMLResponse)
def admin_patterns(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    latest = db.query(ViralPatternReport).order_by(ViralPatternReport.id.desc()).first()
    return templates.TemplateResponse(
        "patterns.html",
        {"request": request, "user": user, "latest": latest},
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
        "logs.html",
        {
            "request": request,
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
        "posts.html",
        {
            "request": request,
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
        "engagement.html",
        {"request": request, "items": items, "view": view, "user": user},
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
            target_author=author,
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
        "outreach.html",
        {
            "request": request,
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
        "queue.html",
        {
            "request": request,
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
