# services/worker/app/agents/engagement/comments.py
import re
from typing import Dict, List, Optional
from agents.llm import draft

BRAND_VOICE = """You are Mary from Hello To Natural: warm, grounded, feminine, real.
Write human comments that sound like a real woman—not a bot.
No links. No emoji-only. Avoid generic praise. No repetitive phrasing.
"""

def _word_count(s: str) -> int:
    return len([w for w in re.split(r"\s+", s.strip()) if w])

def _sanitize(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    # remove leading/trailing quotes
    s = s.strip('"\'')

    # avoid @mentions spam
    s = re.sub(r"@\w+", "", s).strip()
    # avoid hashtags in comments
    s = re.sub(r"#\w+", "", s).strip()
    return s

def _passes_rules(text: str) -> bool:
    wc = _word_count(text)
    if wc < 8 or wc > 20:
        return False
    # must have a "specific" token (weak heuristic)
    if len(text) < 30:
        return False
    if "http" in text.lower():
        return False
    return True

def generate_comment(target: Dict, recent_comments: Optional[List[str]] = None) -> str:
    """
    target: { 'caption': str, 'author': str, 'url': str, 'topic_hint': str }
    recent_comments used to reduce repetition.
    """
    caption = (target.get("caption") or "")[:600]
    topic_hint = target.get("topic_hint") or "wellness"

    avoid = "\n".join(f"- {c}" for c in (recent_comments or [])[-10:]) or "- (none)"

    prompt = f"""
Write ONE Instagram comment.

Requirements:
- 8–20 words.
- References something specific from the post caption (paraphrase a phrase or theme).
- No emojis-only. Max 1 emoji total (optional).
- No links, no hashtags, no @mentions.
- Not generic ("Love this!", "So good!", "Amazing post" are banned).
- Must feel like a real woman.

Post caption:
\"\"\"{caption}\"\"\"

Topic hint: {topic_hint}

Avoid repeating these phrases/styles:
{avoid}

Return ONLY the comment text.
"""
    raw = draft(prompt, system=BRAND_VOICE, temperature=0.7)
    text = _sanitize(raw)

    # If LLM gives junk, force a second pass
    if not _passes_rules(text):
        repair_prompt = f"""
Fix this comment to meet rules (8–20 words, specific, not generic, no links/hashtags/@mentions).
Bad comment: {text}
Post caption: {caption}
Return ONLY corrected comment.
"""
        text = _sanitize(draft(repair_prompt, system=BRAND_VOICE, temperature=0.6))

    # Final hard clamp: if still invalid, return empty and mark failed upstream
    return text if _passes_rules(text) else ""