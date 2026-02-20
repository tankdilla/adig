import os
import logging
import structlog
from celery import Celery
from fastapi import FastAPI, Depends, Header, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_
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
    Creator,
    ApprovalStatus,
    ContentType,
    ActionMode,
    AppLog,
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
    status: ApprovalStatus = ApprovalStatus.pending,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    items = (
        db.query(EngagementQueueItem)
        .filter(EngagementQueueItem.status == status)
        .order_by(EngagementQueueItem.created_at.desc())
        .limit(300)
        .all()
    )
    return templates.TemplateResponse("engagement.html", {
        "request": request,
        "user": user,
        "status": status.value,
        "items": items,
    })

# --- Admin UI: Outreach ---

@app.get("/admin/outreach", response_class=HTMLResponse)
def admin_outreach(
    request: Request,
    status: ApprovalStatus = ApprovalStatus.pending,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    items = (
        db.query(OutreachDraft)
        .filter(OutreachDraft.status == status)
        .order_by(OutreachDraft.created_at.desc())
        .limit(300)
        .all()
    )
    return templates.TemplateResponse("outreach.html", {
        "request": request,
        "user": user,
        "status": status.value,
        "items": items,
    })

# --- Admin UI: Generate Today (celery task) ---

@app.post("/admin/generate-today")
def generate_today(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    # Simple click-spam guard: 5 minute cooldown
    key = "CONTENT_INTEL_LAST_REQUESTED_AT"
    last = db.get(Setting, key)

    from datetime import datetime, timedelta
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

### Helper
# def _shoot_pack_obj(p):
#     if not p.shoot_pack:
#         return None
#     try:
#         return json.loads(p.shoot_pack)
#     except Exception:
#         return {"_raw": p.shoot_pack, "_error": "Invalid JSON"}

### Trigger shoot pack

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

@app.post("/outreach/{draft_id}/approve")
def approve_outreach(
    request: Request,
    draft_id: int,
    approved: bool = Form(...),
    by: str = Form("Mary/Darrell"),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    o = db.get(OutreachDraft, draft_id)
    if not o:
        raise HTTPException(404, "Not found")

    if approved:
        o.status = ApprovalStatus.approved
        o.approved_by = by or user
        o.approved_at = now_utc()
    else:
        o.status = ApprovalStatus.rejected

    db.add(o)
    db.commit()

    return RedirectResponse(url="/admin/outreach", status_code=303)
