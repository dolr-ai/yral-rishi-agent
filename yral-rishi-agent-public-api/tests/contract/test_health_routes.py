# ---------------------------------------------------------------------------
# test_health_routes.py — contract tests for /health/{live,ready,deep}.
#
# ⭐ START HERE: 4 tests covering the Codex PR #97 round-4 state of
# the readiness probe + the BLOCKER-5 deep probe:
#   - /health/live always 200 (cheap, no deps)
#   - /health/ready 200 with `dependencies.redis="ok"` when the
#     real async-Sentinel-aware ping (monkey-patched here for
#     determinism) succeeds
#   - /health/ready 503 envelope when `redis_asyncio.Redis.from_url`
#     returns a client whose ping() raises (simulates the
#     single-primary fallback Redis being unreachable)
#   - /health/deep always 503 envelope (F9-honest "not implemented yet")
#
# WHY THESE TESTS USE `client_flag_off`?
# Health probes have NO dependency on the placeholder flag (per F9 they
# must answer regardless of feature-flag state — otherwise a misconfigured
# flag could cause the entire service to fail rolling-update health
# checks per I2 + auto-rollback). Using the flag-off client proves they
# answer in the production-default state.
#
# WHY MONKEY-PATCH `_check_redis_reachable` FOR THE 200 PATH BUT PATCH
# `redis_asyncio.Redis.from_url` FOR THE 503 PATH?
# Two reasons:
#   1. The 200-path test cares about the handler's response shape
#      (200 dict with `dependencies.redis="ok"`); patching the helper
#      directly is the cleanest way to exercise the response branch
#      without booting a real Redis.
#   2. The 503-path test should exercise the REAL code that talks to
#      Redis (so a future regression in the timeout / error-handling
#      logic fails the test). Patching `redis_asyncio.Redis.from_url`
#      to return a mock whose ping() raises lets the actual
#      `_check_redis_reachable` body run through the try/except.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# unittest.mock.AsyncMock — used to build an async-callable fake whose
# .ping() returns True (200 path) or raises (503 path) so we can
# control the readiness probe's behavior without real Redis.
from unittest.mock import AsyncMock

# pytest — used by the monkeypatch fixture below + would expose
# pytest.raises if we needed it.
import pytest  # noqa: F401 — imported for fixture discovery clarity

# health_routes module — monkey-patched in the 200-path test to flip
# `_check_redis_reachable` from "real Sentinel ping" to an async-True
# stub so the handler returns the 200 happy-path body without booting
# Redis.
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
    assert body["service"] == "yral-rishi-agent-public-api"


def test_health_ready_returns_200_when_redis_pingable(client_flag_off, monkeypatch):
    """/health/ready: 200 + dependencies.redis="ok" when the ping succeeds.

    WHAT: monkey-patches `_check_redis_reachable` to an async stub
          returning True; GETs /health/ready; asserts HTTP 200 +
          raw body shape `{"status": "ok", "dependencies": {"redis": "ok"}}`.
    WHEN: simulates the cluster steady state — Sentinel-aware client
          successfully pings the current Redis primary.
    WHY:  Codex PR #97 round-4 BLOCKER 2 flipped /health/ready from a
          round-3 503-always stub to the real async-Sentinel-aware
          check. This test exercises the happy-path handler branch
          (the 200 envelope, the BLOCKER-3 `dependencies` key spelling)
          without needing a real Redis container — the real Sentinel
          path itself is exercised in the Day-5 cluster smoke test.
    """
    async def fake_check() -> bool:
        return True

    monkeypatch.setattr(health_routes, "_check_redis_reachable", fake_check)
    response = client_flag_off.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # BLOCKER 3 rename — `dependencies`, not `deps`.
    assert body["dependencies"]["redis"] == "ok"


def test_health_ready_returns_503_envelope_when_redis_ping_fails(
    client_flag_off, monkeypatch,
):
    """/health/ready: 503 envelope when the redis ping raises.

    WHAT: patches `redis_asyncio.Redis.from_url` to return a mock
          whose `.ping()` raises ConnectionError. The fixture flag
          stays at its default (`redis_sentinel_enabled=False`), so
          `_check_redis_reachable` takes the single-primary fallback
          path + invokes `from_url()` + awaits `.ping()` + catches
          the raised error + returns False. The handler then returns
          envelope-shaped 503.
    WHEN: simulates the single-primary fallback Redis being down
          (laptop dev with `redis-server` stopped, OR cluster smoke
          before the Sentinel flag is flipped on).
    WHY:  exercises the REAL `_check_redis_reachable` code path
          end-to-end (not just the handler's branch on the boolean) —
          if a future regression broke the timeout / error-handling
          logic, this test catches it. Asserts the locked
          `error="service_unavailable"` envelope wire shape.
    """
    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock(
        side_effect=ConnectionError("simulated redis down for test"),
    )

    # The health helper imports `redis.asyncio as redis_asyncio`;
    # patching `Redis.from_url` on the `redis.asyncio.Redis` class
    # reaches the call site since attribute lookup happens at call time.
    import redis.asyncio as redis_asyncio_lib

    monkeypatch.setattr(
        redis_asyncio_lib.Redis,
        "from_url",
        classmethod(lambda cls, *args, **kwargs: fake_redis),
    )

    response = client_flag_off.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"] == "service_unavailable"
    # The data.dependencies map is the rename target per BLOCKER 3
    # (was `deps`). Future deps (Postgres, orchestrator) layer in here.
    assert body["data"]["dependencies"]["redis"] == "unreachable"


