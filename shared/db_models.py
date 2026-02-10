import enum
from datetime import datetime
from sqlalchemy import (
    String, Text, DateTime, Integer, Boolean, Enum, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase
# from .db import Base

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
