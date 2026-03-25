from auth import sign_session, verify_session
from settings import settings


def test_sign_and_verify_round_trip():
    token = sign_session("mary")
    payload = verify_session(token)
    assert payload is not None
    assert payload["u"] == "mary"


def test_verify_session_rejects_tampering():
    token = sign_session("mary") + "tampered"
    assert verify_session(token) is None


def test_cookie_name_comes_from_settings():
    assert settings.session_cookie_name == "test_cookie"
