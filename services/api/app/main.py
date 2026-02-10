import os
from celery import Celery
from fastapi import FastAPI, Depends, Header, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import datetime, date

from .db import Base, engine, get_db
from .settings import settings
# from . import models
from .auth import sign_session, verify_session, COOKIE_NAME
from pathlib import Path
from shared.db_models import (
    Setting,
    DailyPlan,
    PostDraft,
    EngagementQueueItem,
    OutreachDraft,
    Creator,
    ApprovalStatus,
    ContentType,
    ActionMode,
)

app = FastAPI(title="H2N Agent Control Plane", version="0.1.0")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# MVP: auto-create tables (later you can switch to Alembic)
Base.metadata.create_all(bind=engine)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CONTENT_INTEL_TASK = os.getenv("CONTENT_INTEL_TASK", "tasks.content_intel_daily")

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

# --- Admin UI: Posts ---

@app.get("/admin/posts", response_class=HTMLResponse)
def admin_posts(
    request: Request,
    status: ApprovalStatus = ApprovalStatus.pending,
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    items = (
        db.query(PostDraft)
        .filter(PostDraft.status == status)
        .order_by(PostDraft.created_at.desc())
        .limit(200)
        .all()
    )
    return templates.TemplateResponse("posts.html", {
        "request": request,
        "user": user,
        "status": status.value,
        "items": items,
    })

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

# --- Admin UI: Generate Today ---

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

# ---- Simple Admin Page (mobile-friendly) ----

@app.get("/", response_class=HTMLResponse)
def admin_home(request: Request, db: Session = Depends(get_db), x_admin_token: str | None = Header(default=None)):
    # light auth: require header token to view admin
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized (set x-admin-token header)")
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
    })
