# ---------------------------------------------------------------------------
# conftest.py — shared pytest fixtures for tests/contract/.
#
# ⭐ START HERE: the contract for every Day-2 test in this folder used
# to be "depend on `client` (placeholder flag ON) or `client_flag_off`
# (placeholder flag OFF)." Day-4B added authentication as a real
# dependency on every chat + influencer handler. To keep the Day-2 test
# bodies UNCHANGED (per A2.1 — surgical wiring), both clients now bake
# a valid Bearer header into every outgoing request via TestClient's
# constructor-level `headers=` kwarg AND set up Redis + JWKS mocks so
# the shadow rig doesn't try to talk to localhost:6379 / auth.yral.com
# during test runs.
#
# THE THREE FIXTURE TIERS:
#   1. Session-scoped data (RSA keypair) — generated once per pytest run.
#   2. Function-scoped mocks (`_auth_mocks`) — Redis FakeRedis + JWKS
#      upstream stub. Runs before every test that uses `client` /
#      `client_flag_off`. Cleanup auto-reverts via monkeypatch.
#   3. Function-scoped clients (`client`, `client_flag_off`,
#      `client_no_auth`, `client_no_auth_flag_off`) — TestClient
#      instances wired for different combinations of (auth header,
#      Day-2 flag state).
#
# WHY TWO CLIENTS WITH AUTH + TWO WITHOUT?
# Day-2 happy-path tests need both auth + flag-on (so the handler runs
# its placeholder body + the auth header is accepted by the dependency).
# Day-2 503 tests need auth + flag-off (so the handler bails BEFORE
# the placeholder factory runs). The new Day-4B auth-required tests
# need the OPPOSITE: NO auth header (to assert 401 on missing /
# malformed Authorization).
#
# WHY TestClient(app, headers=...) INSTEAD OF PER-CALL HEADERS?
# Without the constructor-level default, every Day-2 test body would
# need an explicit `headers=auth_headers` parameter — touching ~32
# test bodies for a wiring change. TestClient's constructor-level
# headers are merged into every outgoing request, so existing tests
# pass unchanged.
#
# WHY MOCK BOTH REDIS AND THE UPSTREAM JWKS FETCH?
# When the dependency runs strict-path validation, it calls
# get_signing_keys() → tries Redis GET → cache miss →
# _fetch_jwks_from_upstream() → returns parsed keys. Without mocks
# the Redis GET attempts a TCP connect to localhost:6379 + the
# upstream tries to reach auth.yral.com. Both would either fail (no
# crash but ~2s timeout per test) or succeed with wrong keys for our
# test-minted tokens (strict would return bad_sig → divergence on
# every Day-2 test). Mocking both keeps tests fast + deterministic.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app import redis_client
from app.api.auth import jwks_client
from app.api.feature_flag import require_day_2_placeholder_flag_enabled
from app.main import app


# Stable IDs used across the auth-test mocks. Tests can assert against
# these without re-deriving them.
TEST_KID = "conftest-shared-kid-2026-05-18"
TEST_USER_ID = "conftest-test-user-id"
DEFAULT_ISSUER = "https://auth.yral.com"


# =========================================================================
# Helpers
# =========================================================================


class _FakeRedis:
    """Minimal dict-backed Redis stand-in for the JWKS cache layer.

    WHAT: implements `get`, `set`, `delete` matching redis-py's
          interface enough for the JWKS cache helpers in jwks_client.py.
    WHEN: instantiated by `_auth_mocks` and substituted for the real
          redis.Redis client via monkey-patching get_redis().
    WHY:  contract tests don't need a real Redis; only 3 redis-py
          methods are touched.
    """

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def get(self, key: str):  # noqa: ANN201 — signature mirrors redis-py
        return self._data.get(key)

    def set(self, key: str, value: bytes, ex: Optional[int] = None) -> bool:  # noqa: ARG002 — TTL ignored in tests
        self._data[key] = value
        return True

    def delete(self, key: str) -> int:
        return 1 if self._data.pop(key, None) is not None else 0


