from datetime import datetime, timedelta
from typing import Optional

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from .settings import settings

# Cookie name + secret
COOKIE_NAME = getattr(settings, "session_cookie_name", "h2n_admin")
SECRET = getattr(settings, "session_secret", "change_me_session_secret")

serializer = URLSafeTimedSerializer(SECRET, salt="h2n-admin-session")

def sign_session(username: str) -> str:
    # payload can be expanded later (roles, etc.)
    return serializer.dumps({"u": username})

def verify_session(token: str, max_age_seconds: int = 60 * 60 * 24 * 7) -> Optional[dict]:
    try:
        return serializer.loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
