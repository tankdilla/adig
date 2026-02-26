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
from agents.outreach.discovery_engine import discover_handles, enrich_and_filter, discover_related_handles

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

    # ---- config helpers (avoid bool(x or True) bugs) ----
    def _get_bool(key: str, default: bool) -> bool:
        v = ig.get(key, default)
        return bool(v) if isinstance(v, bool) else bool(v)  # tolerate None/0/1/"true"

    def _get_int(key: str, default: int) -> int:
        try:
            return int(ig.get(key, default))
        except Exception:
            return default

    def _get_float(key: str, default: float) -> float:
        try:
            return float(ig.get(key, default))
        except Exception:
            return default

    enable_related = _get_bool("enable_related_expansion", True)
    related_seed_count = _get_int("related_seed_count", 25)
    related_per_seed_posts = _get_int("related_per_seed_posts", 12)
    related_max_total_handles = _get_int("related_max_total_handles", int(limit * 8))
    related_enrich_concurrency = _get_int("related_enrich_concurrency", 6)

    tags = ig.get("seed_hashtags") or []
    if not tags:
        return {"ok": False, "error": "no_seed_hashtags"}

    sample = random.sample(tags, k=min(int(rotate), len(tags)))

    follower_min = _get_int("follower_min", 1_500)
    follower_max = _get_int("follower_max", 35_000)
    hard_max_followers = _get_int("hard_max_followers", 150_000)

    oversample_factor = _get_float("oversample_factor", 6.0)
    per_hashtag_posts = _get_int(
        "per_hashtag_posts",
        max(80, int((limit * oversample_factor) / max(1, len(sample)))),
    )
    max_total_handles = _get_int("max_total_handles", int(limit * oversample_factor))
    include_excluded_in_db = _get_bool("include_excluded_in_db", False)
    enrich_concurrency = _get_int("enrich_concurrency", 6)

    # --- results ---
    created = 0
    updated = 0
    skipped = 0
    excluded_seen = 0
    excluded_created = 0

    related = {
        "ok": False,
        "seed_count": 0,
        "handles_found": 0,
        "enriched_count": 0,
        "created": 0,
        "updated": 0,
    }

    # 1) Discover candidate handles (oversample so we can filter down to niche)
    handles = await discover_handles(
        seed_hashtags=sample,
        per_hashtag_posts=per_hashtag_posts,
        max_total_handles=max_total_handles,
    )

    # 2) Enrich + filter
    enriched = await enrich_and_filter(
        handles,
        follower_min=follower_min,
        follower_max=follower_max,
        hard_max_followers=hard_max_followers,
        include_excluded=include_excluded_in_db,
        max_concurrency=enrich_concurrency,
    )

    # 3) Upsert hashtag-discovered creators (cap eligible new inserts to `limit`)
    for item in enriched:
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

        # enforce limit for eligible inserts
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

    # Make new rows visible for seed selection (without committing)
    try:
        db.flush()
    except Exception:
        pass

    # 4) Related expansion (run ONCE)
    if enable_related:
        seed_rows = (
            db.query(Creator)
            .filter(Creator.is_brand.is_(False))
            .filter(Creator.is_spam.is_(False))
            .order_by(
                Creator.score.desc().nullslast(),
                Creator.followers_est.desc().nullslast(),
                Creator.created_at.desc(),
            )
            .limit(related_seed_count)
            .all()
        )
        seed_handles = [c.handle for c in seed_rows if c.handle]
        related["seed_count"] = len(seed_handles)

        if seed_handles:
            related_handles = await discover_related_handles(
                seed_handles,
                per_seed_posts=related_per_seed_posts,
                max_total_handles=related_max_total_handles,
                max_concurrency=related_enrich_concurrency,
            )
            related["handles_found"] = len(related_handles)

            related_enriched = await enrich_and_filter(
                related_handles,
                follower_min=follower_min,
                follower_max=follower_max,
                hard_max_followers=hard_max_followers,
                include_excluded=False,
                max_concurrency=related_enrich_concurrency,
            )
            related["enriched_count"] = len(related_enriched)

            r_created = 0
            r_updated = 0

            # cap related inserts (separate cap so related can add more)
            add_cap = limit

            for item in related_enriched:
                if r_created >= add_cap:
                    break

                h = (item.get("handle") or "").lower().lstrip("@")
                if not h:
                    continue

                existing = db.query(Creator).filter(Creator.handle == h).first()
                if existing:
                    if item.get("followers_est") is not None:
                        existing.followers_est = item["followers_est"]
                    if item.get("posts_count") is not None:
                        existing.posts_count = item["posts_count"]
                    existing.is_brand = bool(item.get("is_brand", False))
                    existing.is_spam = bool(item.get("is_spam", False))
                    r_updated += 1
                    continue

                c = Creator(
                    handle=h,
                    platform="instagram",
                    followers_est=item.get("followers_est"),
                    posts_count=item.get("posts_count"),
                    is_brand=bool(item.get("is_brand", False)),
                    is_spam=bool(item.get("is_spam", False)),
                    niche_tags="related_expansion"[:1500],
                    notes=f"Related expansion from seeds: {', '.join(seed_handles[:5])}{'...' if len(seed_handles) > 5 else ''}",
                    created_at=datetime.utcnow(),
                    fraud_flags={},
                )
                fraud_score, flags = assess_fraud(c)
                c.fraud_score = fraud_score
                c.fraud_flags = {**(c.fraud_flags or {}), **(flags or {})}

                db.add(c)
                r_created += 1

            related.update({"ok": True, "created": r_created, "updated": r_updated})

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
        "related": related,
        "config": {
            "follower_min": follower_min,
            "follower_max": follower_max,
            "hard_max_followers": hard_max_followers,
            "oversample_factor": oversample_factor,
            "per_hashtag_posts": per_hashtag_posts,
            "max_total_handles": max_total_handles,
            "include_excluded_in_db": include_excluded_in_db,
            "enable_related_expansion": enable_related,
            "related_seed_count": related_seed_count,
            "related_per_seed_posts": related_per_seed_posts,
            "related_max_total_handles": related_max_total_handles,
            "related_enrich_concurrency": related_enrich_concurrency,
        },
    }