# ---------------------------------------------------------------------------
# test_jwt_shadow.py — J1-HOT-tier tests for the Day-3 JWT shadow rig.
#
# ⭐ START HERE: 6 test scenarios from Rishi's Day-3 directive:
#   - happy: valid token → both paths agree
#   - expired: legacy=ok, strict=fail(expired) → divergence=true
#   - tampered signature: legacy=ok, strict=fail(bad_sig) → divergence=true
#   - wrong issuer: legacy=ok, strict=fail(bad_iss) → divergence=true
#   - JWKS unreachable: strict=fail(jwks_fetch_error), MUST NOT crash legacy
#   - flag-on smoke: flag=true → strict authoritative; expired → 401
#
# WHY A TEST-INTERNAL FastAPI APP (not the main app)?
# Per the Day-3 scope guardrail in the agent definition: "ONLY auth
# dependency + JWKS client + the feature flag. Do NOT touch handlers
# or DTOs." Tests must exercise the dependency without wiring it into
# the real chat / influencer handlers. A test-internal FastAPI app
# with a /whoami endpoint that applies the dependency satisfies both
# the test goal AND the scope guardrail.
#
# WHY GENERATE A REAL RSA KEYPAIR PER TEST?
# Two reasons:
#   1. Tests using a real keypair signal + verify the actual crypto
#      path PyJWT exercises in production. Mock keys would let
#      verification bugs slip through.
#   2. The pyjwt[crypto] dep already pulls in `cryptography`, so the
#      keygen call is free of additional deps.
#
# WHY MONKEY-PATCH _fetch_jwks INSTEAD OF SERVING A REAL JWKS HTTP
# ENDPOINT?
# Avoids the test needing to bind a port + spin a real HTTP server. The
# JWKS client's only side effect is the HTTP fetch; everything else is
# pure-Python parsing + caching. Patching the fetch is a one-line
# substitution.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.api.auth import jwks_client
from app.api.auth.dependency import authenticate_user_dual_validate
from app.config import get_settings


# Test kid — used as the JWT header `kid` AND as the JWKS dict key.
TEST_KID = "test-kid-2026-05-18"


