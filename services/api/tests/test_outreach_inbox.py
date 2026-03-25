from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import Base, get_db
from db_models import ApprovalStatus, Creator, OutreachDraft, OutreachStatus
from main import app
from settings import settings


def _make_test_client(tmp_path):
    db_file = tmp_path / "test_outreach_inbox.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
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


def test_outreach_inbox_groups_due_and_overdue_steps(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        now = datetime.utcnow().replace(second=0, microsecond=0)
        with SessionLocal() as db:
            overdue_creator = Creator(handle="amberroot", score=91, outreach_status="eligible")
            today_creator = Creator(handle="brightfern", score=83, outreach_status="eligible", fraud_flags={"phase1_emails": ["hi@brightfern.com"]})
            upcoming_creator = Creator(handle="cedargrace", score=77, outreach_status="eligible")
            db.add_all([overdue_creator, today_creator, upcoming_creator])
            db.flush()
            db.add_all([
                OutreachDraft(creator_id=overdue_creator.id, message="Overdue first touch", send_channel="instagram_dm", sequence_name="standard_3_touch", sequence_step="first_touch", due_at=now - timedelta(days=2), status=ApprovalStatus.pending, outreach_status=OutreachStatus.pending),
                OutreachDraft(creator_id=today_creator.id, message="Today email touch", send_channel="email", sequence_name="standard_3_touch", sequence_step="follow_up_1", due_at=now, status=ApprovalStatus.approved, outreach_status=OutreachStatus.approved),
                OutreachDraft(creator_id=upcoming_creator.id, message="Next week follow up", send_channel="instagram_dm", sequence_name="standard_3_touch", sequence_step="follow_up_2", due_at=now + timedelta(days=3), status=ApprovalStatus.pending, outreach_status=OutreachStatus.pending),
            ])
            db.commit()

        response = client.get("/admin/outreach/inbox", headers={"x-admin-token": settings.admin_token})
        assert response.status_code == 200
        html = response.text
        assert "Outreach Inbox" in html
        assert "amberroot" in html
        assert "brightfern" in html
        assert "cedargrace" not in html
        assert "Actionable" in html
        assert "Overdue" in html
        assert "Due today" in html
        assert "Approve draft" in html
        assert "Send email" in html

        next7_response = client.get("/admin/outreach/inbox?bucket=next_7_days&channel=instagram_dm", headers={"x-admin-token": settings.admin_token})
        assert next7_response.status_code == 200
        next7_html = next7_response.text
        assert "cedargrace" in next7_html
        assert "brightfern" not in next7_html
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_outreach_inbox_actions_redirect_back_to_inbox(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        now = datetime.utcnow().replace(second=0, microsecond=0)
        with SessionLocal() as db:
            creator = Creator(handle="dewpetal", score=80, outreach_status="eligible")
            db.add(creator)
            db.flush()
            pending = OutreachDraft(creator_id=creator.id, message="Pending approval", send_channel="instagram_dm", sequence_name="standard_3_touch", sequence_step="first_touch", due_at=now - timedelta(days=1), status=ApprovalStatus.pending, outreach_status=OutreachStatus.pending)
            approved = OutreachDraft(creator_id=creator.id, message="Ready to send", send_channel="email", sequence_name="standard_3_touch", sequence_step="follow_up_1", due_at=now, status=ApprovalStatus.approved, outreach_status=OutreachStatus.approved)
            db.add_all([pending, approved])
            db.commit()
            pending_id = pending.id
            approved_id = approved.id

        redirect_target = "/admin/outreach/inbox?bucket=actionable&channel=any&approval_state=any&page=1"
        approve_response = client.post(f"/admin/outreach_drafts/{pending_id}/approve", headers={"x-admin-token": settings.admin_token}, data={"return_to": redirect_target}, follow_redirects=False)
        assert approve_response.status_code == 303
        assert approve_response.headers["location"] == redirect_target

        mark_sent_response = client.post(f"/admin/outreach_drafts/{approved_id}/mark_sent", headers={"x-admin-token": settings.admin_token}, data={"return_to": redirect_target, "thread_url": "https://example.com/thread"}, follow_redirects=False)
        assert mark_sent_response.status_code == 303
        assert mark_sent_response.headers["location"] == redirect_target

        with SessionLocal() as db:
            approved_pending = db.get(OutreachDraft, pending_id)
            sent_draft = db.get(OutreachDraft, approved_id)
            assert approved_pending.status == ApprovalStatus.approved
            assert sent_draft.outreach_status == OutreachStatus.sent
            assert sent_draft.thread_url == "https://example.com/thread"
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()
