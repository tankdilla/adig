from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import app, get_db, settings
from db_models import Creator


def _make_test_client(tmp_path: Path):
    db_path = tmp_path / "review_queue.db"
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


def test_review_queue_shows_discovery_reasoning_and_links(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        db = SessionLocal()
        db.add(
            Creator(
                handle="glowherballife",
                platform="instagram",
                score=88,
                followers_est=18000,
                niche_tags="natural wellness, body care",
                notes="""Phase 1 discovery score: 88
Discovery reasons: has email, multi source
Mentioned in curated web list""",
                fraud_flags={
                    "phase1_sources": ["https://example.com/top-creators"],
                    "phase1_source_platforms": ["youtube", "web"],
                    "phase1_reasons": ["has email", "multi source"],
                    "phase1_emails": ["collabs@example.com"],
                    "phase1_website_url": "https://example.com",
                    "phase1_confidence": 0.82,
                    "discovery_review_status": "pending",
                },
            )
        )
        db.commit()
        db.close()

        response = client.get("/admin/creators/review", headers={"x-admin-token": settings.admin_token})

        assert response.status_code == 200
        html = response.text
        assert "@glowherballife" in html
        assert "has email" in html
        assert "multi source" in html
        assert "https://example.com/top-creators" in html
        assert "collabs@example.com" in html
        assert "Approve" in html
        assert "Reject" in html
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_review_actions_update_status_and_outreach(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        db = SessionLocal()
        creator = Creator(
            handle="wellnessmaker",
            platform="instagram",
            score=72,
            notes="Phase 1 discovery score: 72",
            fraud_flags={"phase1_sources": ["https://example.com/list"], "discovery_review_status": "pending"},
        )
        db.add(creator)
        db.commit()
        creator_id = creator.id
        db.close()

        approve = client.post(
            f"/admin/creators/{creator_id}/review",
            headers={"x-admin-token": settings.admin_token},
            data={"decision": "approved", "reason": "Strong fit"},
            follow_redirects=False,
        )
        assert approve.status_code == 303

        db = SessionLocal()
        updated = db.get(Creator, creator_id)
        assert updated.outreach_status == "eligible"
        assert updated.fraud_flags["discovery_review_status"] == "approved"
        assert updated.fraud_flags["discovery_review_reason"] == "Strong fit"
        assert "Discovery review: approved" in (updated.notes or "")
        db.close()

        reject = client.post(
            f"/admin/creators/{creator_id}/review",
            headers={"x-admin-token": settings.admin_token},
            data={"decision": "rejected", "reason": "Off niche"},
            follow_redirects=False,
        )
        assert reject.status_code == 303

        db = SessionLocal()
        updated = db.get(Creator, creator_id)
        assert updated.outreach_status == "excluded"
        assert updated.outreach_exclude_reason == "Discovery review: Off niche"
        assert updated.fraud_flags["discovery_review_status"] == "rejected"
        db.close()
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_bulk_review_action_updates_multiple_creators(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        db = SessionLocal()
        creator_one = Creator(
            handle="bulkfitone",
            platform="instagram",
            score=81,
            notes="Phase 1 discovery score: 81",
            fraud_flags={"phase1_sources": ["https://example.com/list-a"], "discovery_review_status": "pending"},
        )
        creator_two = Creator(
            handle="bulkfittwo",
            platform="instagram",
            score=79,
            notes="Phase 1 discovery score: 79",
            fraud_flags={"phase1_sources": ["https://example.com/list-b"], "discovery_review_status": "pending"},
        )
        db.add_all([creator_one, creator_two])
        db.commit()
        creator_ids = [creator_one.id, creator_two.id]
        db.close()

        page_response = client.get("/admin/creators/review", headers={"x-admin-token": settings.admin_token})
        assert page_response.status_code == 200
        assert 'Apply bulk action' in page_response.text
        assert 'name="creator_ids"' in page_response.text

        bulk = client.post(
            "/admin/creators/review/bulk",
            headers={"x-admin-token": settings.admin_token},
            data={
                "creator_ids": creator_ids,
                "decision": "approved",
                "reason": "Bulk fit for outreach",
                "status": "pending",
                "page": 1,
            },
            follow_redirects=False,
        )
        assert bulk.status_code == 303
        assert bulk.headers["location"].endswith("/admin/creators/review?status=pending&page=1")

        db = SessionLocal()
        updated = db.query(Creator).filter(Creator.id.in_(creator_ids)).order_by(Creator.id.asc()).all()
        assert len(updated) == 2
        for creator in updated:
            assert creator.outreach_status == "eligible"
            assert creator.fraud_flags["discovery_review_status"] == "approved"
            assert creator.fraud_flags["discovery_review_reason"] == "Bulk fit for outreach"
            assert "Discovery review: approved" in (creator.notes or "")
        db.close()
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_review_queue_filters_and_sort_controls(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        db = SessionLocal()
        db.add_all([
            Creator(
                handle="scoredlow",
                platform="instagram",
                score=55,
                notes="Phase 1 discovery score: 55",
                fraud_flags={
                    "phase1_sources": ["https://example.com/low"],
                    "phase1_source_platforms": ["web"],
                    "phase1_confidence": 0.42,
                    "discovery_review_status": "pending",
                },
            ),
            Creator(
                handle="scoredhigh",
                platform="instagram",
                score=91,
                notes="Phase 1 discovery score: 91",
                fraud_flags={
                    "phase1_sources": ["https://example.com/high"],
                    "phase1_source_platforms": ["youtube", "web"],
                    "phase1_emails": ["hello@example.com"],
                    "phase1_confidence": 0.91,
                    "discovery_review_status": "pending",
                },
            ),
        ])
        db.commit()
        db.close()

        response = client.get(
            "/admin/creators/review?status=pending&min_score=80&source_type=youtube&email_state=yes&confidence_band=high&sort_by=score_desc",
            headers={"x-admin-token": settings.admin_token},
        )

        assert response.status_code == 200
        html = response.text
        assert 'name="min_score"' in html
        assert 'name="source_type"' in html
        assert 'name="email_state"' in html
        assert 'name="confidence_band"' in html
        assert 'name="sort_by"' in html
        assert '@scoredhigh' in html
        assert '@scoredlow' not in html
        assert 'Confidence high' in html
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_review_redirects_preserve_active_filters(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        db = SessionLocal()
        creator = Creator(
            handle="redirectkeeper",
            platform="instagram",
            score=83,
            notes="Phase 1 discovery score: 83",
            fraud_flags={"phase1_sources": ["https://example.com/source"], "discovery_review_status": "pending"},
        )
        db.add(creator)
        db.commit()
        creator_id = creator.id
        db.close()

        response = client.post(
            f"/admin/creators/{creator_id}/review",
            headers={"x-admin-token": settings.admin_token},
            data={
                "decision": "approved",
                "reason": "Strong fit",
                "status": "pending",
                "page": 2,
                "min_score": 80,
                "source_type": "youtube",
                "email_state": "yes",
                "confidence_band": "high",
                "sort_by": "score_desc",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].endswith(
            "/admin/creators/review?status=pending&page=2&min_score=80&source_type=youtube&email_state=yes&confidence_band=high&sort_by=score_desc"
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()
