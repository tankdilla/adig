import json
from typing import Optional, Dict, Any

from agents.llm import draft

SYSTEM = """You are a content producer for Hello To Natural (H2N), a natural wellness brand.
You create practical, safe, non-medical, non-diagnostic content.
Avoid medical claims (no cures, no guarantees). Use benefit language that is cosmetic/wellness oriented.
Output must be valid JSON only.
"""

def build_prompt(hook: str, caption: str, hashtags: Optional[str], media_notes: Optional[str]) -> str:
    return f"""
Create a structured "Shoot Pack" for a short Instagram Reel.

Return JSON with exactly these keys:
- title
- hook_line (1 line)
- on_screen_text (array of 4-6 short lines)
- script (array of 5-8 bullet lines, short)
- broll (array of 6-10 shot ideas)
- filming_notes (array of 4-7 tips)
- caption_polish (string)
- first_comment (string)
- safety_checklist (array of 4-7 items)

Inputs:
HOOK:
{hook or ""}

CAPTION:
{caption or ""}

HASHTAGS (newline separated):
{hashtags or ""}

EXISTING MEDIA NOTES:
{media_notes or ""}

Rules:
- Keep it actionable and easy to film at home.
- Do not include medical claims.
- Keep hook punchy and aligned with natural body care.
- Make sure the JSON is valid (no trailing commas).
"""

def generate_shoot_pack(hook: str, caption: str, hashtags: Optional[str], media_notes: Optional[str]) -> Dict[str, Any]:
    raw = draft(build_prompt(hook, caption, hashtags, media_notes), system=SYSTEM, temperature=0.4).strip()

    # Sometimes models return text before JSON; harden by slicing first { to last }
    if "{" in raw and "}" in raw:
        raw = raw[raw.find("{"): raw.rfind("}") + 1]

    return json.loads(raw)
