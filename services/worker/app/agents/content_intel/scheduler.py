from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import List, Dict, Any
import csv
import os

DEFAULT_POST_TIMES = [time(11, 30), time(18, 30)]  # local-ish suggestions

def build_calendar(ideas: List[Dict[str, Any]], start_date: datetime | None = None) -> List[Dict[str, Any]]:
    start = start_date or datetime.now()
    plan = []
    day = start.date()

    for i, idea in enumerate(ideas):
        t = DEFAULT_POST_TIMES[i % len(DEFAULT_POST_TIMES)]
        scheduled_at = datetime.combine(day, t)

        plan.append(
            {
                "slot": i + 1,
                "scheduled_at": scheduled_at,
                "hook": idea.get("hook", ""),
                "concept": idea.get("concept", ""),
            }
        )

        # advance day every 2 posts
        if (i + 1) % 2 == 0:
            day = day + timedelta(days=1)

    return plan

def export_csv(rows: List[Dict[str, Any]], path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else ["empty"]

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    return path
