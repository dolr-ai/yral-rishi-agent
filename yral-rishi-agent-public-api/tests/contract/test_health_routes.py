# ---------------------------------------------------------------------------
# test_health_routes.py — contract tests for /health/{live,ready,deep}.
#
# ⭐ START HERE: 4 tests covering the Codex PR #97 round-3 BLOCKER 2
# state of the readiness probe + the BLOCKER-5 deep probe:
#   - /health/live always 200 (cheap, no deps)
#   - /health/ready 503 envelope by default (DEP-006 not yet resolved)
#   - /health/ready 200 with `dependencies.redis="ok"` IF the helper
#     is monkey-patched to True (future-ready test for when the real
#     async Sentinel-aware check lands)
#   - /health/deep always 503 envelope (F9-honest "not implemented yet")
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

# health_routes module — monkey-patched by the future-ready 200 test
# to flip _check_redis_reachable from the BLOCKER-2 stub-False default
# to True, exercising the eventual happy-path branch.
from app.api import health_routes


def test_health_live_returns_200_with_status_ok(client_flag_off):
    """/health/live: cheapest probe — always 200 with status + service.

    WHAT: GETs /health/live + asserts HTTP 200 with body containing
          `status="ok"` + the service identity string.
    WHEN: docker / Swarm probe this every few seconds to know if the
          container PID is still responsive.
    WHY:  liveness is the contract gate Swarm uses to decide "restart
          the container vs leave it"; a regression to 5xx here would
          loop-restart the container forever.
    """
    response = client_flag_off.get("/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # BLOCKER 5 added the service identity so on-call can grep across
    # replicas for "which container responded."
    assert body["service"] == "yral-rishi-agent-public-api"


def test_health_ready_returns_503_envelope_by_default(client_flag_off):
    """/health/ready: returns envelope-shaped 503 by default (BLOCKER 2).

    WHAT: GETs /health/ready WITHOUT mocking the readiness helper;
          asserts the envelope-shaped 503 with `error="service_unavailable"`
          + the msg referring to DEP-006 + a `data.dependencies.redis`
          marker of "not_yet_implemented".
    WHEN: production-default state — the Sentinel-aware async check
          hasn't landed yet (DEP-006 pending Session 1).
    WHY:  Codex round-3 BLOCKER 2 + coordinator preference: ship the
          F9-honest 503 now (clean + revertable). Better to loudly
          block deploys than ship a misleading 200 that lets a
          half-built v2 cluster claim healthy.
    """
    response = client_flag_off.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "service_unavailable"
    # The msg explicitly names DEP-006 so on-call knows what's pending.
    assert "dep-006" in body["msg"].lower()
    # The data.dependencies map is the rename target per BLOCKER 3
    # (was `deps`). Future deps (Postgres, orchestrator) layer in here.
    assert body["data"]["dependencies"]["redis"] == "not_yet_implemented"


def test_health_ready_returns_200_when_check_returns_true(client_flag_off, monkeypatch):
    """/health/ready: returns 200 with `dependencies.redis="ok"` when
    `_check_redis_reachable` returns True (future-ready BLOCKER 2 path).

    WHAT: monkey-patches health_routes._check_redis_reachable to True;
          GETs /health/ready; asserts HTTP 200 + raw body shape with
          `dependencies.redis="ok"`.
    WHEN: forward-looking — exercises the branch the follow-up
          async-Sentinel PR will fill in. Right now the helper is a
          stub-False; this test proves the 200 path is wired correctly
          + uses the English-spelled `dependencies` key (BLOCKER 3
          rename from `deps`).
    WHY:  ships the test now so the follow-up PR's only diff is
          changing the helper from stub → real implementation; no
          test-infrastructure change needed at that point.
    """
    monkeypatch.setattr(health_routes, "_check_redis_reachable", lambda: True)
    response = client_flag_off.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # BLOCKER 3 rename — `dependencies`, not `deps`.
    assert body["dependencies"]["redis"] == "ok"


def test_health_deep_returns_503_envelope_with_explanation(client_flag_off):
    """/health/deep: returns 503 envelope (BLOCKER 5 F9-honest fallback).

    WHAT: GETs /health/deep + asserts HTTP 503 + envelope-shaped body
          with `error="service_unavailable"` + msg explicitly noting
          the deep check is not yet implemented.
    WHEN: H9 synthetic-user heartbeat probes this every 5 min on prod.
    WHY:  Day-2 used to return 200 + "not implemented yet" which the
          on-call dashboard misread as healthy. The 503 here means
          on-call sees an unambiguous signal until Day-5+ wires a real
          end-to-end round-trip through one handler path.
    """
    response = client_flag_off.get("/health/deep")
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "service_unavailable"
    # The msg explicitly notes deep is "not yet implemented" so on-call
    # can distinguish "real outage" from "expected stub" at a glance.
    msg_lower = body["msg"].lower()
    assert "not yet implemented" in msg_lower or "not implemented" in msg_lower


# ===========================================================================
# RELATED FILES:
#   conftest.py                          — provides `client_flag_off`
#   ../../app/api/health_routes.py       — handlers under test +
#                                          _check_redis_reachable (monkey-
#                                          patched by the future-ready test)
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                                        — F9 (three-tier health split),
#                                          C10 (Caddy health_uri probe),
#                                          C11 (Redis Sentinel HA),
#                                          I2 (canary deploy auto-rollback)
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md
#                                        — DEP-006 (Session 1 Sentinel
#                                          config that unblocks the real
#                                          async readiness check)
# ===========================================================================
