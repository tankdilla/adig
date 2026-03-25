from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import app, get_db, settings
from db_models import (
    ApprovalStatus,
    ContentType,
    Creator,
    EngagementAction,
    EngagementActionType,
    EngagementStatus,
    OutreachDraft,
    OutreachEvent,
    OutreachStatus,
    PostDraft,
)


def _make_test_client(tmp_path: Path):
    db_path = tmp_path / "workflow.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    return client, SessionLocal, engine


def test_post_approval_and_posting_workflow(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        with SessionLocal() as db:
            post = PostDraft(
                content_type=ContentType.reel,
                hook="Hook",
                caption="Caption",
                status=ApprovalStatus.pending,
            )
            db.add(post)
            db.commit()
            db.refresh(post)
            post_id = post.id

        approve_response = client.post(
            f"/posts/{post_id}/approve",
            headers={"x-admin-token": settings.admin_token},
            data={"approved": "true", "by": "Mary"},
            follow_redirects=False,
        )
        assert approve_response.status_code == 303
        assert approve_response.headers["location"] == "/admin/posts"

        with SessionLocal() as db:
            post = db.get(PostDraft, post_id)
            assert post.status == ApprovalStatus.approved
            assert post.approved_by == "Mary"
            assert post.approved_at is not None
            assert post.rejection_reason is None

        mark_posted_response = client.post(
            f"/posts/{post_id}/posted",
            headers={"x-admin-token": settings.admin_token},
            data={"ig_url": "https://instagram.com/p/demo"},
            follow_redirects=False,
        )
        assert mark_posted_response.status_code == 303
        assert mark_posted_response.headers["location"] == "/admin/queue"

        with SessionLocal() as db:
            post = db.get(PostDraft, post_id)
            assert post.posted_at is not None
            assert post.ig_url == "https://instagram.com/p/demo"

        unpost_response = client.post(
            f"/posts/{post_id}/unposted",
            headers={"x-admin-token": settings.admin_token},
            follow_redirects=False,
        )
        assert unpost_response.status_code == 303
        assert unpost_response.headers["location"] == "/admin/queue"

        with SessionLocal() as db:
            post = db.get(PostDraft, post_id)
            assert post.posted_at is None
            assert post.ig_url is None
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_engagement_approval_skip_and_execute_workflow(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        with SessionLocal() as db:
            approved_item = EngagementAction(
                platform="instagram",
                target_url="https://instagram.com/p/approved",
                target_handle="creator_one",
                action_type=EngagementActionType.comment,
                status=EngagementStatus.pending,
            )
            skipped_item = EngagementAction(
                platform="instagram",
                target_url="https://instagram.com/p/skipped",
                target_handle="creator_two",
                action_type=EngagementActionType.comment,
                status=EngagementStatus.pending,
            )
            db.add_all([approved_item, skipped_item])
            db.commit()
            db.refresh(approved_item)
            db.refresh(skipped_item)
            approved_id = approved_item.id
            skipped_id = skipped_item.id

        approve_response = client.post(
            f"/engagement/{approved_id}/approve",
            headers={"x-admin-token": settings.admin_token},
            follow_redirects=False,
        )
        assert approve_response.status_code == 303
        assert approve_response.headers["location"] == "/admin/engagement?view=pending"

        with SessionLocal() as db:
            item = db.get(EngagementAction, approved_id)
            assert item.status == EngagementStatus.approved
            assert item.approved_by == "header_admin"
            assert item.approved_at is not None

        executed_response = client.post(
            f"/engagement/{approved_id}/executed",
            headers={"x-admin-token": settings.admin_token},
            data={"note": "Done manually from admin queue"},
            follow_redirects=False,
        )
        assert executed_response.status_code == 303
        assert executed_response.headers["location"] == "/admin/engagement?view=approved"

        with SessionLocal() as db:
            item = db.get(EngagementAction, approved_id)
            assert item.status == EngagementStatus.executed
            assert item.executed_at is not None
            assert item.notes == "Done manually from admin queue"

        skip_response = client.post(
            f"/engagement/{skipped_id}/skip",
            headers={"x-admin-token": settings.admin_token},
            data={"reason": "Off brand target"},
            follow_redirects=False,
        )
        assert skip_response.status_code == 303
        assert skip_response.headers["location"] == "/admin/engagement?view=pending"

        with SessionLocal() as db:
            item = db.get(EngagementAction, skipped_id)
            assert item.status == EngagementStatus.skipped
            assert item.notes == "Off brand target"
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_outreach_draft_approval_send_and_response_workflow(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        with SessionLocal() as db:
            creator = Creator(handle="partnercreator", score=82, fraud_score=5)
            db.add(creator)
            db.flush()
            draft = OutreachDraft(
                creator_id=creator.id,
                message="Would love to send you a gifted set.",
                status=ApprovalStatus.pending,
                outreach_status=OutreachStatus.pending,
            )
            db.add(draft)
            db.commit()
            db.refresh(draft)
            draft_id = draft.id

        approve_response = client.post(
            f"/outreach/{draft_id}/approve",
            headers={"x-admin-token": settings.admin_token},
            follow_redirects=False,
        )
        assert approve_response.status_code == 303
        assert approve_response.headers["location"] == "/admin/outreach?view=pending"

        with SessionLocal() as db:
            draft = db.get(OutreachDraft, draft_id)
            events = (
                db.query(OutreachEvent)
                .filter(OutreachEvent.outreach_draft_id == draft_id)
                .order_by(OutreachEvent.id.asc())
                .all()
            )
            assert draft.status == ApprovalStatus.approved
            assert draft.outreach_status == OutreachStatus.approved
            assert draft.approved_by == "header_admin"
            assert draft.approved_at is not None
            assert [event.event_type for event in events] == ["approved"]

        sent_response = client.post(
            f"/outreach/{draft_id}/sent",
            headers={"x-admin-token": settings.admin_token},
            data={"sent_by": "Mary", "thread_url": "https://instagram.com/direct/thread/123"},
            follow_redirects=False,
        )
        assert sent_response.status_code == 303
        assert sent_response.headers["location"] == "/admin/outreach?view=approved"

        with SessionLocal() as db:
            draft = db.get(OutreachDraft, draft_id)
            events = (
                db.query(OutreachEvent)
                .filter(OutreachEvent.outreach_draft_id == draft_id)
                .order_by(OutreachEvent.id.asc())
                .all()
            )
            assert draft.outreach_status == OutreachStatus.sent
            assert draft.sent_at is not None
            assert draft.sent_by == "Mary"
            assert draft.thread_url == "https://instagram.com/direct/thread/123"
            assert [event.event_type for event in events] == ["approved", "sent"]

        response_record = client.post(
            f"/outreach/{draft_id}/response",
            headers={"x-admin-token": settings.admin_token},
            data={"status": "booked", "response_text": "Yes, I would love to collaborate."},
            follow_redirects=False,
        )
        assert response_record.status_code == 303
        assert response_record.headers["location"] == "/admin/outreach?view=sent"

        with SessionLocal() as db:
            draft = db.get(OutreachDraft, draft_id)
            events = (
                db.query(OutreachEvent)
                .filter(OutreachEvent.outreach_draft_id == draft_id)
                .order_by(OutreachEvent.id.asc())
                .all()
            )
            assert draft.outreach_status == OutreachStatus.booked
            assert draft.last_response_at is not None
            assert draft.last_response_text == "Yes, I would love to collaborate."
            assert [event.event_type for event in events] == ["approved", "sent", "response:booked"]
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()
