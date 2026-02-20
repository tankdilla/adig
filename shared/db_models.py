import enum
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, DateTime, Integer, Boolean, Enum, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

import json
from functools import cached_property

class Base(DeclarativeBase):
    pass

class ActionMode(str, enum.Enum):
    review = "review"   # generate only; requires approval to execute
    manual = "manual"   # approved queues export; no automation
    live = "live"       # approved queues may be executed by action runner

class ApprovalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"

class ContentType(str, enum.Enum):
    reel = "reel"
    carousel = "carousel"
    story = "story"
    static = "static"

class QueueType(str, enum.Enum):
    engagement = "engagement"
    outreach = "outreach"
    posting = "posting"

class EngagementActionType(str, enum.Enum):
    comment = "comment"
    like = "like"
    follow = "follow"

class EngagementStatus(str, enum.Enum):
    pending = "pending"      # generated/ready for review
    approved = "approved"    # approved by admin
    executed = "executed"    # manually completed (or executed by runner in the future)
    skipped = "skipped"      # rejected/skipped
    failed = "failed"        # generation failed / invalid target / other error

class AppLog(Base):
    __tablename__ = "app_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    level = Column(String(16), nullable=False, index=True)
    logger = Column(String(128), nullable=True, index=True)
    service = Column(String(32), nullable=True, index=True)   # api / worker / scraper
    message = Column(Text, nullable=True)

    request_id = Column(String(64), nullable=True, index=True)
    task_id = Column(String(64), nullable=True, index=True)

    event = Column(String(128), nullable=True, index=True)
    data = Column(JSONB, nullable=True)

class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

class DailyPlan(Base):
    __tablename__ = "daily_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("plan_date", name="uq_daily_plans_plan_date"),)

class PostDraft(Base):
    __tablename__ = "post_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_type: Mapped[ContentType] = mapped_column(Enum(ContentType), nullable=False)
    hook: Mapped[str] = mapped_column(String(280), nullable=True)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    hashtags: Mapped[str] = mapped_column(Text, nullable=True)  # newline separated
    media_notes: Mapped[str] = mapped_column(Text, nullable=True)  # filming notes, b-roll ideas
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.pending, nullable=False)
    approved_by: Mapped[str] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[str] = mapped_column(String(280), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    shoot_pack: Mapped[str] = mapped_column(Text, nullable=True)          # structured shoot pack text
    posted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)  # when you actually posted
    ig_url: Mapped[str] = mapped_column(Text, nullable=True)              # optional IG URL

    broll_manifest: Mapped[str] = mapped_column(Text, nullable=True)  # JSON text
    broll_dir: Mapped[str] = mapped_column(String(255), nullable=True)

    @staticmethod
    def _safe_json(raw: str | None):
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return {"_error": True, "_raw": raw}

    @cached_property
    def shoot_pack_obj(self):
        return self._safe_json(self.shoot_pack)

    @cached_property
    def broll_obj(self):
        return self._safe_json(self.broll_manifest)

class EngagementQueueItem(Base):
    __tablename__ = "engagement_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)  # post/profile URL
    target_handle: Mapped[str] = mapped_column(String(120), nullable=True)
    suggested_comment: Mapped[str] = mapped_column(Text, nullable=True)
    action_like: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    action_comment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.pending, nullable=False)
    approved_by: Mapped[str] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

class EngagementAction(Base):
    """
    Next-gen engagement queue table (safe-by-design).
    - Stores targets + generated copy
    - Supports scheduling and “mark executed” workflow
    - Avoids automation by default; human can copy/paste manually
    """
    __tablename__ = "engagement_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    platform: Mapped[str] = mapped_column(String(32), default="instagram", nullable=False)

    # Target info (what you want to engage with)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    target_handle: Mapped[str] = mapped_column(String(120), nullable=True)
    target_caption: Mapped[str] = mapped_column(Text, nullable=True)

    # Action
    action_type: Mapped[EngagementActionType] = mapped_column(Enum(EngagementActionType), nullable=False)

    # AI draft output (e.g., comment text)
    proposed_text: Mapped[str] = mapped_column(Text, nullable=True)

    # Workflow / scheduling
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    status: Mapped[EngagementStatus] = mapped_column(Enum(EngagementStatus), default=EngagementStatus.pending, nullable=False)

    approved_by: Mapped[str] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    executed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # Prevent duplicate engagement items for same url/action/platform
        UniqueConstraint("platform", "action_type", "target_url", name="uq_engagement_actions_target"),
    )

class Creator(Base):
    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    handle: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    platform: Mapped[str] = mapped_column(String(32), default="instagram", nullable=False)
    followers_est: Mapped[int] = mapped_column(Integer, nullable=True)
    niche_tags: Mapped[str] = mapped_column(Text, nullable=True)  # comma-separated
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

class OutreachDraft(Base):
    __tablename__ = "outreach_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey("creators.id"), nullable=False)
    creator: Mapped["Creator"] = relationship("Creator")

    message: Mapped[str] = mapped_column(Text, nullable=False)
    offer_type: Mapped[str] = mapped_column(String(64), nullable=True)  # gifted/affiliate/etc
    campaign_name: Mapped[str] = mapped_column(String(120), nullable=True)

    status: Mapped[ApprovalStatus] = mapped_column(Enum(ApprovalStatus), default=ApprovalStatus.pending, nullable=False)
    approved_by: Mapped[str] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
