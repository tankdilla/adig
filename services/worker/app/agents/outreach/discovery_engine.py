"""Creator discovery engine (heuristic, no official IG API).

Strategy:
1) Hashtag page -> extract post shortcodes
2) Post page -> extract owner handle + @mentions
3) Profile page -> estimate followers/posts/bio
4) Filter out spam/brands/mega and keep niche follower band
"""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from agents.scrape import fetch_page_html

_RE_HANDLE = re.compile(r"@([A-Za-z0-9._]{2,30})")
_RE_POST_SHORTCODE = re.compile(r"/p/([A-Za-z0-9_-]{5,})/")

# Simple per-run HTML cache to avoid refetching the same url many times.
# (This resets per process restart; that's fine for a heuristic engine.)
_HTML_CACHE: dict[str, str] = {}


async def _cached_fetch(url: str) -> str:
    if url in _HTML_CACHE:
        return _HTML_CACHE[url]
    html = await fetch_page_html(url)
    _HTML_CACHE[url] = html
    return html


def _unique_handles(seq: Iterable[str]) -> list[str]:
    """Unique, normalized handles. Handles are case-insensitive on IG."""
    out: list[str] = []
    seen: set[str] = set()
    for x in seq:
        x = (x or "").strip().lstrip("@").lower()
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _unique_shortcodes(seq: Iterable[str]) -> list[str]:
    """Unique post shortcodes. IMPORTANT: shortcodes are case-sensitive -> DO NOT lower()."""
    out: list[str] = []
    seen: set[str] = set()
    for x in seq:
        x = (x or "").strip().strip("/")  # keep original casing
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _parse_intish(n: str) -> Optional[int]:
    try:
        return int(n)
    except Exception:
        return None


@dataclass
class ProfileSnapshot:
    handle: str
    followers: Optional[int] = None
    posts: Optional[int] = None
    bio: str = ""
    external_url: str = ""
    is_verified: bool = False


def _extract_profile_snapshot(html: str, handle: str) -> ProfileSnapshot:
    followers = None
    posts = None

    m = re.search(r"\"edge_followed_by\"\s*:\s*\{\s*\"count\"\s*:\s*(\d+)", html)
    if m:
        followers = _parse_intish(m.group(1))

    m = re.search(r"\"edge_owner_to_timeline_media\"\s*:\s*\{\s*\"count\"\s*:\s*(\d+)", html)
    if m:
        posts = _parse_intish(m.group(1))

    bio = ""
    m = re.search(r"\"biography\"\s*:\s*\"(.*?)\"", html)
    if m:
        bio = bytes(m.group(1), "utf-8").decode("unicode_escape", errors="ignore")

    external_url = ""
    m = re.search(r"\"external_url\"\s*:\s*\"(.*?)\"", html)
    if m:
        external_url = bytes(m.group(1), "utf-8").decode("unicode_escape", errors="ignore")

    is_verified = "\"is_verified\":true" in html

    return ProfileSnapshot(
        handle=handle,
        followers=followers,
        posts=posts,
        bio=bio,
        external_url=external_url,
        is_verified=is_verified,
    )


def _is_spammy(handle: str, bio: str) -> bool:
    h = handle.lower()
    b = (bio or "").lower()
    spam_terms = [
        "dm for promo", "dm for collab", "free money", "forex", "crypto",
        "betting", "giveaway", "cashapp", "onlyfans", "telegram",
    ]
    if any(t in h for t in ["giveaway", "promo", "free", "forex", "crypto"]):
        return True
    if any(t in b for t in spam_terms):
        return True
    return False


def _looks_like_brand(bio: str, external_url: str) -> bool:
    b = (bio or "").lower()
    if external_url and any(t in b for t in ["shop", "order", "store", "website"]):
        return True
    if ("shop" in b or "store" in b or "order" in b) and ("link in bio" in b or "shipping" in b):
        return True
    return False


def _looks_like_login_wall(html: str) -> bool:
    lower = (html or "").lower()
    # best-effort signals
    return ("login" in lower and "password" in lower and "instagram" in lower) or ("please wait a few minutes" in lower)