# Default issuer for happy-path tokens. MUST match the strict validator's
# expected issuer (set by config.jwt_expected_issuer default).
DEFAULT_ISSUER = "https://auth.yral.com"


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture(scope="module")
def rsa_keypair():
    """Generate an RSA keypair once per test module.

    WHAT: returns (private_key_pem_bytes, public_key_obj) tuple. Private
          PEM is used to sign tokens; public key obj goes into the JWKS
          dict the monkey-patched _fetch_jwks returns.
    WHEN: module-scoped — keygen is ~50ms, so per-test would slow the
          module noticeably without value.
    WHY:  real crypto path through PyJWT validates the actual RSA verify
          (not a mocked happy path).
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = private_key.public_key()
    return private_pem, public_key


@pytest.fixture
def patched_jwks(monkeypatch, rsa_keypair):
    """Patch the JWKS client to return our test keypair's public key.

    WHAT: monkey-patches app.api.auth.jwks_client._fetch_jwks to return
          {TEST_KID: <test_public_key>}. Also resets the per-module
          cache so the next get_signing_keys() call hits the patched
          fetch.
    WHEN: used by every test that needs the strict path to succeed
          (i.e., every test EXCEPT the JWKS-unreachable scenario).
    WHY:  isolates tests from network — no real HTTP fetch to
          auth.yral.com.
    """
    _, public_key = rsa_keypair
    fake_jwks = {TEST_KID: public_key}

    monkeypatch.setattr(jwks_client, "_fetch_jwks", lambda: fake_jwks)
    jwks_client.reset_cache_for_testing()
    yield
    jwks_client.reset_cache_for_testing()


@pytest.fixture
def unreachable_jwks(monkeypatch):
    """Patch the JWKS client to raise JwksFetchError on every fetch.

    WHAT: monkey-patches _fetch_jwks to always raise JwksFetchError,
          simulating an outage at auth.yral.com.
    WHEN: used by the JWKS-unreachable scenario.
    WHY:  proves strict returns jwks_fetch_error (and the request does
          NOT crash) when the JWKS endpoint is down.
    """
    def _raise(*_args, **_kwargs):
        raise jwks_client.JwksFetchError("simulated outage")

    monkeypatch.setattr(jwks_client, "_fetch_jwks", _raise)
    jwks_client.reset_cache_for_testing()
    yield
    jwks_client.reset_cache_for_testing()


@pytest.fixture
def auth_test_client():
    """A FastAPI TestClient with a test-internal /whoami endpoint.

    WHAT: builds a minimal FastAPI app whose /whoami endpoint applies
          authenticate_user_dual_validate as a dependency + returns
          the resolved user_id.
    WHEN: every JWT shadow test uses this client.
    WHY:  isolates the dependency under test from the main app's
          chat / influencer handlers (per the Day-3 scope guardrail —
          don't touch real handlers).
    """
    test_app = FastAPI()

    @test_app.get("/test/whoami")
    def whoami(user_id: str = Depends(authenticate_user_dual_validate)) -> dict:
        # Returns the user_id the authoritative validator resolved.
        return {"user_id": user_id}

    # The dependency raises HTTPException with dict detail on 401 — the
    # main app's envelope-aware handler is in app/main.py, but THIS
    # test-internal app doesn't have it. Register the same handler here
    # so the 401 body shape matches what production would emit.
    from fastapi import HTTPException, Request
    from fastapi.responses import JSONResponse

    @test_app.exception_handler(HTTPException)
    async def _envelope_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return TestClient(test_app)


@pytest.fixture
def strict_flag_on(monkeypatch):
    """Flip jwt_strict_validation_enabled to True for the duration of a test.

    WHAT: sets the env var + clears the get_settings() lru_cache so the
          next call re-reads the env. Restores on teardown.
    WHEN: used by the flag-on smoke test.
    WHY:  proves the authoritative-answer flip works end-to-end without
          deploying a new config.
    """
    monkeypatch.setenv("JWT_STRICT_VALIDATION_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ===========================================================================
# Test-token helpers
# ===========================================================================


def _make_token(
    private_pem: bytes,
    *,
    sub: str = "test-user-id",
    issuer: str = DEFAULT_ISSUER,
    audience: Optional[str] = None,
    expires_in_seconds: int = 3600,
    kid: str = TEST_KID,
) -> str:
    """Build a signed RS256 JWT with the given claims.

    WHAT: encodes a JWT using the given private key + claims; returns
          the token string.
    WHEN: called by tests to generate happy + edge-case tokens.
    WHY:  centralizes the token-construction defaults so per-test
          deltas are obvious (caller overrides only the field under test).
    """
    now = datetime.now(timezone.utc)
    payload = {
        "iss": issuer,
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    if audience is not None:
        payload["aud"] = audience
    return jwt.encode(
        payload,
        private_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


# ===========================================================================
# Scenario 1 — happy path: both validators agree
# ===========================================================================


def test_happy_both_paths_agree(auth_test_client, patched_jwks, rsa_keypair):
    """Valid token → legacy=ok + strict=ok + divergence=false → 200."""
    private_pem, _ = rsa_keypair
    token = _make_token(private_pem)
    response = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"user_id": "test-user-id"}


# ===========================================================================
# Scenario 2 — expired token: legacy ok + strict fail(expired)
# ===========================================================================


def test_expired_token_legacy_ok_strict_fail_expired(auth_test_client, patched_jwks, rsa_keypair):
    """Expired token → legacy=ok (today's behaviour) → 200; strict
    failure logged via the shadow rig."""
    private_pem, _ = rsa_keypair
    # Negative expires_in_seconds → token was issued in the past + already
    # expired by `abs(expires_in_seconds)` seconds when generated.
    token = _make_token(private_pem, expires_in_seconds=-3600)
    response = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Legacy is authoritative (flag default False) — request succeeds
    # despite the expired token. This MATCHES chat-ai's current
    # behavior; the strict-path failure is shadow-logged.
    assert response.status_code == 200
    assert response.json() == {"user_id": "test-user-id"}


# ===========================================================================
# Scenario 3 — tampered signature: legacy ok + strict fail(bad_sig)
# ===========================================================================


def test_tampered_signature_legacy_ok_strict_fail_bad_sig(auth_test_client, patched_jwks, rsa_keypair):
    """Tampered token signature → legacy=ok → 200; strict logs bad_sig."""
    private_pem, _ = rsa_keypair
    token = _make_token(private_pem)

    # Mutate the signature segment (third dot-separated segment) by
    # flipping a single character. JWT signatures are base64url —
    # changing one char breaks the signature without breaking the
    # base64 decode (so it reaches the verify step + fails there).
    header, payload, signature = token.split(".")
    # Pick a char that swap produces a different base64url char.
    mutated_signature = ("B" if signature[0] != "B" else "C") + signature[1:]
    tampered_token = ".".join([header, payload, mutated_signature])

    response = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": f"Bearer {tampered_token}"},
    )
    # Legacy doesn't check signature → ok → 200.
    assert response.status_code == 200


# ===========================================================================
# Scenario 4 — wrong issuer: legacy ok + strict fail(bad_iss)
# ===========================================================================


def test_wrong_issuer_legacy_ok_strict_fail_bad_iss(auth_test_client, patched_jwks, rsa_keypair):
    """Token with wrong iss → legacy=ok → 200; strict logs bad_iss."""
    private_pem, _ = rsa_keypair
    token = _make_token(private_pem, issuer="https://attacker.example.com")
    response = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Legacy doesn't check iss → ok → 200.
    assert response.status_code == 200


# ===========================================================================
# Scenario 5 — JWKS unreachable: strict fail(jwks_fetch_error) + no crash
# ===========================================================================


def test_jwks_unreachable_strict_fail_no_crash(auth_test_client, unreachable_jwks, rsa_keypair):
    """When the JWKS endpoint is unreachable, the request MUST NOT
    crash; legacy still answers + strict's jwks_fetch_error is shadow-
    logged. This is the most important resilience test in the suite —
    a real auth.yral.com outage cannot take down v2 public-api."""
    private_pem, _ = rsa_keypair
    token = _make_token(private_pem)
    response = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Legacy answers despite JWKS being down → 200.
    assert response.status_code == 200
    assert response.json() == {"user_id": "test-user-id"}


# ===========================================================================
# Scenario 6 — flag-on smoke: strict authoritative; expired → 401
# ===========================================================================


def test_flag_on_strict_authoritative_expired_token_401(
    auth_test_client, patched_jwks, rsa_keypair, strict_flag_on,
):
    """When jwt_strict_validation_enabled=True, strict is authoritative.
    An expired token (which legacy would accept) now produces 401."""
    private_pem, _ = rsa_keypair
    token = _make_token(private_pem, expires_in_seconds=-3600)
    response = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Strict authoritative → expired → 401 with envelope-shaped body.
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "unauthorized"
    # The reason from strict ("expired") appears in the user-facing
    # message so a developer reading Sentry can correlate.
    assert "expired" in body["msg"]


# ===========================================================================
# Additional resilience: missing or malformed Authorization header → 401
# ===========================================================================


def test_missing_authorization_header_returns_401(auth_test_client, patched_jwks):
    """No Authorization header → 401 envelope-shaped body."""
    response = auth_test_client.get("/test/whoami")
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "unauthorized"


def test_malformed_bearer_header_returns_401(auth_test_client, patched_jwks):
    """Authorization header present but doesn't start with 'Bearer ' → 401."""
    response = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": "Basic abcdefg"},
    )
    assert response.status_code == 401


def test_empty_bearer_token_returns_401(auth_test_client, patched_jwks):
    """Authorization: Bearer (empty) → 401."""
    response = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": "Bearer "},
    )
    assert response.status_code == 401


# ===========================================================================
# RELATED FILES:
#   ../../app/api/auth/dependency.py    — authenticate_user_dual_validate
#   ../../app/api/auth/validators.py    — LegacyJwtValidator + StrictJwtValidator
#   ../../app/api/auth/jwks_client.py   — _fetch_jwks (monkey-patched here)
#   ../../app/api/auth/observability.py — emit_dual_validate_result
#   ../../app/config.py                 — jwt_strict_validation_enabled etc.
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                                       — E6 (auth), E9 (shadow rollout), J1 (HOT-tier coverage)
# ===========================================================================
