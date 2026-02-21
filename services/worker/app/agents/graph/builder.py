"""Creator graph builder.

MVP goals:
- Extract @mentions from creator profile pages and post pages
- Store mention edges in creator_edges
- Compute similarity edges from niche tags

This is designed to be resilient to scraping failures: it will skip anything it can't fetch.
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.orm import Session

from db_models import Creator, CreatorEdge, CreatorEdgeType
from agents.outreach.similarity import similarity_score


MENTION_RE = re.compile(r"@([A-Za-z0-9_\.]{2,30})")


def extract_mentions(text: str) -> set[str]:
    if not text:
        return set()
    handles = {m.group(1).lstrip("@").lower() for m in MENTION_RE.finditer(text)}
    # Filter obvious non-handles
    return {h for h in handles if h and not h.isdigit()}


def upsert_edge(db: Session, source_id: int, target_id: int, edge_type: CreatorEdgeType, weight: float, metadata: dict | None = None):
    if source_id == target_id:
        return
    row = (
        db.query(CreatorEdge)
        .filter(CreatorEdge.source_creator_id == source_id)
        .filter(CreatorEdge.target_creator_id == target_id)
        .filter(CreatorEdge.edge_type == edge_type)
        .first()
    )
    now = datetime.utcnow()
    if not row:
        row = CreatorEdge(
            source_creator_id=source_id,
            target_creator_id=target_id,
            edge_type=edge_type,
            weight=float(weight or 0.0),
            metadata=metadata,
            last_seen_at=now,
            created_at=now,
        )
    else:
        # gentle accumulation
        row.weight = float(max(row.weight or 0.0, weight or 0.0))
        row.edge_metadata = metadata or row.edge_metadata
        row.last_seen_at = now
    db.add(row)


def ensure_creator(db: Session, handle: str, platform: str = "instagram") -> Creator:
    handle = handle.lstrip("@").strip().lower()
    c = db.query(Creator).filter(Creator.handle == handle).first()
    if c:
        return c
    c = Creator(handle=handle, platform=platform, created_at=datetime.utcnow(), score=0)
    db.add(c)
    db.flush()
    return c


def build_similarity_edges(db: Session, base_creator: Creator, candidates: list[Creator], top_k: int = 25):
    scored = []
    for c in candidates:
        if c.id == base_creator.id:
            continue
        s = similarity_score(base_creator, c)
        if s <= 0:
            continue
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    for s, c in scored[:top_k]:
        upsert_edge(
            db,
            source_id=base_creator.id,
            target_id=c.id,
            edge_type=CreatorEdgeType.similarity,
            weight=float(s),
            metadata={"method": "jaccard+bucket"},
        )
