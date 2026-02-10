import os
import requests
from typing import Optional

BASE = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
MODEL_MAIN = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
# MODEL_FAST = os.getenv("OLLAMA_MODEL_FAST", "qwen2.5:7b")
MODEL_FAST = os.getenv("OLLAMA_MODEL_FAST", MODEL_MAIN)

def _generate(model: str, prompt: str, system: Optional[str], temperature: float) -> str:
    payload = {
        "model": model,
        "prompt": prompt if system is None else f"{system}\n\n{prompt}",
        "stream": False,
        "options": {"temperature": temperature},
    }
    r = requests.post(f"{BASE}/api/generate", json=payload, timeout=180)
    
    if r.status_code >= 400:
        raise RuntimeError(
            f"Ollama error {r.status_code} using model '{model}': {r.text[:500]}"
        )

    return r.json().get("response", "").strip()

def think(prompt: str, system: Optional[str] = None, temperature: float = 0.4) -> str:
    """Higher-quality reasoning/planning."""
    return _generate(MODEL_MAIN, prompt, system, temperature)

def draft(prompt: str, system: Optional[str] = None, temperature: float = 0.6) -> str:
    """Fast drafts/variations."""
    return _generate(MODEL_FAST, prompt, system, temperature)
