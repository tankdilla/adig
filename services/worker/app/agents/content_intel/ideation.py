import os

import json
import re

from datetime import date
from typing import Any, Dict, List, Optional
from agents.llm import think, draft

BRAND_SYSTEM = """You are a content strategist for Hello To Natural, a faith-friendly natural wellness brand.
Create Reel ideas that are practical, kind, and credible.
Avoid medical claims, avoid diagnosing, and avoid fear-based language.
Use simple, warm, sister-to-sister tone.
Keep Reels: 12–25 seconds.
Always include a clear CTA: Save / Share / Comment keyword.
"""

def build_daily_plan(signals: Dict) -> str:
    prompt = f"""
Using the signals below, identify 2-3 themes that are trending today and how Hello To Natural can respond.

Signals (summarized):
- Google Trends: {signals.get("trends", [])[:10]}
- YouTube topics: {signals.get("youtube", [])[:10]}
- Reddit topics: {signals.get("reddit", [])[:10]}

Output a short daily plan with:
1) Today's themes
2) 1 Reel format recommendation (face-forward, b-roll, text-over)
3) 1 CTA recommendation
4) A reminder of safety (no medical claims)
"""
    return think(prompt, system=BRAND_SYSTEM, temperature=0.3)

def _normalize_ideas(items: Any, n: int) -> List[Dict[str, Any]]:
    """
    Guarantees a list of dicts with keys we expect.
    Also coerces list fields into lists, strips strings, and truncates to n.
    """
    if not isinstance(items, list):
        return []

    out: List[Dict[str, Any]] = []
    for it in items[:n]:
        if not isinstance(it, dict):
            continue

        broll = it.get("broll", [])
        if isinstance(broll, str):
            broll = [b.strip() for b in broll.split(",") if b.strip()]
        if not isinstance(broll, list):
            broll = []

        hashtags = it.get("hashtags", [])
        if isinstance(hashtags, str):
            # allow "#a #b" or "a,b"
            hashtags = re.split(r"[\s,]+", hashtags.strip())
            hashtags = [h for h in hashtags if h]
        if not isinstance(hashtags, list):
            hashtags = []

        idea = {
            "hook": str(it.get("hook", "")).strip(),
            "concept": str(it.get("concept", "")).strip(),
            "script_outline": str(it.get("script_outline", "")).strip(),
            "broll": broll,
            "caption": str(it.get("caption", "")).strip(),
            "hashtags": hashtags,
        }

        # require minimally useful fields
        if idea["hook"] and idea["concept"]:
            out.append(idea)

    return out

# ---------- main function ----------

def generate_reel_ideas(signals: Dict[str, Any], n: int = 5) -> List[Dict[str, Any]]:
    """
    Returns a list of n reel ideas as strict JSON (with repair pass + normalization).
    """

    prompt = f"""
You are generating Instagram Reel ideas for the brand Hello To Natural (H2N).
Use the trend signals below as inspiration, but keep it brand-safe and realistic.

Trend Signals (summarized):
{json.dumps(signals, indent=2)[:8000]}

Requirements:
- Generate exactly {n} ideas.
- Each idea must be UNIQUE.
- Content must be safe and avoid medical claims.
- Make them practical, engaging, and aligned with H2N body care / self-care vibe.
- Tone: fun, sexy, playful, confident, but tasteful.

JSON OUTPUT CONTRACT (strict):
Return ONLY valid JSON.
No markdown, no code fences, no commentary.
Output must be a JSON array of {n} objects, each with EXACT keys:
- hook (string)
- concept (string)
- script_outline (string)
- broll (array of strings)
- caption (string)
- hashtags (array of strings)
"""

    raw = draft(prompt, system=BRAND_SYSTEM, temperature=0.6)

    # Helpful debug if it fails in logs
    if not raw or not raw.strip():
        # Try one repair immediately
        raw = draft(
            f"Return ONLY valid JSON per the contract. Generate {n} ideas now.",
            system=BRAND_SYSTEM,
            temperature=0.2,
        )

    try:
        parsed = _safe_json_load(raw)
    except Exception:
        # Repair pass: ask model to convert its own output into valid JSON only
        repair_prompt = f"""
You returned invalid JSON.

Fix it and return ONLY valid JSON (no markdown, no commentary).
It must be a JSON array of {n} objects with EXACT keys:
hook, concept, script_outline, broll, caption, hashtags.

Here is your previous output:
{raw}
"""
        fixed = draft(repair_prompt, system=BRAND_SYSTEM, temperature=0.2)
        parsed = _safe_json_load(fixed)

    ideas = _normalize_ideas(parsed, n=n)

    # Last resort fallback: if model gave parsed JSON but empty/invalid structure
    if len(ideas) < n:
        # Fill missing with a safe minimal fallback rather than crashing the pipeline
        while len(ideas) < n:
            ideas.append(
                {
                    "hook": "Glow check: what’s in your body butter?",
                    "concept": "Quick ingredient + texture education with a fun ASMR whip shot.",
                    "script_outline": "Show the butter texture, explain 1–2 key benefits, end with CTA to shop.",
                    "broll": ["whipping butter close-up", "hand application", "product label shot"],
                    "caption": "Soft skin season is here. Which butter are you grabbing today?",
                    "hashtags": ["#hellotonatural", "#bodybutter", "#selfcare", "#skincare"],
                }
            )
        ideas = ideas[:n]

    return ideas


def _strip_fences(s: str) -> str:
    s = s.strip()
    # Remove ```json ... ``` or ``` ... ```
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()

def _extract_json_blob(s: str) -> str:
    s = _strip_fences(s)

    # If it's already pure JSON, great.
    if s.startswith("{") or s.startswith("["):
        return s

    # Try to find the first JSON array or object in the text
    m = re.search(r"(\[.*\]|\{.*\})", s, flags=re.DOTALL)
    if m:
        return m.group(1).strip()

    return ""  # nothing usable

def _safe_json_load(raw: str):
    blob = _extract_json_blob(raw)
    if not blob:
        raise ValueError("No JSON found in model output")
    return json.loads(blob)
