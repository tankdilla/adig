"""Audience overlap scoring.

True audience overlap requires platform analytics. As an MVP we approximate overlap by:
- niche tag Jaccard similarity
- shared neighbors in creator graph (if edges exist)

Returns a 0..1 score.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from db_models import CreatorEdge, CreatorEdgeType
from agents.outreach.similarity import jaccard_tags


def _neighbors(db: Session, creator_id: int) -> set[int]:
    rows = (
        db.query(CreatorEdge.target_creator_id)
        .filter(CreatorEdge.source_creator_id == creator_id)
        .filter(CreatorEdge.edge_type.in_([CreatorEdgeType.mention, CreatorEdgeType.co_mentioned]))
        .limit(500)
        .all()
    )
    return {r[0] for r in rows if r and r[0]}


def overlap_score(db: Session, creator_a, creator_b) -> float:
    tag_sim = jaccard_tags(getattr(creator_a, "niche_tags", None), getattr(creator_b, "niche_tags", None))

    # graph neighbor overlap (optional)
    try:
        na = _neighbors(db, creator_a.id)
        nb = _neighbors(db, creator_b.id)
        if na and nb:
            inter = len(na.intersection(nb))
            union = len(na.union(nb)) or 1
            graph_sim = inter / union
        else:
            graph_sim = 0.0
    except Exception:
        graph_sim = 0.0

    # weighted blend
    return min(1.0, (0.7 * tag_sim) + (0.3 * graph_sim))
