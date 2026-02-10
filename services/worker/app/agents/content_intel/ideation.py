import os
from datetime import date
from typing import Dict, List
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

def generate_reel_ideas(signals: Dict, n: int = 5) -> List[Dict]:
    prompt = f"""
Create {n} Reel ideas for today based on these public trend signals.
Each idea must include:
- hook (<= 90 characters)
- caption (80–180 words)
- hashtags (10–18, newline separated)
- media_notes (what to film + on-screen text)
- content_type: reel

Signals:
Google Trends: {signals.get("trends", [])[:12]}
YouTube: {signals.get("youtube", [])[:12]}
Reddit: {signals.get("reddit", [])[:12]}

Constraints:
- No medical claims. Use language like "may help", "some people find", "talk to your clinician".
- Stay consistent with Hello To Natural (plant-forward, natural living, faith-friendly encouragement).
- Avoid fear hooks. Use curiosity + hope.
Return as JSON array of objects.
"""
    raw = draft(prompt, system=BRAND_SYSTEM, temperature=0.6)

    # Keep MVP simple: you can add a robust JSON parser/repair later.
    # For now, expect the model returns JSON.
    import json
    return json.loads(raw)
