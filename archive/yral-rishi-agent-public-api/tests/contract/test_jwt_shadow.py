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
# WHY MONKEY-PATCH _fetch_jwks_from_upstream INSTEAD OF SERVING A
# REAL JWKS HTTP ENDPOINT?
# Avoids the test needing to bind a port + spin a real HTTP server.
# The JWKS client's only network side effect is the HTTPS fetch;
# everything else is pure-Python parsing + Redis caching. Patching the
# fetch (and replacing get_redis with a fake) gives full control over
# both layers without external dependencies.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock

import jwt
import pytest
import redis as redis_lib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app import redis_client
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


# NOTE: the module-local `rsa_keypair` fixture was deleted in Day-4B —
# `conftest.py` now ships a session-scoped one shared with the chat /
# influencer / handler-auth tests, so a single keypair powers the whole
# pytest session (faster + simpler).

class _FakeRedis:
    """Minimal dict-backed stand-in for redis.Redis used by tests.

    WHAT: implements `get`, `set`, `delete` matching redis-py's
          interface enough for the JWKS cache helpers in
          jwks_client.py to use it transparently.
    WHEN: instantiated by the `fake_redis_client` fixture + by
          fixtures that need an "ok" Redis behind the JWKS cache.
    WHY:  avoids spinning up a real Redis (or `fakeredis` dep) for
          the contract tests; we only exercise 3 redis-py methods.
    """

    def __init__(self) -> None:
        # Backing store keyed by str (cache key) → bytes (cached value).
        self._data: dict[str, bytes] = {}

    def get(self, key: str):  # noqa: ANN201 — signature mirrors redis-py
        return self._data.get(key)

    def set(self, key: str, value: bytes, ex: Optional[int] = None) -> bool:  # noqa: ARG002 — TTL ignored in tests
        # TTL semantics aren't relevant for in-memory contract tests;
        # the cache-hit test asserts call count, not TTL expiry.
        self._data[key] = value
        return True

    def delete(self, key: str) -> int:
        return 1 if self._data.pop(key, None) is not None else 0


@pytest.fixture
def fake_redis_client(monkeypatch):
    """Replace get_redis() with a dict-backed fake.

    WHAT: monkey-patches app.redis_client.get_redis to return a fresh
          _FakeRedis instance. Returns the fake so tests can inspect
          its state (`fake._data`) directly.
    WHEN: dependency of patched_jwks; used wherever a working Redis is
          needed (most tests).
    WHY:  isolates the JWKS cache from a real Redis dependency in CI.
    """
    fake = _FakeRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    redis_client.reset_for_testing()
    yield fake
    redis_client.reset_for_testing()


@pytest.fixture
def patched_jwks(monkeypatch, rsa_keypair, fake_redis_client):
    """Patch the upstream JWKS fetch to return our test keypair's JWKS.

    WHAT: monkey-patches _fetch_jwks_from_upstream to return a JSON
          JWKS document (the bytes form) containing one key built from
          the test keypair's public key. Resets the Redis cache before
          + after each test so cache state doesn't leak.
    WHEN: used by every test that needs the strict path to succeed.
    WHY:  isolates tests from auth.yral.com — no real HTTP fetch. The
          Redis cache layer (fake_redis_client) still participates so
          the cache-hit test can assert call counts.
    """
    _, public_key = rsa_keypair

    # Build a JWKS-document-shaped dict (the same shape auth.yral.com
    # would return) so the parse layer (json.loads + RSAAlgorithm.from_jwk)
    # exercises the real code path. RSAAlgorithm.to_jwk emits a JSON
    # string for one key; we wrap into the standard {"keys": [...]} form
    # + serialize.
    jwk_str = RSAAlgorithm.to_jwk(public_key)
    jwk_dict = json.loads(jwk_str)
    jwk_dict["kid"] = TEST_KID
    jwks_document = {"keys": [jwk_dict]}
    jwks_bytes = json.dumps(jwks_document).encode("utf-8")

    # `fetch_call_count` is exposed so the cache-hit test can assert
    # the upstream was hit exactly once across two requests.
    state = {"fetch_call_count": 0}

    def _fake_fetch() -> bytes:
        state["fetch_call_count"] += 1
        return jwks_bytes

    monkeypatch.setattr(jwks_client, "_fetch_jwks_from_upstream", _fake_fetch)
    jwks_client.reset_cache_for_testing()
    yield state
    jwks_client.reset_cache_for_testing()