def test_verify_production_sentinel_or_die_raises_when_flag_off(monkeypatch):
    """Codex PR #97 round-5 ITEM 6: production env + Sentinel OFF → SystemExit.

    WHAT: monkey-patches `settings.environment` to "production" +
          `settings.redis_sentinel_enabled` to False via env vars;
          calls `health_routes.verify_production_sentinel_or_die()`;
          asserts SystemExit(1) is raised.
    WHEN: at app construction time on a misconfigured production
          deploy. The check is wired into `app/main.py` so the worker
          exits at startup rather than serving with a silent C11
          violation.
    WHY:  Codex PR #97 round-5 ITEM 6 — production MUST use Sentinel
          per C11. Without this fail-closed check, a misconfigured
          deploy with the flag OFF would silently degrade to single-
          primary (catastrophic on Redis failover); SystemExit makes
          the combination impossible to ship.
    """
    import sys
    from app.config import get_settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_SENTINEL_ENABLED", "false")
    get_settings.cache_clear()

    import pytest as _pytest

    with _pytest.raises(SystemExit) as exc_info:
        health_routes.verify_production_sentinel_or_die()

    assert exc_info.value.code == 1

    # Cleanup — restore the cached settings to the test defaults so
    # subsequent tests in the session don't see the production env.
    get_settings.cache_clear()


def test_verify_production_sentinel_or_die_passes_when_flag_on(monkeypatch):
    """Production env + Sentinel ON → check passes (no exit).

    WHAT: monkey-patches env to production + Sentinel ON; calls the
          check; asserts no SystemExit is raised.
    WHEN: the happy-path production deploy.
    WHY:  proves the check is a TARGETED gate (only env=production +
          flag=False combination), not a blanket production block.
    """
    from app.config import get_settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_SENTINEL_ENABLED", "true")
    get_settings.cache_clear()

    # Should NOT raise. Just call + let the test continue.
    health_routes.verify_production_sentinel_or_die()

    get_settings.cache_clear()


def test_verify_production_sentinel_or_die_passes_in_local_env(monkeypatch):
    """Local env + Sentinel OFF → check passes (laptop dev fallback OK).

    WHAT: monkey-patches env to local + Sentinel OFF; calls the check;
          asserts no SystemExit.
    WHEN: laptop dev + docker-compose + CI all run with the flag OFF
          per the default. The C11-fallback LOUD warning in
          `_check_redis_reachable` is the dev-time signal there.
    WHY:  the production fail-closed gate must NOT block local
          development. Codex PR #97 round-5 ITEM 6 directive: "Local
          dev (environment='local') still allowed to fall back."
    """
    from app.config import get_settings

    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("REDIS_SENTINEL_ENABLED", "false")
    get_settings.cache_clear()

    health_routes.verify_production_sentinel_or_die()

    get_settings.cache_clear()


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
    msg_lower = body["msg"].lower()
    assert "not yet implemented" in msg_lower or "not implemented" in msg_lower


# ===========================================================================
# RELATED FILES:
#   conftest.py                          — provides `client_flag_off`
#   ../../app/api/health_routes.py       — handlers under test +
#                                          `_check_redis_reachable` (the
#                                          200-path test monkey-patches
#                                          this; the 503-path test patches
#                                          `redis_asyncio.Redis.from_url`
#                                          one level deeper to exercise
#                                          the real helper body)
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                                        — F9 (three-tier health split),
#                                          C10 (Caddy health_uri probe),
#                                          C11 (Redis Sentinel HA — the
#                                          contract the round-4 fix
#                                          implements verbatim),
#                                          I2 (canary deploy auto-rollback)
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md
#                                        — DEP-006 RESOLVED in round-4:
#                                          Session 1's cluster bootstrap
#                                          already declared the Sentinel
#                                          config; round-3 raised the DEP
#                                          on a stale read
# ===========================================================================