async def discover_handles(
    seed_hashtags: Sequence[str],
    *,
    per_hashtag_posts: int = 60,
    max_total_handles: int = 500,
    max_concurrency: int = 6,
    prefer_mentions: bool = True,
) -> list[str]:
    """
    Niche-first discovery:
    - extract MANY shortcodes from hashtag page
    - shuffle to avoid always "top posts"
    - crawl post pages with bounded concurrency
    - capture owner + caption mentions
    """
    handles: list[str] = []
    seen_posts: set[str] = set()
    sem = asyncio.Semaphore(max_concurrency)

    async def _fetch_post_handles(sc: str) -> list[str]:
        if sc in seen_posts:
            return []
        seen_posts.add(sc)

        post_url = f"https://www.instagram.com/p/{sc}/"
        async with sem:
            post_html = await _cached_fetch(post_url)

        if _looks_like_login_wall(post_html):
            return []

        found: list[str] = []

        # owner username
        m = re.search(
            r"\"owner\"\s*:\s*\{[^}]*\"username\"\s*:\s*\"([A-Za-z0-9._]{2,30})\"",
            post_html,
        )
        if m:
            found.append(m.group(1))

        # mentions in caption / page
        if prefer_mentions:
            found.extend(_RE_HANDLE.findall(post_html))

        return found

    for tag in seed_hashtags:
        tag = (tag or "").strip().lstrip("#")
        if not tag:
            continue

        hashtag_url = f"https://www.instagram.com/explore/tags/{tag}/"
        async with sem:
            html = await _cached_fetch(hashtag_url)

        if _looks_like_login_wall(html):
            continue

        # Pull more than needed, then shuffle to avoid always top posts
        shortcodes = _unique_shortcodes(_RE_POST_SHORTCODE.findall(html))
        if not shortcodes:
            continue

        random.shuffle(shortcodes)
        shortcodes = shortcodes[:per_hashtag_posts]

        tasks = [_fetch_post_handles(sc) for sc in shortcodes]
        for fut in asyncio.as_completed(tasks):
            try:
                found = await fut
            except Exception:
                continue

            handles.extend(found)
            if len(handles) >= max_total_handles:
                break

        if len(handles) >= max_total_handles:
            break

    return _unique_handles(handles)[:max_total_handles]


async def enrich_and_filter(
    handles: Sequence[str],
    *,
    follower_min: int = 2_000,
    follower_max: int = 80_000,
    hard_max_followers: int = 250_000,
    include_excluded: bool = False,
    max_concurrency: int = 6,
) -> list[dict]:
    out: list[dict] = []
    sem = asyncio.Semaphore(max_concurrency)

    async def _enrich_one(h: str) -> Optional[dict]:
        profile_url = f"https://www.instagram.com/{h}/"
        async with sem:
            html = await _cached_fetch(profile_url)

        if _looks_like_login_wall(html):
            return None

        snap = _extract_profile_snapshot(html, h)

        followers = snap.followers or 0
        is_spam = _is_spammy(h, snap.bio)
        is_brand = _looks_like_brand(snap.bio, snap.external_url)
        is_mega = followers >= hard_max_followers

        excluded = False
        exclude_reason = ""
        if is_spam:
            excluded = True
            exclude_reason = "spammy_profile"
        elif is_brand:
            excluded = True
            exclude_reason = "brand_account"
        elif is_mega:
            excluded = True
            exclude_reason = "mega_account"
        elif followers and (followers < follower_min or followers > follower_max):
            excluded = True
            exclude_reason = "outside_target_follower_range"

        item = {
            "handle": h,
            "followers_est": snap.followers,
            "posts_count": snap.posts,
            "is_brand": bool(is_brand),
            "is_spam": bool(is_spam),
            "excluded": bool(excluded),
            "exclude_reason": exclude_reason,
        }

        if excluded and not include_excluded:
            return None

        return item

    hs = _unique_handles(handles)
    tasks = [_enrich_one(h) for h in hs]

    for fut in asyncio.as_completed(tasks):
        try:
            item = await fut
        except Exception:
            continue
        if item:
            out.append(item)

    return out