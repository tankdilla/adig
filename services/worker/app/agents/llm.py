import os
import requests
from typing import Optional

import time
from requests.exceptions import ReadTimeout, ConnectionError

BASE = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
MODEL_MAIN = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
# MODEL_FAST = os.getenv("OLLAMA_MODEL_FAST", "qwen2.5:7b")
MODEL_FAST = os.getenv("OLLAMA_MODEL_FAST", MODEL_MAIN)

TIMEOUT = int(os.getenv("LLM_TIMEOUT_SECONDS", "600"))  # default 10 min

def _generate(model: str, prompt: str, system: Optional[str], temperature: float) -> str:
    payload = {
        "model": model,
        "prompt": prompt if system is None else f"{system}\n\n{prompt}",
        "stream": False,
        "options": {"temperature": temperature},
    }
    r = _post_with_retry(f"{BASE}/api/generate", payload, TIMEOUT, retries=2)
    
    if r.status_code >= 400:
        raise RuntimeError(
            f"Ollama error {r.status_code} using model '{model}': {r.text[:500]}"
        )

    return r.json().get("response", "").strip()

def think(prompt: str, system: Optional[str] = None, temperature: float = 0.4) -> str:
    """Higher-quality reasoning/planning."""
    return _generate(MODEL_MAIN, prompt, system, temperature)

def draft(prompt: str, system: Optional[str] = None, temperature: float = 0.6) -> str:
    try:
        return _generate(MODEL_FAST, prompt, system, temperature)
    except ReadTimeout:
        return "LLM timeout — fallback: propose 3 short reel hooks + CTA (manual review required)."

def _post_with_retry(url: str, payload: dict, timeout: int, retries: int = 2):
    delay = 2
    for attempt in range(retries + 1):
        try:
            return requests.post(url, json=payload, timeout=timeout)
        except (ReadTimeout, ConnectionError):
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 2
