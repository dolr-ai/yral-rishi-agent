# ---------------------------------------------------------------------------
# test_handler_auth.py — Day-4B contract tests for the auth-required
# wiring on real chat + influencer handlers + health-no-auth smoke.
#
# ⭐ START HERE: 5 tests asserting the auth contract on real endpoints:
#   1. missing Authorization header on a chat handler → 401 envelope
#   2. malformed Bearer on an influencer handler → 401 envelope
#   3. legacy=ok + strict=fail (e.g., expired) → 200 (shadow doesn't deny)
#   4. flag-on + strict=fail (expired) → 401 envelope (production-grade
#      auth on real handler — flip-on smoke equivalent of Day-3's
#      test-internal version)
#   5. health endpoints answer 200 WITHOUT auth (per F9 + C10 + I2 —
#      Caddy `health_uri /health/ready` + Uptime Kuma + Swarm rolling-
#      update health checks must NOT require auth)
#
# WHY A SEPARATE FILE (not appended to test_chat_routes.py)?
# Day-2 test files (test_chat_routes / test_influencer_routes /
# test_health_routes) are about handler BEHAVIOR (envelope shape, DTO
# fields, feature-flag gating). Day-4B's tests are about the AUTH
# WIRING (the dependency raises 401, shadow doesn't deny, health
# bypasses). Separating keeps each file focused on one concern.
#
# WHY THESE 5 TESTS (not 4 as the directive lists)?
# Directive lists 4 auth-edge tests + a separate "health smoke test."
# I keep the health smoke here because it's the same "wiring contract"
# concern (health MUST NOT auth) — semantically these 5 belong together
# in one file.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from app.api.feature_flag import require_day_2_placeholder_flag_enabled
from app.config import get_settings
from app.main import app

# Reach into conftest helpers for token-minting parameters. Conftest is
# a pytest fixtures file; importing module-level constants from it is
# unusual but supported (it's a regular Python module from pytest's
# collection perspective).
from tests.contract.conftest import DEFAULT_ISSUER, TEST_KID, TEST_USER_ID


# ===========================================================================
# 1. Missing Authorization → 401 envelope on a chat handler
# ===========================================================================


