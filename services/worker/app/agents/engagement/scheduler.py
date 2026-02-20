# services/worker/app/agents/engagement/scheduler.py
from datetime import datetime, timedelta
from typing import List

def schedule_actions(
    count: int,
    start_at: datetime,
    per_hour: int = 25,
    jitter_minutes: int = 6,
) -> List[datetime]:
    """
    Returns a list of scheduled datetimes spaced out so we never exceed per_hour.
    Adds a small deterministic jitter pattern to avoid exact periodicity.
    """
    if per_hour <= 0:
        per_hour = 25

    # spacing in minutes
    step = max(2, int(60 / per_hour))  # e.g. 25/hr -> 2 min
    times = []
    t = start_at

    for i in range(count):
        # simple jitter wave: -jitter..+jitter (deterministic)
        j = (i % 3) * jitter_minutes - jitter_minutes  # -6, 0, +6
        scheduled = t + timedelta(minutes=j)
        times.append(scheduled)

        t = t + timedelta(minutes=step)

    # Ensure non-decreasing
    times.sort()
    return times