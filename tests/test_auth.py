"""Tests for app/auth.py — JWT extraction and validation."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def _make_jwt(payload: dict) -> str:
    import jwt

    return jwt.encode(payload, "test-secret", algorithm="HS256")


def _make_request(headers: dict):
    """Create a minimal mock request with headers."""

    class MockRequest:
        def __init__(self, headers):
            self.headers = headers

    return MockRequest(headers)


def test_missing_auth_header():
    from auth import get_current_user
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_make_request({}))
    assert exc_info.value.status_code == 401


def test_invalid_bearer_format():
    from auth import get_current_user
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_make_request({"Authorization": "Basic abc123"}))
    assert exc_info.value.status_code == 401


def test_expired_token():
    from auth import get_current_user
    from fastapi import HTTPException

    token = _make_jwt(
        {"sub": "user1", "iss": "https://auth.yral.com", "exp": int(time.time()) - 3600}
    )
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_make_request({"Authorization": f"Bearer {token}"}))
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


def test_untrusted_issuer():
    from auth import get_current_user
    from fastapi import HTTPException

    token = _make_jwt(
        {"sub": "user1", "iss": "https://evil.com", "exp": int(time.time()) + 3600}
    )
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_make_request({"Authorization": f"Bearer {token}"}))
    assert exc_info.value.status_code == 401
    assert "issuer" in exc_info.value.detail.lower()


def test_missing_sub():
    from auth import get_current_user
    from fastapi import HTTPException

    token = _make_jwt({"iss": "https://auth.yral.com", "exp": int(time.time()) + 3600})
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(_make_request({"Authorization": f"Bearer {token}"}))
    assert exc_info.value.status_code == 401


def test_valid_token():
    from auth import get_current_user

    token = _make_jwt(
        {
            "sub": "user-principal-123",
            "iss": "https://auth.yral.com",
            "exp": int(time.time()) + 3600,
        }
    )
    user_id = get_current_user(_make_request({"Authorization": f"Bearer {token}"}))
    assert user_id == "user-principal-123"


def test_lowercase_bearer():
    from auth import get_current_user

    token = _make_jwt(
        {
            "sub": "user-456",
            "iss": "https://auth.dolr.ai",
            "exp": int(time.time()) + 3600,
        }
    )
    user_id = get_current_user(_make_request({"Authorization": f"bearer {token}"}))
    assert user_id == "user-456"
