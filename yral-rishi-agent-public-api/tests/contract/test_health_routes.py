# ---------------------------------------------------------------------------
# test_health_routes.py — contract tests for /health/{live,ready,deep}.
#
# ⭐ START HERE: the contract for health endpoints (per F9) is NOT the
# ApiResponse envelope — it's the simpler `{"status": "ok"}` shape that
# docker / Swarm / Uptime Kuma probe. These tests assert that simpler
# shape AND assert the endpoints don't depend on the Day-2 placeholder
# flag (health must answer regardless of feature-flag state — otherwise
# a misconfigured flag could cause the entire service to fail rolling-
# update health checks during deploy per I2 + auto-rollback).
#
# WHY THESE TESTS USE `client_flag_off` ONLY?
# Health probes have NO dependency on the placeholder flag. Using the
# flag-off client proves the endpoints answer in the production-default
# state. (Using `client` would also pass but adds no information.)
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


def test_health_live_returns_200_with_status_ok(client_flag_off):
    """/health/live: cheapest possible probe — always returns 200."""
    response = client_flag_off.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_ready_returns_200_with_status_ok(client_flag_off):
    """/health/ready: Day-2 returns 200 unconditionally (no real dep
    checks until Day 4+). Swarm rolling-update relies on this."""
    response = client_flag_off.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_deep_returns_200_with_explicit_note(client_flag_off):
    """/health/deep: Day-2 returns 200 with a note explaining the deep
    check is not yet implemented (so the H9 synthetic-user heartbeat
    sees an unambiguous response, not a silent stub)."""
    response = client_flag_off.get("/health/deep")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "note" in body  # explicit "not yet implemented" disclosure


# ===========================================================================
# RELATED FILES:
#   conftest.py                          — provides `client_flag_off`
#   ../../app/api/health_routes.py       — handlers under test
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                                        — F9 (three-tier health split)
# ===========================================================================