def test_missing_authorization_on_chat_handler_returns_401(client_no_auth):
    """POST /api/v1/chat/conversations WITHOUT Authorization → 401
    envelope.

    WHAT: sends a request body with NO Authorization header. The
          require_authenticated_user dependency raises 401 BEFORE the
          handler body runs.
    WHEN: covers Day-4B directive item (a) on real chat handler.
    WHY:  proves the dependency is actually wired on the real
          handlers (not just on the Day-3 test-internal endpoint).
    """
    response = client_no_auth.post(
        "/api/v1/chat/conversations",
        json={"ai_influencer_id": "test", "conversation_type": "ai_chat"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "unauthorized"


# ===========================================================================
# 2. Malformed Bearer → 401 envelope on an influencer handler
# ===========================================================================


def test_malformed_bearer_on_influencer_handler_returns_401(client_no_auth):
    """GET /api/v1/influencers with `Authorization: Basic ...` → 401
    envelope.

    WHAT: sends a request with an Authorization header that doesn't
          start with `Bearer `. The dependency rejects it before the
          handler runs.
    WHEN: covers Day-4B directive item (b) on real influencer handler.
    WHY:  proves the auth contract is uniform across handler types.
    """
    response = client_no_auth.get(
        "/api/v1/influencers",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "unauthorized"


# ===========================================================================
# 3. Legacy ok + strict fail (expired) → 200 (shadow doesn't deny)
# ===========================================================================


def test_legacy_ok_strict_fail_does_not_deny_real_handler(client_no_auth, rsa_keypair):
    """Expired token sent to a real chat handler → 200 (legacy
    authoritative, strict logs divergence + fails closed silently).

    WHAT: mints a token whose `exp` is in the past. Legacy ignores
          expiry (accepts any well-formed JWT). Strict catches the
          expired claim, returns ValidationResult(ok=False,
          reason="expired") — but legacy is authoritative (flag default
          OFF), so the request proceeds + the handler returns 200.
    WHEN: covers Day-4B directive item (c).
    WHY:  proves the shadow contract holds on real handlers — strict's
          failures are observed-only, NOT user-impacting, while the
          flag is OFF.
    """
    private_pem, _ = rsa_keypair
    now = datetime.now(timezone.utc)
    expired_token = jwt.encode(
        {
            "iss": DEFAULT_ISSUER,
            "sub": TEST_USER_ID,
            "iat": int((now - timedelta(seconds=7200)).timestamp()),
            "exp": int((now - timedelta(seconds=3600)).timestamp()),
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": TEST_KID},
    )
    # Use the Day-2 placeholder flag override so the handler body runs
    # (otherwise we'd 503 before observing auth's behavior).
    app.dependency_overrides[require_day_2_placeholder_flag_enabled] = lambda: None
    try:
        response = client_no_auth.get(
            "/api/v1/influencers",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
    finally:
        app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)
    # Legacy authoritative + ok → request succeeds.
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True


# ===========================================================================
# 4. Flag on + strict fail (expired) → 401 envelope on real handler
# ===========================================================================


def test_flag_on_strict_fail_returns_401_on_real_handler(
    client_no_auth, rsa_keypair, monkeypatch,
):
    """jwt_strict_validation flag ON + expired token on real influencer
    handler → 401 envelope.

    WHAT: flips enable_strict_jwt_signature_validation to True via env
          + cache_clear; sends expired token; asserts 401.
    WHEN: covers Day-4B directive item (d). Day-3 had the equivalent
          test on the test-internal /test/whoami endpoint; this is its
          production-handler twin.
    WHY:  proves the flip-on path works end-to-end on real handlers
          (not just on the synthetic test endpoint).
    """
    monkeypatch.setenv("ENABLE_STRICT_JWT_SIGNATURE_VALIDATION", "true")
    get_settings.cache_clear()
    try:
        private_pem, _ = rsa_keypair
        now = datetime.now(timezone.utc)
        expired_token = jwt.encode(
            {
                "iss": DEFAULT_ISSUER,
                "sub": TEST_USER_ID,
                "iat": int((now - timedelta(seconds=7200)).timestamp()),
                "exp": int((now - timedelta(seconds=3600)).timestamp()),
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": TEST_KID},
        )
        app.dependency_overrides[require_day_2_placeholder_flag_enabled] = lambda: None
        try:
            response = client_no_auth.get(
                "/api/v1/influencers",
                headers={"Authorization": f"Bearer {expired_token}"},
            )
        finally:
            app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)
        assert response.status_code == 401
        body = response.json()
        assert body["success"] is False
        assert body["error"] == "unauthorized"
        assert "expired" in body["msg"]
    finally:
        get_settings.cache_clear()


# ===========================================================================
# 5. Health endpoints answer 200 WITHOUT auth (per F9 + C10 + I2)
# ===========================================================================


def test_health_endpoints_answer_without_auth():
    """/health/{live,ready,deep} are NOT auth-gated (per F9 + C10 + I2).

    WHAT: hits each of the three health endpoints with a NO-auth
          TestClient (NO default Bearer header, NO test override) and
          asserts none of them return 401. The actual status code
          (200 for /health/live; 503 for /health/ready when Redis is
          unreachable in the test env; 503 for /health/deep per the
          F9-honest "not implemented yet" contract) is exercised in
          test_health_routes.py — this test ONLY guards the auth
          contract: health probes must reach the handler body even
          when the request has no Authorization header. If anyone
          ever wires require_authenticated_user onto a health
          handler by accident, EVERY path here would 401 and this
          test would fail loudly.
    WHEN: regression guard for the F9 + C10 + I2 contract.
    WHY:  health probes have NO credentials; auth-gating health =
          permanent deploy failure (Swarm rolling update + Caddy
          health_uri + Uptime Kuma all hit these endpoints without
          a token).
    """
    with TestClient(app) as no_auth_client:
        for path in ("/health/live", "/health/ready", "/health/deep"):
            response = no_auth_client.get(path)
            assert response.status_code != 401, (
                f"{path} must NOT require auth per F9; got 401 — auth dep "
                "was wired onto a health handler"
            )


# ===========================================================================
# RELATED FILES:
#   conftest.py                          — provides `client_no_auth` + `rsa_keypair`
#   ../../app/api/dependencies.py        — the require_authenticated_user wired into handlers
#   ../../app/api/auth/dependency.py     — AuthenticatedUser + the dual-validate dependency
#   ../../app/api/chat_routes.py         — handlers under test (POST /conversations)
#   ../../app/api/influencer_routes.py   — handlers under test (GET /influencers)
#   ../../app/api/health_routes.py       — exempted from auth per F9 + this regression test
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                                        — E6 (auth via auth.yral.com),
#                                          E9 (shadow rollout flag),
#                                          F9 (three-tier health split + no auth),
#                                          C10 (Caddy health_uri probe),
#                                          I2 (canary deploy + auto-rollback on health failure)
# ===========================================================================