def _mint_valid_token(private_pem: bytes, *, sub: str = TEST_USER_ID) -> str:
    """Mint a Bearer token both legacy + strict will accept.

    WHAT: encodes a fresh RS256 JWT with the test keypair, the
          conftest-shared kid, the default issuer, sub claim, and a
          1-hour expiry.
    WHEN: called from `auth_headers` (for client default headers) +
          from new-Day-4B tests that want to vary one claim.
    WHY:  centralizes token defaults so per-test deltas are obvious
          (caller overrides only the claim under test).
    """
    now = datetime.now(timezone.utc)
    payload = {
        "iss": DEFAULT_ISSUER,
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=3600)).timestamp()),
    }
    return jwt.encode(
        payload,
        private_pem,
        algorithm="RS256",
        headers={"kid": TEST_KID},
    )


def _flag_on_noop() -> None:
    """FastAPI dependency override that no-ops the Day-2 placeholder gate.

    WHAT: returns None so handlers proceed past the
          require_day_2_placeholder_flag_enabled dependency.
    WHEN: applied via app.dependency_overrides in the `client` +
          `client_no_auth` fixtures.
    WHY:  contract tests need the flag ON to assert the placeholder
          response shape; this override is the test-side equivalent
          of setting the env var.
    """
    return None


# =========================================================================
# Session-scoped data
# =========================================================================


