import asyncio
import json
import requests
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.content_intel.scraper import load_sources, fetch_many
from agents.content_intel.parsers import (
    parse_google_trends_rss, parse_youtube_results, parse_reddit_titles
)
from agents.content_intel.ideation import build_daily_plan, generate_reel_ideas

from agents.content_intel.scheduler import build_calendar, export_csv

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
    cfg = load_sources("/shared/trend_sources.yaml")
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

    calendar = build_calendar(ideas)
    # Create a combined export (idea + calendar fields)
    rows = []
    for i, idea in enumerate(ideas):
        rows.append({
            "scheduled_at": calendar[i]["scheduled_at"],
            "hook": idea["hook"],
            "concept": idea["concept"],
            "script_outline": idea["script_outline"],
            "caption": idea["caption"],
            "hashtags": " ".join(idea["hashtags"]),
            "broll": "; ".join(idea["broll"]),
            "status": "NEEDS_APPROVAL",  # human checkbox in Notion
        })

    csv_path = export_csv(rows, "/logs/todays_ideas.csv")
    print("CONTENT_INTEL: exported csv to", csv_path)

    print("CONTENT_INTEL: ideas_count=", len(ideas))
    print("CONTENT_INTEL: plan_len=", len(plan_text or ""))

    # Persist
    db = SessionLocal()
    try:
        today = date.today().isoformat()  # matches DailyPlan.plan_date: "YYYY-MM-DD"

        # Upsert daily plan (plan_date is a string)
        existing = db.query(DailyPlan).filter(DailyPlan.plan_date == today).first()
        if not existing:
            existing = DailyPlan(plan_date=today, summary=plan_text)
        else:
            existing.summary = plan_text
        db.add(existing)

        # Insert post drafts as pending
        for i, idea in enumerate(ideas):
            hashtags_list = idea.get("hashtags") or []
            broll_list = idea.get("broll") or []
            script_outline = (idea.get("script_outline") or "").strip()

            hashtags_text = "\n".join(
                [str(h).strip() for h in hashtags_list if str(h).strip()]
            ) or None

            broll_text = "\n".join(
                [f"- {str(x).strip()}" for x in broll_list if str(x).strip()]
            )

            media_notes = "\n\n".join(
                part for part in [
                    f"Script outline:\n{script_outline}" if script_outline else "",
                    f"B-roll ideas:\n{broll_text}" if broll_text else "",
                ] if part
            ) or None

            scheduled_for = None
            if i < len(calendar):
                # calendar[i]["scheduled_at"] is ISO string in your scheduler.py
                # If you later change build_calendar to return datetime objects, this still works.
                v = calendar[i].get("scheduled_at")
                if hasattr(v, "isoformat"):
                    scheduled_for = v
                elif isinstance(v, str) and v:
                    from datetime import datetime
                    scheduled_for = datetime.fromisoformat(v)

            pd = PostDraft(
                content_type=ContentType.reel,
                hook=(idea.get("hook") or "").strip() or None,
                caption=(idea.get("caption") or "").strip(),
                hashtags=hashtags_text,
                media_notes=media_notes,
                scheduled_for=scheduled_for,
                status=ApprovalStatus.pending,
            )
            db.add(pd)

        db.commit()
        print("CONTENT_INTEL: commit OK")
        return {"date": today, "ideas": len(ideas)}

    except Exception as e:
        db.rollback()
        print("CONTENT_INTEL: DB ERROR:", repr(e))
        raise
    finally:
        db.close()


    # # Persist
    # db = SessionLocal()
    # try:
    #     today = date.today().isoformat()

    #     # Upsert daily plan
    #     existing = db.query(DailyPlan).filter(DailyPlan.plan_date == today).first()
    #     if not existing:
    #         existing = DailyPlan(plan_date=today, summary=plan_text)
    #     else:
    #         existing.summary = plan_text
    #     db.add(existing)

    #     # Insert post drafts as pending
    #     for idea in ideas:
    #         pd = PostDraft(
    #             content_type=ContentType.reel,
    #             hook=idea.get("hook"),
    #             caption=idea.get("caption", ""),
    #             hashtags=idea.get("hashtags"),
    #             media_notes=idea.get("media_notes"),
    #             status=ApprovalStatus.pending,
    #         )
    #         db.add(pd)

    #     db.commit()
    #     return {"date": today, "ideas": len(ideas)}
    # finally:
    #     db.close()
