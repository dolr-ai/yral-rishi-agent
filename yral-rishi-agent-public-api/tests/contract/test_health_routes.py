# ---------------------------------------------------------------------------
# test_health_routes.py — contract tests for /health/{live,ready,deep}.
#
# ⭐ START HERE: 5 tests covering the Codex PR #97 BLOCKER 5 contract:
#   - /health/live always 200 (cheap, no deps)
#   - /health/ready 200 when Redis reachable (autouse mock returns True)
#   - /health/ready 503 envelope when Redis unreachable (test overrides the mock)
#   - /health/deep always 503 envelope (F9-honest "not implemented yet")
#   - all 3 endpoints unaffected by the Day-2 placeholder flag
#
# WHY THESE TESTS USE `client_flag_off`?
# Health probes have NO dependency on the placeholder flag (per F9 they
# must answer regardless of feature-flag state — otherwise a misconfigured
# flag could cause the entire service to fail rolling-update health
# checks per I2 + auto-rollback). Using the flag-off client proves they
# answer in the production-default state.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# health_routes module — monkey-patched by the Redis-down test to flip
# _check_redis_reachable from the autouse "True" default to "False".
from app.api import health_routes


def test_health_live_returns_200_with_status_ok(client_flag_off):
    """/health/live: cheapest probe — always 200 with status + service tag."""
    response = client_flag_off.get("/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # BLOCKER 5 added the service identity so on-call can grep across
    # replicas for "which container responded."
    assert body["service"] == "yral-rishi-agent-public-api"


def test_health_ready_returns_200_when_redis_reachable(client_flag_off):
    """/health/ready: returns 200 + deps.redis="ok" when Redis pings.

    The autouse `mock_redis_healthy` fixture in conftest patches
    _check_redis_reachable to True, so the readiness probe sees a
    healthy Redis without needing a real container.
    """
    response = client_flag_off.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["deps"]["redis"] == "ok"


def test_health_ready_returns_503_envelope_when_redis_down(
    client_flag_off, monkeypatch,
):
    """/health/ready: returns envelope-shaped 503 when Redis is unreachable.

    Codex PR #97 BLOCKER 5: a broken Redis must trip the readiness probe
    so Swarm rolling-update + Caddy `health_uri` see the upstream as
    down. Test overrides the autouse mock to simulate Redis-down + asserts
    the locked error-code wire shape.
    """
    monkeypatch.setattr(health_routes, "_check_redis_reachable", lambda: False)
    response = client_flag_off.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "service_unavailable"
    # The msg explicitly names Redis so on-call knows which dep failed.
    assert "redis" in body["msg"].lower()


def test_health_deep_returns_503_envelope_with_explanation(client_flag_off):
    """/health/deep: returns 503 envelope (BLOCKER 5 F9-honest fallback).

    Day-2 used to return 200 + "not implemented yet" which the on-call
    dashboard misread as healthy. The fixup returns 503 envelope so a
    misleading "all green" is impossible until Day-5+ wires a real
    round-trip.
    """
    response = client_flag_off.get("/health/deep")
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "service_unavailable"
    # The msg explicitly notes deep is "not yet implemented" so on-call
    # can distinguish "real outage" from "expected stub" at a glance.
    assert "not yet implemented" in body["msg"].lower() or "not implemented" in body["msg"].lower()


# ===========================================================================
# RELATED FILES:
#   conftest.py                          — provides `client_flag_off` +
#                                          the autouse mock_redis_healthy fixture
#   ../../app/api/health_routes.py       — handlers under test +
#                                          _check_redis_reachable (mocked here)
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                                        — F9 (three-tier health split),
#                                          C10 (Caddy health_uri probe),
#                                          I2 (canary deploy auto-rollback)
# ===========================================================================
