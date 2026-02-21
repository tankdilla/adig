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

from agents.scrape import fetch_page_text
from db_models import Creator
from agents.outreach.fraud_detection import is_excludable, assess_fraud


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
    tags = ((cfg.get("instagram") or {}).get("seed_hashtags")) or []
    if not tags:
        return {"ok": False, "error": "no_seed_hashtags"}

    sample = random.sample(tags, k=min(int(rotate), len(tags)))

    created = 0
    skipped = 0
    for tag in sample:
        url = f"https://www.instagram.com/explore/tags/{tag}/"
        try:
            text = await fetch_page_text(url)
        except Exception:
            continue

        handles = extract_handles(text)
        for h in handles:
            if created >= limit:
                break

            ex, reason = excluded_by_rules(h, text, cfg)
            if ex:
                skipped += 1
                continue

            existing = db.query(Creator).filter(Creator.handle == h).first()
            if existing:
                continue

            c = Creator(
                handle=h,
                platform="instagram",
                niche_tags=", ".join((cfg.get("instagram") or {}).get("target_niches") or [])[:1500],
                notes=f"Discovered via #{tag}",
                created_at=datetime.utcnow(),
            )
            # initial fraud assessment (soft)
            fraud_score, flags = assess_fraud(c)
            c.fraud_score = fraud_score
            c.fraud_flags = flags

            db.add(c)
            created += 1

        if created >= limit:
            break

    return {"ok": True, "created": created, "skipped": skipped, "tags": sample}