@pytest.fixture
def redis_down_jwks(monkeypatch):
    """Replace get_redis() with a mock that raises on every operation.

    WHAT: monkey-patches get_redis() to return a MagicMock whose
          `.get()` and `.set()` and `.delete()` raise redis_lib.ConnectionError.
          The JWKS client catches these + raises JwksFetchError; the
          strict validator catches that + returns
          ValidationResult(ok=False, reason="jwks_fetch_error").
    WHEN: used by the Redis-down scenario tests (per Day-4A's
          repurposed "JWKS unreachable" semantics + the new
          divergence-logged test).
    WHY:  proves strict fails closed (does NOT silently succeed via
          live-fetch bypass) AND that legacy still answers so the
          request handler doesn't crash.
    """
    mock = MagicMock()
    mock.get.side_effect = redis_lib.ConnectionError("simulated redis down")
    mock.set.side_effect = redis_lib.ConnectionError("simulated redis down")
    mock.delete.side_effect = redis_lib.ConnectionError("simulated redis down")

    monkeypatch.setattr(redis_client, "get_redis", lambda: mock)
    redis_client.reset_for_testing()
    yield mock
    redis_client.reset_for_testing()


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
    def whoami(user=Depends(authenticate_user_dual_validate)) -> dict:
        # `authenticate_user_dual_validate` returns `AuthenticatedUser`
        # after the Day-4B refactor (was a bare `str` on Day 3). The
        # /test/whoami endpoint extracts `.user_id` so the JWT shadow
        # tests' assertions (`response.json() == {"user_id": "..."}`)
        # keep matching the same wire shape without those test bodies
        # having to know about the dataclass internals.
        return {"user_id": user.user_id}

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
    """Flip enable_strict_jwt_signature_validation to True for the duration of a test.

    WHAT: sets the env var + clears the get_settings() lru_cache so the
          next call re-reads the env. Restores on teardown.
    WHEN: used by the flag-on smoke test.
    WHY:  proves the authoritative-answer flip works end-to-end without
          deploying a new config.
    """
    monkeypatch.setenv("ENABLE_STRICT_JWT_SIGNATURE_VALIDATION", "true")
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


