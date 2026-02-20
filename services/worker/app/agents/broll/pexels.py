import os
import json
import pathlib
import requests
from typing import Any, Dict, List, Optional, Tuple

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PEXELS_BASE = "https://api.pexels.com"
ORIENTATION = os.getenv("PEXELS_ORIENTATION", "portrait")
PER_QUERY = int(os.getenv("PEXELS_PER_QUERY", "4"))
DOWNLOAD = os.getenv("PEXELS_DOWNLOAD", "1") == "1"
ASSETS_DIR = os.getenv("ASSETS_DIR", "/assets")

class PexelsError(RuntimeError):
    pass

def _headers() -> Dict[str, str]:
    if not PEXELS_API_KEY:
        raise PexelsError("PEXELS_API_KEY is not set")
    # Pexels wants raw key in Authorization (no Bearer).  :contentReference[oaicite:2]{index=2}
    return {"Authorization": PEXELS_API_KEY}

def search_videos(query: str, per_page: int = PER_QUERY) -> Dict[str, Any]:
    url = f"{PEXELS_BASE}/videos/search"
    params = {"query": query, "per_page": per_page, "orientation": ORIENTATION}
    r = requests.get(url, headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def _pick_best_file(video: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Choose a video_file that best matches portrait orientation (or requested orientation).
    Pexels returns multiple encodes in video_files.
    """
    files = video.get("video_files") or []
    if not files:
        return None

    # Prefer files that are portrait-ish if ORIENTATION=portrait
    def score(f: Dict[str, Any]) -> Tuple[int, int]:
        w = int(f.get("width") or 0)
        h = int(f.get("height") or 0)
        portrait_bonus = 1 if (ORIENTATION == "portrait" and h >= w) else 0
        # higher resolution gets higher score
        return (portrait_bonus, w * h)

    return sorted(files, key=score, reverse=True)[0]

def download_file(url: str, out_path: str) -> None:
    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

def get_broll_for_keywords(post_id: int, keywords: List[str]) -> Dict[str, Any]:
    """
    Returns a manifest:
    {
      "keywords": [...],
      "clips": [
        { "query": "...", "pexels_video_id": 123, "page_url": "...",
          "creator": "...", "creator_url": "...",
          "file_url": "...", "local_path": "...", "width":..., "height":... }
      ],
      "attribution": [...]
    }
    """
    clips: List[Dict[str, Any]] = []
    out_dir = f"{ASSETS_DIR}/broll/{post_id}"

    for kw in keywords:
        data = search_videos(kw, per_page=PER_QUERY)
        for v in data.get("videos", [])[:PER_QUERY]:
            best = _pick_best_file(v)
            if not best:
                continue

            creator = (v.get("user") or {}).get("name")
            creator_url = (v.get("user") or {}).get("url")
            page_url = v.get("url")
            file_url = best.get("link")
            width = best.get("width")
            height = best.get("height")

            item: Dict[str, Any] = {
                "query": kw,
                "pexels_video_id": v.get("id"),
                "page_url": page_url,
                "creator": creator,
                "creator_url": creator_url,
                "file_url": file_url,
                "width": width,
                "height": height,
                "local_path": None,
            }

            if DOWNLOAD and file_url:
                local_path = f"{out_dir}/{kw.replace(' ', '_')}_{v.get('id')}.mp4"
                download_file(file_url, local_path)
                item["local_path"] = local_path

            clips.append(item)

    attribution = []
    for c in clips:
        if c.get("creator") and c.get("page_url"):
            attribution.append(f'Video by {c["creator"]} on Pexels ({c["page_url"]})')

    return {
        "keywords": keywords,
        "clips": clips,
        "out_dir": out_dir if DOWNLOAD else None,
        "attribution": attribution,
    }