@pytest.fixture(scope="session")
def rsa_keypair():
    """Session-scoped RSA keypair shared by ALL auth-related tests.

    WHAT: returns (private_pem_bytes, public_key_obj). Keygen is
          ~50ms — session-scoping it saves ~2s across the full suite.
    WHEN: depended on by `_auth_mocks` (for the JWKS upstream) +
          `auth_headers` (for token minting) + any test-specific
          fixture in test_jwt_shadow.py that needs the same keypair.
    WHY:  one keypair shared across tests is faster + simpler than
          per-test keygen.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_pem, private_key.public_key()


# =========================================================================
# Function-scoped auth + Redis + JWKS mocks
# =========================================================================


@pytest.fixture
def _auth_mocks(monkeypatch, rsa_keypair):
    """Wire up FakeRedis + a stubbed upstream JWKS fetch.

    WHAT: monkey-patches app.redis_client.get_redis to return a fresh
          _FakeRedis; monkey-patches
          app.api.auth.jwks_client._fetch_jwks_from_upstream to return
          a JWKS document built from the session keypair's public key
          (so strict-path verification of conftest-minted tokens
          succeeds without network).
    WHEN: depended on (directly or transitively) by every fixture that
          builds a TestClient — `client`, `client_flag_off`,
          `client_no_auth`, `client_no_auth_flag_off`.
    WHY:  keeps tests fast + deterministic + isolated from external
          dependencies (Redis + auth.yral.com).
    """
    _, public_key = rsa_keypair

    # FakeRedis swap.
    fake = _FakeRedis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    redis_client.reset_for_testing()

    # JWKS upstream stub. Build a JSON document containing one key
    # (built from the session keypair's public_key) with the conftest
    # TEST_KID. The strict validator reads this through the Redis
    # cache layer, so a real cache hit / miss cycle still happens —
    # the upstream just doesn't hit the network.
    jwk_str = RSAAlgorithm.to_jwk(public_key)
    jwk_dict = json.loads(jwk_str)
    jwk_dict["kid"] = TEST_KID
    jwks_bytes = json.dumps({"keys": [jwk_dict]}).encode("utf-8")
    monkeypatch.setattr(jwks_client, "_fetch_jwks_from_upstream", lambda: jwks_bytes)
    jwks_client.reset_cache_for_testing()
    yield
    jwks_client.reset_cache_for_testing()
    redis_client.reset_for_testing()


@pytest.fixture
def auth_headers(rsa_keypair) -> dict:
    """A `{"Authorization": "Bearer <valid token>"}` dict.

    WHAT: mints a fresh token via the conftest keypair + the conftest
          defaults; returns the standard Authorization-header dict.
    WHEN: any test that needs to make an explicit authenticated
          request OUTSIDE the auto-headers `client` fixture — primarily
          the new Day-4B handler-auth tests that vary one component.
    WHY:  centralizes the happy-path token shape; tests override only
          what they're testing.
    """
    private_pem, _ = rsa_keypair
    token = _mint_valid_token(private_pem)
    return {"Authorization": f"Bearer {token}"}


# =========================================================================
# TestClient fixtures (auth + flag combinations)
# =========================================================================


@pytest.fixture
def client(_auth_mocks, auth_headers):
    """TestClient that AUTH-passes + has the Day-2 placeholder flag ON.

    WHAT: TestClient constructed with default Authorization header (so
          every request through this client is authenticated) +
          dependency-overrides the placeholder gate to no-op.
    WHEN: every Day-2 happy-path test uses this. Day-4B wiring did NOT
          require test-body changes because the default headers are
          merged automatically into every TestClient request.
    WHY:  back-compat with Day-2 test bodies + correct auth coverage.
    """
    app.dependency_overrides[require_day_2_placeholder_flag_enabled] = _flag_on_noop
    try:
        with TestClient(app, headers=auth_headers) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)


@pytest.fixture
def client_flag_off(_auth_mocks, auth_headers):
    """TestClient that AUTH-passes + has the Day-2 placeholder flag OFF.

    WHAT: same auth setup as `client`; no override on the placeholder
          gate (so the production-default 503 path fires).
    WHEN: Day-2 flag-off tests that assert the production-safety gate.
    WHY:  auth must succeed so the request reaches the flag dependency
          (otherwise we'd test 401 not 503).
    """
    app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)
    with TestClient(app, headers=auth_headers) as test_client:
        yield test_client


@pytest.fixture
def client_no_auth(_auth_mocks):
    """TestClient that does NOT auto-send Authorization + flag ON.

    WHAT: like `client` but without the default Authorization header.
    WHEN: new Day-4B tests that assert the dependency raises 401 on
          missing / malformed / empty Authorization — they need to be
          ABLE to send a request without auth headers.
    WHY:  proves the wiring required-auth contract: handler is
          UNREACHABLE without a valid token.
    """
    app.dependency_overrides[require_day_2_placeholder_flag_enabled] = _flag_on_noop
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)


@pytest.fixture
def client_no_auth_flag_off(_auth_mocks):
    """TestClient with NO auth header + Day-2 flag OFF.

    WHAT: same as `client_no_auth` but with the placeholder gate at
          its production default.
    WHEN: rare — used when a test needs to assert "without auth, the
          401 path is reached BEFORE the 503 path." Order-of-failure
          matters for some contract assertions.
    WHY:  defensive coverage for the dependency-evaluation order
          (auth dep evaluates BEFORE the placeholder dep because
          FastAPI evaluates Depends in declaration order).
    """
    app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)
    with TestClient(app) as test_client:
        yield test_client


# ===========================================================================
# RELATED FILES:
#   ../../app/api/feature_flag.py        — placeholder gate dependency
#   ../../app/api/dependencies.py        — require_authenticated_user (Day 4B)
#   ../../app/api/auth/dependency.py     — AuthenticatedUser dataclass + the
#                                          dual-validate authentication
#   ../../app/api/auth/jwks_client.py    — get_signing_keys() + JWKS cache
#   ../../app/redis_client.py            — get_redis() singleton
#   ../../app/main.py                    — the FastAPI app instance under test
#   test_chat_routes.py                  — uses `client` + `client_flag_off`
#                                          (Day-2 bodies unchanged after 4B)
#   test_influencer_routes.py            — same
#   test_health_routes.py                — uses `client_flag_off` (no auth dep
#                                          per F9)
#   test_handler_auth.py                 — new Day-4B file using `client_no_auth`
#                                          + `auth_headers` for the auth-edge tests
#   test_jwt_shadow.py                   — uses its own `auth_test_client`
#                                          fixture (separate FastAPI app)
# ===========================================================================
