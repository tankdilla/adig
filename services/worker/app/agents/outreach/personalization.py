"""Outreach DM personalization.

This stays intentionally lightweight: it uses structured creator fields when available
and falls back to safe generic language.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PersonalizationContext:
    top_niche: str | None = None
    recent_topic: str | None = None
    compliment: str | None = None


def build_personalization_context(creator) -> PersonalizationContext:
    # niche_tags are stored as comma-separated
    top_niche = None
    if getattr(creator, "niche_tags", None):
        top_niche = (creator.niche_tags.split(",")[0] or "").strip() or None

    # we may store hints in notes
    recent_topic = None
    notes = (getattr(creator, "notes", "") or "").lower()
    for kw in ["skincare", "body care", "wellness", "herbal", "self-care", "faith", "natural hair"]:
        if kw in notes:
            recent_topic = kw
            break

    compliment = None
    if top_niche:
        compliment = f"I love how you share about {top_niche}."
    elif recent_topic:
        compliment = f"I really enjoy your {recent_topic} content."

    return PersonalizationContext(top_niche=top_niche, recent_topic=recent_topic, compliment=compliment)


def build_personalized_dm(creator, campaign_name: str | None = None) -> str:
    ctx = build_personalization_context(creator)
    handle = getattr(creator, "handle", "").lstrip("@").strip() or "there"

    opener = ctx.compliment or "I love your content and the way you show up for your community."
    camp = f" ({campaign_name})" if campaign_name else ""

    return (
        f"Hey @{handle}!\n\n"
        f"{opener}\n\n"
        f"I’m with Hello To Natural{camp} — we do small-batch body care + wellness rituals (think shea + oils + self-care vibes). "
        f"Would you be open to a gifted collab + optional affiliate code if it feels aligned?\n\n"
        f"If yes, I can send quick details and let you choose what you’d love to try.\n\n"
        f"— Mary & Darrell"
    )
