"""Influencer fraud / low-quality detection heuristics.

We keep this conservative: it does NOT accuse anyone, it only flags "low confidence".
The goal is to avoid wasting outreach on spam pages and fake engagement farms.

Output:
- fraud_score: 0..100 (higher = riskier)
- flags: dict
"""

from __future__ import annotations


def assess_fraud(creator) -> tuple[int, dict]:
    followers = getattr(creator, "followers_est", None) or 0
    posts = getattr(creator, "posts_count", None)
    er = getattr(creator, "avg_engagement_rate", None)

    flags: dict[str, object] = {}
    score = 0

    # Very low content footprint
    if posts is not None and posts < 8:
        score += 25
        flags["low_posts"] = posts

    # Suspiciously low engagement for large accounts
    if followers >= 20_000 and er is not None and er < 0.2:
        score += 35
        flags["low_er_for_size"] = er
    if followers >= 100_000 and er is not None and er < 0.1:
        score += 25
        flags["very_low_er_for_mega"] = er

    # Likely brands (we usually exclude brands from creator outreach)
    handle = (getattr(creator, "handle", "") or "").lower()
    if any(x in handle for x in ["shop", "store", "official", "boutique", "brand"]):
        score += 15
        flags["brandish_handle"] = True

    # Spammy tag patterns saved in notes
    notes = (getattr(creator, "notes", "") or "").lower()
    if any(x in notes for x in ["dm for promo", "crypto", "forex", "giveaway page", "link in bio\u2192whatsapp"]):
        score += 40
        flags["spam_signals"] = True

    score = max(0, min(100, score))
    return score, flags


def is_excludable(creator) -> tuple[bool, str | None]:
    """Hard excludes: brands, mega accounts, spam pages."""
    followers = getattr(creator, "followers_est", None)

    if getattr(creator, "is_brand", False):
        return True, "brand"
    if getattr(creator, "is_spam", False):
        return True, "spam"

    # Exclude mega accounts by default (can change later)
    if followers is not None and followers >= 250_000:
        return True, "mega_account"

    return False, None
