from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db_models import ApprovalStatus, Creator, EngagementQueueItem, OutreachDraft, PostDraft
from app.main import app, get_db, settings
from app.db import Base


def _make_test_client(tmp_path: Path):
    db_path = tmp_path / "integration.db"
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


def test_login_flow_and_root_redirect(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        root_response = client.get("/", follow_redirects=False)
        assert root_response.status_code == 303
        assert root_response.headers["location"] == "/login"

        unauthorized = client.get("/admin")
        assert unauthorized.status_code == 401

        invalid_login = client.post("/login", data={"username": "Mary", "token": "wrong-token"})
        assert invalid_login.status_code == 200
        assert "Invalid token" in invalid_login.text

        login = client.post(
            "/login",
            data={"username": "Mary", "token": settings.admin_token},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/admin"
        assert "test_cookie=" in login.headers.get("set-cookie", "")

        admin = client.get("/admin")
        assert admin.status_code == 200
        assert "Signed in as <b>Mary</b>" in admin.text
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_admin_dashboard_counts_from_database(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        with SessionLocal() as db:
            db.add_all(
                [
                    PostDraft(content_type="reel", caption="pending post", status=ApprovalStatus.pending),
                    PostDraft(content_type="story", caption="approved post", status=ApprovalStatus.approved),
                    EngagementQueueItem(target_url="https://example.com/p/1", status=ApprovalStatus.pending),
                ]
            )
            creator_a = Creator(handle="creator_a", score=70, fraud_score=5)
            creator_b = Creator(handle="creator_b", score=71, fraud_score=5)
            db.add_all([creator_a, creator_b])
            db.flush()
            db.add_all(
                [
                    OutreachDraft(creator_id=creator_a.id, message="hello", status=ApprovalStatus.pending),
                    OutreachDraft(creator_id=creator_b.id, message="sent", status=ApprovalStatus.approved),
                ]
            )
            db.commit()

        response = client.get("/admin", headers={"x-admin-token": settings.admin_token})
        assert response.status_code == 200
        assert "Posts: 1" in response.text
        assert "Engagement: 1" in response.text
        assert "Outreach: 1" in response.text
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def test_creators_page_filters_out_low_quality_rows(tmp_path):
    client, SessionLocal, engine = _make_test_client(tmp_path)
    try:
        with SessionLocal() as db:
            db.add_all(
                [
                    Creator(handle="greatcreator", score=88, fraud_score=10, followers_est=12000),
                    Creator(handle="toosmall", score=12, fraud_score=5, followers_est=200),
                    Creator(handle="toorisky", score=90, fraud_score=85, followers_est=50000),
                    Creator(handle="brandacct", score=91, fraud_score=0, is_brand=True),
                    Creator(handle="spamacct", score=91, fraud_score=0, is_spam=True),
                ]
            )
            db.commit()

        response = client.get(
            "/admin/creators?min_score=50&max_fraud=70",
            headers={"x-admin-token": settings.admin_token},
        )
        assert response.status_code == 200
        assert "@greatcreator" in response.text
        assert "@toosmall" not in response.text
        assert "@toorisky" not in response.text
        assert "@brandacct" not in response.text
        assert "@spamacct" not in response.text
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()
