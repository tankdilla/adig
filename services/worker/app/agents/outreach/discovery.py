"""Creator discovery.

This MVP uses public pages (hashtags / explore) and extracts handles from visible text.
It is intentionally best-effort: Instagram may throttle/obfuscate pages.

Workflow:
1) Choose seed hashtags (rotate)
2) Fetch page text via Playwright (fetch_page_text)
3) Extract @handles
4) Apply exclude rules
5) Upsert into creators table
"""

from __future__ import annotations

import os
import random
import re
from datetime import datetime

import yaml

from sqlalchemy.orm import Session

from db_models import Creator
from agents.outreach.fraud_detection import is_excludable, assess_fraud
from agents.outreach.discovery_engine import discover_handles, enrich_and_filter

MENTION_RE = re.compile(r"@([A-Za-z0-9_\.]{2,30})")

def load_targeting_config(path: str = "/shared/targeting.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def extract_handles(text: str) -> list[str]:
    if not text:
        return []
    found = [m.group(1).lstrip("@").lower() for m in MENTION_RE.finditer(text)]
    # de-dupe, preserve order
    seen = set()
    out = []
    for h in found:
        if h in seen:
            continue
        if len(h) < 2:
            continue
        seen.add(h)
        out.append(h)
    return out


def excluded_by_rules(handle: str, text: str, cfg: dict) -> tuple[bool, str | None]:
    ex = (((cfg.get("instagram") or {}).get("exclude")) or {})
    handle_contains = [x.lower() for x in (ex.get("handle_contains") or [])]
    text_contains = [x.lower() for x in (ex.get("text_contains") or [])]

    h = (handle or "").lower()
    if any(x in h for x in handle_contains):
        return True, "handle_excluded"

    t = (text or "").lower()
    if any(x in t for x in text_contains):
        return True, "text_excluded"

    return False, None

async def discover_from_hashtags(db: Session, limit: int = 200, rotate: int = 4) -> dict:
    cfg = load_targeting_config()
    ig = (cfg.get("instagram") or {})

    tags = ig.get("seed_hashtags") or []
    if not tags:
        return {"ok": False, "error": "no_seed_hashtags"}

    sample = random.sample(tags, k=min(int(rotate), len(tags)))

    follower_min = int(ig.get("follower_min") or 1_500)
    follower_max = int(ig.get("follower_max") or 35_000)
    hard_max_followers = int(ig.get("hard_max_followers") or 150_000)

    # NEW knobs (sane defaults)
    oversample_factor = float(ig.get("oversample_factor") or 6.0)  # pull more candidates than we need
    per_hashtag_posts = int(ig.get("per_hashtag_posts") or max(80, int((limit * oversample_factor) / max(1, len(sample)))))
    max_total_handles = int(ig.get("max_total_handles") or int(limit * oversample_factor))
    include_excluded_in_db = bool(ig.get("include_excluded_in_db") or False)

    # 1) Discover candidate handles (oversample so we can filter down to niche)
    handles = await discover_handles(
        seed_hashtags=sample,
        per_hashtag_posts=per_hashtag_posts,
        max_total_handles=max_total_handles,
    )

    # 2) Enrich + filter
    # If you want to STORE excluded rows too (for audit), set include_excluded_in_db=True in yaml.
    enriched = await enrich_and_filter(
        handles,
        follower_min=follower_min,
        follower_max=follower_max,
        hard_max_followers=hard_max_followers,
        include_excluded=include_excluded_in_db,
        max_concurrency=int(ig.get("enrich_concurrency") or 6),
    )

    created = 0
    updated = 0
    skipped = 0
    excluded_seen = 0
    excluded_created = 0

    # 3) Upsert into DB (insert up to `limit` *eligible* creators)
    for item in enriched:
        # If we're storing excluded rows, count them separately and don't let them consume the "limit"
        if item.get("excluded"):
            excluded_seen += 1
            if not include_excluded_in_db:
                continue

        h = (item.get("handle") or "").lower().lstrip("@")
        if not h:
            skipped += 1
            continue

        existing = db.query(Creator).filter(Creator.handle == h).first()
        if existing:
            if item.get("followers_est") is not None:
                existing.followers_est = item["followers_est"]
            if item.get("posts_count") is not None:
                existing.posts_count = item["posts_count"]

            existing.is_brand = bool(item.get("is_brand", False))
            existing.is_spam = bool(item.get("is_spam", False))

            ff = existing.fraud_flags or {}
            if item.get("exclude_reason"):
                ff.setdefault("exclude_reasons", [])
                if item["exclude_reason"] not in ff["exclude_reasons"]:
                    ff["exclude_reasons"].append(item["exclude_reason"])
            existing.fraud_flags = ff

            updated += 1
            continue

        # If it's eligible, enforce the limit
        if not item.get("excluded") and created >= limit:
            break

        c = Creator(
            handle=h,
            platform="instagram",
            followers_est=item.get("followers_est"),
            posts_count=item.get("posts_count"),
            is_brand=bool(item.get("is_brand", False)),
            is_spam=bool(item.get("is_spam", False)),
            niche_tags=", ".join(sample)[:1500],
            notes=f"Discovered via hashtags: {', '.join(sample)}",
            created_at=datetime.utcnow(),
            fraud_flags={"exclude_reason": item.get("exclude_reason") or ""},
        )

        fraud_score, flags = assess_fraud(c)
        c.fraud_score = fraud_score
        c.fraud_flags = {**(c.fraud_flags or {}), **(flags or {})}

        db.add(c)

        if item.get("excluded"):
            excluded_created += 1
        else:
            created += 1

    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "tags": sample,
        "handles_found": len(handles),
        "enriched_count": len(enriched),
        "excluded_seen": excluded_seen,
        "excluded_created": excluded_created,
        "config": {
            "follower_min": follower_min,
            "follower_max": follower_max,
            "hard_max_followers": hard_max_followers,
            "oversample_factor": oversample_factor,
            "per_hashtag_posts": per_hashtag_posts,
            "max_total_handles": max_total_handles,
            "include_excluded_in_db": include_excluded_in_db,
        },
    }