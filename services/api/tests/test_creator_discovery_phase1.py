from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import app, get_db, settings
from agents.outreach.phase1_discovery import (
    DiscoveryCandidate,
    merge_candidates,
    parse_curated_article,
    parse_youtube_search_results,
    score_candidate,
)


def _make_test_client(tmp_path: Path):
    db_path = tmp_path / "phase1.db"
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


def test_parse_phase1_sources_and_merge_candidates():
    youtube_html = """
    <html>
      <body>
        <script>
          var ytInitialData = {"channels": ["https://www.youtube.com/@GlowHerbalLife"]};
        </script>
        <a href="https://www.youtube.com/@GlowHerbalLife">Glow Herbal Life</a>
        <div>12K subscribers</div>
        <div>natural wellness herbal skincare routine</div>
      </body>
    </html>
    """
    article_html = """
    <html>
      <head><title>Top natural skincare creators</title></head>
      <body>
        <p>Reach out at collabs@glowherbal.com for partnerships.</p>
        <a href="https://www.instagram.com/glowherballife/">Instagram</a>
        <p>@GlowHerbalLife shares herbal tea and body care routines.</p>
      </body>
    </html>
    """

    youtube_candidates = parse_youtube_search_results(youtube_html, source_url="youtube:natural skincare influencers")
    article_candidates = parse_curated_article(article_html, source_url="https://example.com/list")
    merged = merge_candidates(youtube_candidates + article_candidates)

    assert len(merged) == 1
    candidate = merged[0]
    assert candidate.handle == "glowherballife"
    assert candidate.followers_est == 12_000
    assert candidate.emails == {"collabs@glowherbal.com"}
    assert candidate.source_platforms == {"youtube", "web"}
    assert "natural wellness" in candidate.niche_tags
    assert "body care" in candidate.niche_tags


def test_score_candidate_rewards_multi_source_contactability():
    candidate = DiscoveryCandidate(
        handle="glowherballife",
        source_platforms={"youtube", "web"},
        emails={"collabs@glowherbal.com"},
        website_url="https://glowherbal.com",
        followers_est=18_000,
        confidence_score=0.82,
        niche_tags={"natural wellness", "body care"},
    )

    score, reasons = score_candidate(candidate)

    assert score >= 85
    assert "has email" in reasons
    assert "multi source" in reasons
    assert "micro creator range" in reasons
    assert "niche alignment" in reasons


def test_admin_discovery_route_sends_phase1_task(monkeypatch, tmp_path):
    client, _SessionLocal, engine = _make_test_client(tmp_path)
    sent = {}

    def fake_send_task(name, kwargs):
        sent["name"] = name
        sent["kwargs"] = kwargs

    monkeypatch.setattr("app.main.CREATOR_DISCOVERY_TASK", "tasks.creator_discovery_phase1")
    monkeypatch.setattr("app.main.celery_client.send_task", fake_send_task)

    try:
        response = client.post(
            "/admin/creators/discover",
            headers={"x-admin-token": settings.admin_token},
            data={
                "limit": "75",
                "rotate": "4",
                "max_google_results": "6",
                "queries": "natural skincare influencers\nblack wellness creators",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/admin/creators"
        assert sent["name"] == "tasks.creator_discovery_phase1"
        assert sent["kwargs"] == {
            "limit": 75,
            "queries": ["natural skincare influencers", "black wellness creators"],
            "max_google_results": 6,
        }
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()
