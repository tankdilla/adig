import asyncio
import requests
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.content_intel.scraper import load_sources, fetch_many
from agents.content_intel.parsers import (
    parse_google_trends_rss, parse_youtube_results, parse_reddit_titles
)
from agents.content_intel.ideation import build_daily_plan, generate_reel_ideas

from db_models import (
    DailyPlan,
    PostDraft,
    ApprovalStatus,
    ContentType,
)

import os

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# Import API models (shared schema approach)
# Easiest MVP: duplicate the models file in worker OR move models to /shared and import in both.
# from app_models import DailyPlan, PostDraft, ApprovalStatus, ContentType  # see note below

def run_content_intel():
    cfg = load_sources("/app/shared/trend_sources.yaml")
    rules = cfg.get("rules", {})
    sources = cfg.get("sources", [])
    max_pages_total = int(rules.get("max_pages_total", 10))

    # Gather signals
    signals = {"trends": [], "youtube": [], "reddit": []}

    # Google Trends RSS: fetch with requests (no browser needed)
    for s in sources:
        if s["type"] == "rss":
            xml = requests.get(s["url"], timeout=30).text
            signals["trends"] = parse_google_trends_rss(xml)

    # HTML sources: use Playwright CDP
    html_urls = []
    for s in sources:
        if s["type"] == "html":
            html_urls.extend(s.get("urls", []))
    html_urls = html_urls[:max_pages_total]

    pages = asyncio.run(fetch_many(html_urls, rules, max_pages_total))

    # Parse pages by source heuristic
    for p in pages:
        if p.get("error"):
            continue
        url = p["url"]
        html = p["html"]

        if "youtube.com/results" in url:
            signals["youtube"].extend(parse_youtube_results(html))
        elif "reddit.com/r/" in url:
            signals["reddit"].extend(parse_reddit_titles(html))

    # Synthesize outputs
    plan_text = build_daily_plan(signals)
    ideas = generate_reel_ideas(signals, n=5)

    print("CONTENT_INTEL: ideas_count=", len(ideas))
    print("CONTENT_INTEL: plan_len=", len(plan_text or ""))

    # Persist
    db = SessionLocal()
    try:
        today = date.today().isoformat()

        # Upsert daily plan
        existing = db.query(DailyPlan).filter(DailyPlan.plan_date == today).first()
        if not existing:
            existing = DailyPlan(plan_date=today, summary=plan_text)
        else:
            existing.summary = plan_text
        db.add(existing)

        # Insert post drafts as pending
        for idea in ideas:
            pd = PostDraft(
                content_type=ContentType.reel,
                hook=idea.get("hook"),
                caption=idea.get("caption", ""),
                hashtags=idea.get("hashtags"),
                media_notes=idea.get("media_notes"),
                status=ApprovalStatus.pending,
            )
            db.add(pd)

        db.commit()
        return {"date": today, "ideas": len(ideas)}
    finally:
        db.close()
