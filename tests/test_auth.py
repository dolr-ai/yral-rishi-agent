"""Tests for app/auth.py — ES256 JWKS signature verification (yral-auth v2).

A local EC key stands in for the published JWKS (the module's client is
monkeypatched), so these run offline and deterministically.
"""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec


@pytest.fixture
def signing_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(autouse=True)
def _patch_jwks(signing_key, monkeypatch):
    """Serve the test's public key instead of fetching the real JWKS."""
    import auth

    pub = signing_key.public_key()
    monkeypatch.setattr(
        auth._jwks_client,
        "get_signing_key_from_jwt",
        lambda token: SimpleNamespace(key=pub),
    )


def _es256(claims: dict, key) -> str:
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "default"})


def _claims(**over) -> dict:
    c = {
        "sub": "principal-123",
        "iss": "https://auth.yral.com",
        "exp": int(time.time()) + 3600,
    }
    c.update(over)
    return c


def _req(headers: dict):
    return SimpleNamespace(headers=headers)


def test_valid_token(signing_key):
    from auth import get_current_user

    token = _es256(_claims(), signing_key)
    assert (
        get_current_user(_req({"Authorization": f"Bearer {token}"})) == "principal-123"
    )


def test_lowercase_bearer(signing_key):
    from auth import get_current_user

    token = _es256(_claims(sub="user-456"), signing_key)
    assert get_current_user(_req({"Authorization": f"bearer {token}"})) == "user-456"


def test_missing_auth_header():
    from auth import get_current_user
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        get_current_user(_req({}))
    assert exc.value.status_code == 401


def test_invalid_bearer_format():
    from auth import get_current_user
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        get_current_user(_req({"Authorization": "Basic abc123"}))
    assert exc.value.status_code == 401


def test_expired_token(signing_key):
    from auth import get_current_user
    from fastapi import HTTPException

    token = _es256(_claims(exp=int(time.time()) - 3600), signing_key)
    with pytest.raises(HTTPException) as exc:
        get_current_user(_req({"Authorization": f"Bearer {token}"}))
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower()


def test_untrusted_issuer(signing_key):
    from auth import get_current_user
    from fastapi import HTTPException

    token = _es256(_claims(iss="https://evil.com"), signing_key)
    with pytest.raises(HTTPException) as exc:
        get_current_user(_req({"Authorization": f"Bearer {token}"}))
    assert exc.value.status_code == 401
    assert "issuer" in exc.value.detail.lower()


def test_missing_sub(signing_key):
    from auth import get_current_user
    from fastapi import HTTPException

    claims = _claims()
    del claims["sub"]
    token = _es256(claims, signing_key)
    with pytest.raises(HTTPException) as exc:
        get_current_user(_req({"Authorization": f"Bearer {token}"}))
    assert exc.value.status_code == 401


def test_wrong_key_signature_rejected():
    """A token signed by a DIFFERENT key must fail signature verification."""
    from auth import get_current_user
    from fastapi import HTTPException

    attacker_key = ec.generate_private_key(ec.SECP256R1())
    token = _es256(_claims(), attacker_key)
    with pytest.raises(HTTPException) as exc:
        get_current_user(_req({"Authorization": f"Bearer {token}"}))
    assert exc.value.status_code == 401


def test_alg_confusion_hs256_rejected():
    """The classic HS256 downgrade attack must be rejected (we pin ES256)."""
    from auth import get_current_user
    from fastapi import HTTPException

    token = jwt.encode(_claims(), "secret", algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        get_current_user(_req({"Authorization": f"Bearer {token}"}))
    assert exc.value.status_code == 401


def test_verify_jwt_returns_claims(signing_key):
    """The shared helper (used by the WebSocket handler) returns verified claims."""
    from auth import verify_jwt

    token = _es256(_claims(sub="ws-user"), signing_key)
    assert verify_jwt(token)["sub"] == "ws-user"
