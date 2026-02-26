from __future__ import annotations
import re
from datetime import datetime, timedelta, date
from sqlalchemy.orm import Session
from sqlalchemy import func

from db_models import Creator, CreatorMetricsDaily, CreatorSignal
from agents.outreach.discovery_engine import _extract_profile_snapshot, _extract_profile_shortcodes, _RE_POST_SHORTCODE
from agents.scrape import fetch_page_html

_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{2,50})")

DEFAULT_NICHE_KEYWORDS = [
    # skincare/bodycare
    "body butter","shea","mango butter","body oil","natural skincare","melanin skincare",
    "dry skin","eczema","glowing skin","skin barrier","moisture","self care","selfcare",
    # wellness
    "holistic","herbal","tea","gut health","hormone","pcos","pre diabetic","blood sugar",
    # brand adjacency
    "black owned","black-owned","plant based","plantbased","faith","christian",
]

def _normalize(txt: str) -> str:
    return (txt or "").lower()

def _keyword_score(text: str, keywords: list[str]) -> float:
    t = _normalize(text)
    score = 0.0
    for kw in keywords:
        if kw in t:
            # longer phrases get slightly more weight
            score += 1.0 + min(1.0, len(kw) / 20.0)
    return score

async def snapshot_creator(db: Session, creator: Creator) -> None:
    """Fetch profile html, store a daily snapshot row (for growth)."""
    url = f"https://www.instagram.com/{creator.handle}/"
    html = await fetch_page_html(url)
    snap = _extract_profile_snapshot(html, creator.handle)

    today = date.today()
    row = (
        db.query(CreatorMetricsDaily)
        .filter(CreatorMetricsDaily.creator_id == creator.id)
        .filter(CreatorMetricsDaily.snapshot_date == today)
        .first()
    )
    if not row:
        row = CreatorMetricsDaily(
            creator_id=creator.id,
            snapshot_date=today,
            followers_est=snap.followers,
            posts_count=snap.posts,
        )
        db.add(row)
    else:
        row.followers_est = snap.followers
        row.posts_count = snap.posts

def _growth_pct(new: int | None, old: int | None) -> float | None:
    if not new or not old or old <= 0:
        return None
    return (new - old) / float(old)

def update_growth_fields(db: Session, creator: Creator) -> None:
    """Compute 7d and 30d growth using daily snapshot table."""
    today = date.today()
    d7 = today - timedelta(days=7)
    d30 = today - timedelta(days=30)

    newest = (
        db.query(CreatorMetricsDaily)
        .filter(CreatorMetricsDaily.creator_id == creator.id)
        .order_by(CreatorMetricsDaily.snapshot_date.desc())
        .first()
    )
    if not newest:
        creator.growth_7d = None
        creator.growth_30d = None
        return

    s7 = (
        db.query(CreatorMetricsDaily)
        .filter(CreatorMetricsDaily.creator_id == creator.id)
        .filter(CreatorMetricsDaily.snapshot_date <= d7)
        .order_by(CreatorMetricsDaily.snapshot_date.desc())
        .first()
    )
    s30 = (
        db.query(CreatorMetricsDaily)
        .filter(CreatorMetricsDaily.creator_id == creator.id)
        .filter(CreatorMetricsDaily.snapshot_date <= d30)
        .order_by(CreatorMetricsDaily.snapshot_date.desc())
        .first()
    )

    creator.growth_7d = _growth_pct(newest.followers_est, s7.followers_est) if s7 else None
    creator.growth_30d = _growth_pct(newest.followers_est, s30.followers_est) if s30 else None

async def compute_niche_signals(
    db: Session,
    creator: Creator,
    *,
    keywords: list[str] = DEFAULT_NICHE_KEYWORDS,
    posts_to_scan: int = 8,
) -> float:
    """Scan bio + recent posts for niche keywords and hashtags. Store evidence rows; return niche_score."""
    profile_url = f"https://www.instagram.com/{creator.handle}/"
    prof_html = await fetch_page_html(profile_url)
    snap = _extract_profile_snapshot(prof_html, creator.handle)

    # Clear older signals for this creator (keep it simple)
    db.query(CreatorSignal).filter(CreatorSignal.creator_id == creator.id).delete()

    score = 0.0

    # BIO signal
    bio_score = _keyword_score(snap.bio, keywords)
    if bio_score > 0:
        db.add(CreatorSignal(
            creator_id=creator.id,
            signal_type="bio",
            signal_text=snap.bio[:1200],
            weight=float(bio_score),
            source_url=profile_url,
        ))
        score += bio_score * 1.5  # bio is strong intent

    # RECENT POSTS: extract shortcodes from profile HTML then crawl posts
    shortcodes = _extract_profile_shortcodes(prof_html, max_posts=posts_to_scan)
    for sc in shortcodes:
        post_url = f"https://www.instagram.com/p/{sc}/"
        try:
            post_html = await fetch_page_html(post_url)
        except Exception:
            continue

        # crude: search page for captions/hashtags/keywords
        post_score = _keyword_score(post_html, keywords)
        hashtags = _HASHTAG_RE.findall(post_html)
        hashtag_score = 0.0
        for h in hashtags:
            if _keyword_score(h, keywords) > 0:
                hashtag_score += 0.5

        if post_score > 0:
            db.add(CreatorSignal(
                creator_id=creator.id,
                signal_type="post",
                signal_text=f"Matched keywords on post {sc}",
                weight=float(post_score),
                source_url=post_url,
            ))
            score += post_score

        if hashtag_score > 0:
            db.add(CreatorSignal(
                creator_id=creator.id,
                signal_type="hashtag",
                signal_text=", ".join(hashtags[:25]),
                weight=float(hashtag_score),
                source_url=post_url,
            ))
            score += hashtag_score

    return float(score)

def lexical_similarity(a: str, b: str) -> float:
    """Always-works similarity (no embeddings)."""
    a = set(re.findall(r"[a-z0-9]{3,}", (a or "").lower()))
    b = set(re.findall(r"[a-z0-9]{3,}", (b or "").lower()))
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))

def best_partner_similarity(db: Session, creator: Creator) -> float:
    """
    Compare this creator against the set you mark as is_partner=True.
    Uses Creator.notes/niche_tags + signal text as the corpus.
    """
    partners = db.query(Creator).filter(Creator.is_partner.is_(True)).limit(50).all()
    if not partners:
        return 0.0

    def corpus(c: Creator) -> str:
        sigs = db.query(CreatorSignal).filter(CreatorSignal.creator_id == c.id).all()
        sig_txt = " ".join((s.signal_text or "") for s in sigs)
        return " ".join([
            c.handle or "",
            c.niche_tags or "",
            c.notes or "",
            sig_txt,
        ])

    me = corpus(creator)
    best = 0.0
    for p in partners:
        best = max(best, lexical_similarity(me, corpus(p)))
    return best