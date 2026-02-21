"""Viral pattern detection (MVP).

This creates a daily report from cached CreatorPost.extracted fields.
It does NOT scrape metrics itself (that can be added later).

Report includes:
- most common hooks
- most common CTAs
- most common topics/hashtags
"""

from __future__ import annotations

from collections import Counter


def _norm(s: str | None) -> str | None:
    if not s:
        return None
    s = " ".join(str(s).split()).strip()
    return s[:140] if s else None


def build_report(posts: list[dict]) -> dict:
    hooks = Counter()
    ctas = Counter()
    topics = Counter()

    for p in posts:
        ext = p.get("extracted") or {}
        hook = _norm(ext.get("hook"))
        cta = _norm(ext.get("cta"))
        if hook:
            hooks[hook] += 1
        if cta:
            ctas[cta] += 1
        for t in (ext.get("topics") or []):
            t = _norm(t)
            if t:
                topics[t] += 1

    return {
        "top_hooks": hooks.most_common(10),
        "top_ctas": ctas.most_common(10),
        "top_topics": topics.most_common(15),
        "post_sample": len(posts),
    }