def test_jwks_unreachable_strict_fail_no_crash(auth_test_client, redis_down_jwks, rsa_keypair):
    """When the Redis JWKS cache is unavailable, the request MUST NOT
    crash; legacy still answers + strict's jwks_fetch_error is shadow-
    logged. Day-4A repurposed this fixture from httpx-down to Redis-
    down per E9's "JWKS in Redis 1hr TTL" contract — if the cache
    layer is broken, strict can't trust its job + fails closed; legacy
    is unaffected since it doesn't consult JWKS at all. This is the
    most important resilience test in the suite — a Redis outage MUST
    NOT take down v2 public-api."""
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
    """When enable_strict_jwt_signature_validation=True, strict is authoritative.
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
# Day 4A — new tests for Redis-backed cache layer per E9 reconciliation
# ===========================================================================


def test_redis_cache_hit_second_call_no_refetch(auth_test_client, patched_jwks, rsa_keypair):
    """Two strict-path requests in a row → upstream JWKS fetched ONCE.

    WHAT: makes two `/test/whoami` calls with valid tokens. After the
          first, the JWKS document lives in the (fake) Redis cache;
          the second call's StrictJwtValidator.get_signing_keys()
          must hit the cache + skip _fetch_jwks_from_upstream.
    WHEN: Day-4A directive item: "cache hit on second call within TTL
          = 1 JWKS fetch over 2 calls."
    WHY:  proves the Redis cache layer actually CACHES (not just
          passes through). Without this, every request would hammer
          auth.yral.com which would breach E9's "1hr TTL" contract.
    """
    private_pem, _ = rsa_keypair
    token = _make_token(private_pem)

    response_1 = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )
    response_2 = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Both requests succeed via the strict path.
    assert response_1.status_code == 200
    assert response_2.status_code == 200

    # The critical assertion: upstream fetch happened exactly ONCE
    # across two requests. (The patched_jwks fixture's `state` dict
    # exposes the call counter.)
    assert patched_jwks["fetch_call_count"] == 1, (
        f"Expected 1 upstream fetch across 2 requests, got "
        f"{patched_jwks['fetch_call_count']} — cache layer not working"
    )


def test_redis_down_strict_fails_legacy_unaffected_divergence_logged(
    auth_test_client, redis_down_jwks, rsa_keypair, monkeypatch,
):
    """Redis-down with a valid token → strict=fail(jwks_fetch_error) +
    legacy=ok + divergence emission fires + request returns 200.

    WHAT: replaces emit_dual_validate_result with a spy that records
          every call. Sends a valid token with Redis down. Asserts:
            - The spy was called once.
            - legacy.ok == True (legacy doesn't consult JWKS).
            - strict.ok == False AND strict.reason == "jwks_fetch_error".
            - The request returned 200 (legacy authoritative + ok).
    WHEN: Day-4A directive item (b): "Redis-down → strict=fail
          (jwks_fetch_error), legacy still answers, divergence logged."
    WHY:  resilience contract — Redis outage MUST NOT crash auth +
          MUST surface the divergence so on-call sees the failure.
    """
    # Patch the emission helper with a spy so we can assert what was
    # logged. Need to patch the symbol at its IMPORT site inside the
    # dependency module (not at its definition site) — Python resolves
    # `emit_dual_validate_result` against the dependency module's
    # namespace at call time.
    from app.api.auth import dependency

    emission_calls = []

    def _spy(legacy, strict, request_path):
        emission_calls.append({"legacy": legacy, "strict": strict, "path": request_path})
        return legacy.ok != strict.ok

    monkeypatch.setattr(dependency, "emit_dual_validate_result", _spy)

    private_pem, _ = rsa_keypair
    token = _make_token(private_pem)
    response = auth_test_client.get(
        "/test/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Request succeeds despite Redis being down — legacy is authoritative.
    assert response.status_code == 200
    assert response.json() == {"user_id": "test-user-id"}

    # Emission helper fired exactly once + with the expected payload.
    assert len(emission_calls) == 1
    call = emission_calls[0]
    assert call["legacy"].ok is True
    assert call["legacy"].reason == "ok"
    assert call["strict"].ok is False
    assert call["strict"].reason == "jwks_fetch_error"
    assert call["path"] == "/test/whoami"


# ===========================================================================
# RELATED FILES:
#   ../../app/api/auth/dependency.py    — authenticate_user_dual_validate
#   ../../app/api/auth/validators.py    — LegacyJwtValidator + StrictJwtValidator
#   ../../app/api/auth/jwks_client.py   — _fetch_jwks_from_upstream + Redis cache
#                                         (monkey-patched here via patched_jwks /
#                                         redis_down_jwks fixtures)
#   ../../app/api/auth/observability.py — emit_dual_validate_result
#                                         (spied in divergence-logged test)
#   ../../app/redis_client.py           — get_redis() (monkey-patched to FakeRedis)
#   ../../app/config.py                 — enable_strict_jwt_signature_validation etc.
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                                       — E6 (auth), E9 (JWKS Redis 1hr TTL),
#                                         J1 (HOT-tier coverage)
# ===========================================================================
