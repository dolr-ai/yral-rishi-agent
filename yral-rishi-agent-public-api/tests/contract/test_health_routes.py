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
# Redis-AUTH wiring tests — both Redis paths MUST forward the
# settings.redis_password value so the v2 cluster's `--requirepass`-
# enabled primary accepts the connection. Tests assert the password
# argument reaches `redis.Redis.from_url()` (single-URL path) AND
# `Sentinel.master_for()` (C11 Sentinel-aware path). Third test guards
# the empty-default → None normalization that keeps local dev working.
# Closes the Codex CONCERN on closed coordinator PR #134 by proving
# the public-api half of the wiring on both Redis paths.
#
# Test-isolation discipline (round-2 fix per Codex CONCERN on PR #137):
# the get_redis() tests below MUST clear the `redis_client.get_redis`
# lru_cache in a `finally` block so a fake Redis object captured by
# the monkey-patched from_url() doesn't leak into later tests that
# call get_redis() expecting either the real client or a different
# fake. Without the finally-clear, test-order-dependent failures
# surface when an unrelated downstream test happens to call
# get_redis() AFTER one of these tests runs.
# ===========================================================================


def test_get_redis_forwards_password_to_from_url(monkeypatch):
    """WHAT: assert get_redis() forwards settings.redis_password into
            redis.Redis.from_url(password=...).
    WHEN: when settings.redis_password is non-empty, the redis-py
          from_url() call MUST include the password argument so the
          AUTH frame is sent on connection.
    WHY:  v2 cluster's Redis primary runs --requirepass; without the
          AUTH frame the first command raises AuthenticationError +
          breaks JWKS cache + idempotency-dedup. Defends against a
          refactor that drops the password argument silently.
    """
    from app import redis_client
    from app.config import Settings

    # Build a fresh Settings instance with a known password so the
    # assertion below has a unique sentinel to look for.
    fake_settings = Settings(redis_password="test-pwd-from-fixture")
    monkeypatch.setattr(redis_client, "get_settings", lambda: fake_settings)

    # Clear the lru_cache on get_redis so the next call re-runs the
    # body against the patched settings.
    redis_client.reset_for_testing()
    try:
        # Capture the keyword arguments from_url receives. Return value
        # is ignored — the test only cares that the AUTH credential
        # reached the redis-py boundary.
        captured: dict = {}

        def fake_from_url(*positional_arguments, **keyword_arguments):
            captured.update(keyword_arguments)
            return object()

        monkeypatch.setattr(
            redis_client.redis.Redis, "from_url", fake_from_url,
        )

        redis_client.get_redis()

        assert captured.get("password") == "test-pwd-from-fixture", (
            f"Expected `password=test-pwd-from-fixture` argument on from_url(); "
            f"got: {captured!r}"
        )
    finally:
        # Clear the lru_cache AGAIN after the test — without this, the
        # fake-Redis object captured above would leak into any later
        # test that calls get_redis() and expects a fresh client.
        # Test-order-dependent failures otherwise (per Codex CONCERN
        # on PR #137 round 1).
        redis_client.reset_for_testing()


def test_empty_redis_password_resolves_to_none_in_from_url(monkeypatch):
    """WHY: the `or None` guard normalizes empty string to None so
           redis-py skips the AUTH frame in local dev. Defends
           against the regression where someone removes `or None`
           + breaks local-dev unauthenticated Redis.

    WHAT: assert that when settings.redis_password=="" (the empty
          default kept for local dev), get_redis() forwards
          password=None (NOT password="") to redis.Redis.from_url().
    WHEN: laptop dev / docker-compose / CI — environments where the
          local Redis container runs unauthenticated.
    """
    from app import redis_client
    from app.config import Settings

    fake_settings = Settings(redis_password="")
    monkeypatch.setattr(redis_client, "get_settings", lambda: fake_settings)
    redis_client.reset_for_testing()
    try:
        captured: dict = {}

        def fake_from_url(*positional_arguments, **keyword_arguments):
            captured.update(keyword_arguments)
            return object()

        monkeypatch.setattr(
            redis_client.redis.Redis, "from_url", fake_from_url,
        )

        redis_client.get_redis()

        # The contract is the literal `None`, not just "falsy" —
        # redis-py treats password="" differently than password=None
        # (the former may send an empty AUTH frame which the primary
        # rejects).
        assert captured.get("password") is None, (
            f"Expected `password=None` (empty-default normalized); "
            f"got: {captured!r}"
        )
    finally:
        # Same cache-leak guard as the test above. Per Codex CONCERN
        # on PR #137 round 1.
        redis_client.reset_for_testing()


def test_health_ready_sentinel_path_forwards_password(
    client_flag_off, monkeypatch,
):
    """WHAT: assert /health/ready's Sentinel-aware probe forwards
            settings.redis_password into master_for(password=...).
    WHEN: when settings.redis_sentinel_enabled=True AND
          settings.redis_password is non-empty.
    WHY:  without the AUTH frame, the post-discovery ping() raises
          AuthenticationError + /health/ready falsely reports Redis
          unreachable, breaking Swarm's healthcheck-based
          rolling-update decision.
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.api import health_routes
    from app.config import Settings

    # Force the Sentinel-aware code path with a known password. The
    # default Settings() has redis_sentinel_enabled=False which would
    # take the single-primary fallback branch (the test_get_redis_*
    # tests above already cover that path).
    fake_settings = Settings(
        redis_sentinel_enabled=True,
        redis_password="test-pwd-from-fixture",
    )
    monkeypatch.setattr(health_routes, "get_settings", lambda: fake_settings)

    # Stub the shared-config loader so the probe doesn't try to read
    # the real YAML file (which has the production rishi-4/5/6 hosts).
    monkeypatch.setattr(
        health_routes,
        "_load_redis_section_from_shared_config",
        lambda: {
            "sentinel_master_name": "yral-v2-redis-primary",
            "sentinel_hosts": [{"host": "127.0.0.1", "port": 26379}],
        },
    )

    # Mock the Sentinel class so master_for() is observable. The
    # primary client mock returns True from ping() so the handler
    # takes the 200 branch + the test can assert the response code
    # as a secondary signal that the wiring works end-to-end.
    captured: dict = {}
    mock_primary = MagicMock()
    mock_primary.ping = AsyncMock(return_value=True)
    mock_sentinel = MagicMock()

    def fake_master_for(master_name, **keyword_arguments):
        captured["master_name"] = master_name
        captured.update(keyword_arguments)
        return mock_primary

    mock_sentinel.master_for = fake_master_for
    monkeypatch.setattr(
        health_routes, "Sentinel",
        lambda *positional_arguments, **keyword_arguments: mock_sentinel,
    )

    response = client_flag_off.get("/health/ready")

    # Primary assertion: the AUTH credential reached master_for.
    assert captured.get("password") == "test-pwd-from-fixture", (
        f"Expected `password=test-pwd-from-fixture` argument on master_for(); "
        f"got: {captured!r}"
    )
    # Secondary signal: the handler took the 200 branch (the mock
    # ping returned True), confirming the wiring works end-to-end
    # not just at the password-forward boundary.
    assert response.status_code == 200
    assert response.json()["dependencies"]["redis"] == "ok"


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
