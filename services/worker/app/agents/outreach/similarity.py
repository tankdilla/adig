"""Creator similarity utilities.

We keep this deterministic and explainable:
- Jaccard similarity over niche tags
- Optional bonus if both are in same follower bucket

This powers: finding more creators like your winners.
"""

from __future__ import annotations


def _tag_set(niche_tags: str | None) -> set[str]:
    if not niche_tags:
        return set()
    parts = [p.strip().lower() for p in niche_tags.split(",")]
    return {p for p in parts if p}


def jaccard_tags(a_tags: str | None, b_tags: str | None) -> float:
    a = _tag_set(a_tags)
    b = _tag_set(b_tags)
    if not a or not b:
        return 0.0
    inter = a.intersection(b)
    union = a.union(b)
    return float(len(inter)) / float(len(union) or 1)


def follower_bucket(followers: int | None) -> str:
    if followers is None:
        return "unknown"
    if followers < 5_000:
        return "micro"
    if followers < 20_000:
        return "micro+"
    if followers < 80_000:
        return "mid"
    if followers < 250_000:
        return "large"
    return "mega"


def similarity_score(creator_a, creator_b) -> float:
    base = jaccard_tags(getattr(creator_a, "niche_tags", None), getattr(creator_b, "niche_tags", None))
    if base <= 0:
        return 0.0
    # small bonus if same bucket (keeps outreach strategy consistent)
    if follower_bucket(getattr(creator_a, "followers_est", None)) == follower_bucket(getattr(creator_b, "followers_est", None)):
        base += 0.1
    return min(1.0, base)
