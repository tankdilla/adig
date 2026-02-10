import os
import time
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
ACTION_MODE = os.getenv("ACTION_MODE", "review")
KILL_SWITCH = os.getenv("KILL_SWITCH", "true").lower() == "true"

MAX_ACTIONS_PER_HOUR = int(os.getenv("MAX_ACTIONS_PER_HOUR", "30"))

r = redis.Redis.from_url(REDIS_URL)

def guardrails_ok() -> bool:
    if KILL_SWITCH:
        return False
    if ACTION_MODE != "live":
        return False
    # Rate limit key per hour
    hour_bucket = int(time.time() // 3600)
    key = f"actions:{hour_bucket}"
    count = r.get(key)
    if count and int(count) >= MAX_ACTIONS_PER_HOUR:
        return False
    return True

def increment_action_count(n: int = 1):
    hour_bucket = int(time.time() // 3600)
    key = f"actions:{hour_bucket}"
    r.incrby(key, n)
    r.expire(key, 7200)
