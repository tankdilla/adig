from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import app, get_db, settings
from db_models import ApprovalStatus, Creator, OutreachDraft, OutreachEvent, OutreachStatus


def _make_test_client(tmp_path: Path):
    db_path = tmp_path / "creator_profile.db"
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


def test_creator_profile_shows_contact_summary_and_next_step(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        with SessionLocal() as db:
            creator = Creator(
                handle="earthglowdaily",
                platform="instagram",
                score=90,
                notes="Phase 1 discovery score: 90\nDiscovery reasons: has email, multi source",
                fraud_flags={
                    "phase1_source_platforms": ["youtube", "web"],
                    "phase1_sources": ["https://example.com/top-earth-glow"],
                    "phase1_emails": ["hello@earthglowdaily.com"],
                    "phase1_website_url": "https://earthglowdaily.com",
                    "phase1_reasons": ["has email", "multi source"],
                    "phase1_confidence": 0.88,
                    "discovery_review_status": "approved",
                },
                outreach_status="eligible",
            )
            db.add(creator)
            db.commit()
            creator_id = creator.id

        response = client.get(f"/admin/creators/{creator_id}", headers={"x-admin-token": settings.admin_token})
        assert response.status_code == 200
        html = response.text
        assert "Creator Snapshot" in html
        assert "Create a first-touch email draft to hello@earthglowdaily.com." in html
        assert "hello@earthglowdaily.com" in html
        assert "https://earthglowdaily.com" in html
        assert "https://example.com/top-earth-glow" in html
        assert "has email" in html
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_creator_profile_outreach_edit_followup_and_stage_workflow(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        with SessionLocal() as db:
            creator = Creator(handle="lavenderlane", score=81, outreach_status="eligible")
            db.add(creator)
            db.flush()
            draft = OutreachDraft(
                creator_id=creator.id,
                message="Original first touch",
                offer_type="gifted set",
                campaign_name="Spring Push",
                send_channel="instagram_dm",
                status=ApprovalStatus.approved,
                outreach_status=OutreachStatus.sent,
                followups_sent=0,
            )
            db.add(draft)
            db.commit()
            draft_id = draft.id
            creator_id = creator.id

        update_response = client.post(
            f"/admin/outreach_drafts/{draft_id}/update",
            headers={"x-admin-token": settings.admin_token},
            data={
                "message": "Updated creator-specific draft",
                "offer_type": "affiliate",
                "campaign_name": "Summer Push",
                "send_channel": "email",
                "thread_url": "https://instagram.com/direct/thread/555",
            },
            follow_redirects=False,
        )
        assert update_response.status_code == 303
        assert update_response.headers["location"] == f"/admin/creators/{creator_id}"

        with SessionLocal() as db:
            updated = db.get(OutreachDraft, draft_id)
            assert updated.message == "Updated creator-specific draft"
            assert updated.offer_type == "affiliate"
            assert updated.campaign_name == "Summer Push"
            assert updated.send_channel == "email"
            assert updated.thread_url == "https://instagram.com/direct/thread/555"

        followup_response = client.post(
            f"/admin/outreach_drafts/{draft_id}/followup",
            headers={"x-admin-token": settings.admin_token},
            data={"tone": "warm"},
            follow_redirects=False,
        )
        assert followup_response.status_code == 303
        assert followup_response.headers["location"] == f"/admin/creators/{creator_id}"

        with SessionLocal() as db:
            drafts = db.query(OutreachDraft).filter(OutreachDraft.creator_id == creator_id).order_by(OutreachDraft.id.asc()).all()
            assert len(drafts) == 2
            original, followup = drafts
            assert original.followups_sent == 1
            assert followup.status == ApprovalStatus.pending
            assert followup.outreach_status == OutreachStatus.pending
            assert "Mary, Hello To Natural" in followup.message
            assert "We'd still love to explore affiliate with you." in followup.message

        stage_response = client.post(
            f"/admin/outreach_drafts/{draft_id}/stage",
            headers={"x-admin-token": settings.admin_token},
            data={"stage": "booked", "note": "Creator confirmed a May collab"},
            follow_redirects=False,
        )
        assert stage_response.status_code == 303
        assert stage_response.headers["location"] == f"/admin/creators/{creator_id}"

        with SessionLocal() as db:
            booked = db.get(OutreachDraft, draft_id)
            events = db.query(OutreachEvent).filter(OutreachEvent.outreach_draft_id == draft_id).all()
            assert booked.outreach_status == OutreachStatus.booked
            assert booked.last_response_text == "Creator confirmed a May collab"
            assert any(event.event_type == "profile:booked" for event in events)
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_creator_profile_sequence_planner_creates_three_touch_plan(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        with SessionLocal() as db:
            creator = Creator(
                handle="kindrootswellness",
                score=84,
                outreach_status="eligible",
                fraud_flags={"phase1_emails": ["hello@kindrootswellness.com"]},
            )
            db.add(creator)
            db.commit()
            creator_id = creator.id

        response = client.post(
            f"/admin/creators/{creator_id}/outreach_sequence",
            headers={"x-admin-token": settings.admin_token},
            data={
                "start_at": "2026-03-26T09:00",
                "offer_type": "an affiliate collaboration",
                "campaign_name": "Spring Creator Push",
                "send_channel": "email",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/admin/creators/{creator_id}"

        with SessionLocal() as db:
            drafts = (
                db.query(OutreachDraft)
                .filter(OutreachDraft.creator_id == creator_id)
                .order_by(OutreachDraft.id.asc())
                .all()
            )
            assert len(drafts) == 3
            assert [draft.sequence_step for draft in drafts] == ["first_touch", "follow_up_1", "follow_up_2"]
            assert all(draft.sequence_name == "standard_3_touch" for draft in drafts)
            assert drafts[0].send_channel == "email"
            assert drafts[0].due_at.strftime("%Y-%m-%d %H:%M") == "2026-03-26 09:00"
            assert drafts[1].due_at.strftime("%Y-%m-%d %H:%M") == "2026-03-30 09:00"
            assert drafts[2].due_at.strftime("%Y-%m-%d %H:%M") == "2026-04-04 09:00"
            assert "Hi @kindrootswellness," in drafts[0].message
            assert "one last quick follow up" in drafts[2].message

        profile_response = client.get(f"/admin/creators/{creator_id}", headers={"x-admin-token": settings.admin_token})
        assert profile_response.status_code == 200
        html = profile_response.text
        assert "Outreach Sequence Planner" in html
        assert "First Touch" in html
        assert "Follow Up 1" in html
        assert "Follow Up 2" in html
        assert "Spring Creator Push" in html
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_creator_profile_update_draft_due_date_and_sequence_step(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        with SessionLocal() as db:
            creator = Creator(handle="oliveandember", score=75, outreach_status="eligible")
            db.add(creator)
            db.flush()
            draft = OutreachDraft(
                creator_id=creator.id,
                message="Original note",
                send_channel="instagram_dm",
                status=ApprovalStatus.pending,
                outreach_status=OutreachStatus.pending,
            )
            db.add(draft)
            db.commit()
            draft_id = draft.id
            creator_id = creator.id

        update_response = client.post(
            f"/admin/outreach_drafts/{draft_id}/update",
            headers={"x-admin-token": settings.admin_token},
            data={
                "message": "Updated timed draft",
                "offer_type": "gifted set",
                "campaign_name": "April Flow",
                "send_channel": "instagram_dm",
                "thread_url": "",
                "sequence_step": "follow_up_1",
                "due_at": "2026-03-29T14:30",
            },
            follow_redirects=False,
        )
        assert update_response.status_code == 303
        assert update_response.headers["location"] == f"/admin/creators/{creator_id}"

        with SessionLocal() as db:
            updated = db.get(OutreachDraft, draft_id)
            assert updated.sequence_step == "follow_up_1"
            assert updated.due_at.strftime("%Y-%m-%d %H:%M") == "2026-03-29 14:30"
            assert updated.message == "Updated timed draft"

        profile_response = client.get(f"/admin/creators/{creator_id}", headers={"x-admin-token": settings.admin_token})
        assert profile_response.status_code == 200
        assert "follow_up_1" in profile_response.text
        assert "2026-03-29 14:30:00" in profile_response.text
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()
