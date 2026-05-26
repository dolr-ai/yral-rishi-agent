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
# the empty-default → None normalization that keeps local development working.
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
          breaks JWKS cache + idempotency-deduplication. Defends against a
          refactor that drops the password argument silently.
    """
    # redis_client — module exposing the lru_cache'd get_redis()
    # singleton; tests monkeypatch its `redis.Redis.from_url` callsite
    # and invoke `redis_client.reset_for_testing()` to clear the cache
    # between assertions so a captured fake client doesn't leak.
    from app import redis_client

    # Settings — pydantic-settings class for the app's typed runtime
    # config; tests instantiate a fresh `Settings(...)` to drive
    # specific field values (redis_password, enforce_passwordless_*)
    # without monkeypatching env vars at process scope.
    from app.config import Settings

    # Build a fresh Settings instance with a known password so the
    # assertion below has a unique sentinel to look for.
    fake_settings = Settings(redis_password="test-password-from-fixture")
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

        assert captured.get("password") == "test-password-from-fixture", (
            f"Expected `password=test-password-from-fixture` argument on from_url(); "
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
           redis-py skips the AUTH frame in local development. Defends
           against the regression where someone removes `or None`
           + breaks local-development unauthenticated Redis.

    WHAT: assert that when settings.redis_password=="" (the empty
          default kept for local development), get_redis() forwards
          password=None (NOT password="") to redis.Redis.from_url().
    WHEN: laptop development / docker-compose / CI — environments where the
          local Redis container runs unauthenticated.
    """
    # redis_client — module exposing the lru_cache'd get_redis()
    # singleton; tests monkeypatch its `redis.Redis.from_url` callsite
    # and invoke `redis_client.reset_for_testing()` to clear the cache
    # between assertions so a captured fake client doesn't leak.
    from app import redis_client

    # Settings — pydantic-settings class for the app's typed runtime
    # config; tests instantiate a fresh `Settings(...)` to drive
    # specific field values (redis_password, enforce_passwordless_*)
    # without monkeypatching env vars at process scope.
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
    # AsyncMock — unittest.mock async-aware mock; used to fake the
    # awaitable Sentinel-discovered primary client's `.ping()`
    # coroutine so the health probe returns True without booting Redis.
    # MagicMock — sync mock used for the non-async Sentinel +
    # primary-client object shells; `master_for` is a regular method
    # that returns the primary mock, so MagicMock (not AsyncMock) is
    # the right shape for the outer Sentinel object.
    from unittest.mock import AsyncMock, MagicMock

    # health_routes — module containing `_check_redis_reachable` +
    # the `Sentinel` class import the test monkeypatches; the
    # `_load_redis_section_from_shared_config` helper is also stubbed
    # via setattr on this module.
    from app.api import health_routes

    # Settings — see the per-import comment above on the redis_client
    # block; reused here to drive `redis_sentinel_enabled=True` +
    # `redis_password=<sentinel-value>` for the Sentinel-path probe.
    from app.config import Settings

    # Force the Sentinel-aware code path with a known password. The
    # default Settings() has redis_sentinel_enabled=False which would
    # take the single-primary fallback branch (the test_get_redis_*
    # tests above already cover that path).
    fake_settings = Settings(
        redis_sentinel_enabled=True,
        redis_password="test-password-from-fixture",
    )
    monkeypatch.setattr(health_routes, "get_settings", lambda: fake_settings)

    # Stub the shared-config loader so the probe doesn't try to read
    # the real YAML file (which has the production rishi-4/5/6 hosts).
    monkeypatch.setattr(
        health_routes,
        "_load_redis_section_from_shared_config",
        lambda: {
            "sentinel_master_name": "yral-v2-redis-primary",
            "sentinel_hosts": [{"host": "redis-sentinel-for-test", "port": 26379}],
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
    assert captured.get("password") == "test-password-from-fixture", (
        f"Expected `password=test-password-from-fixture` argument on master_for(); "
        f"got: {captured!r}"
    )
    # Secondary signal: the handler took the 200 branch (the mock
    # ping returned True), confirming the wiring works end-to-end
    # not just at the password-forward boundary.
    assert response.status_code == 200
    assert response.json()["dependencies"]["redis"] == "ok"


# ===========================================================================
# Round-8 — passwordless REDIS_URL contract validator (Codex BLOCKER 1)
# ===========================================================================
# The Settings model's `_reject_password_in_redis_url` validator
# rejects credential-bearing REDIS_URL values at construction time
# so an operator who copies the pre-round-8 `redis://:password@host`
# format gets a loud startup crash naming the field — instead of a
# silent runtime credential-precedence confusion where redis-py
# would take URL credentials over the `password=` keyword argument, bypassing
# the REDIS_PASSWORD Swarm-secret rotation pattern.
# ===========================================================================


def test_redis_url_with_embedded_password_is_rejected_when_flag_is_on():
    """WHAT: instantiate the Settings model with
            enforce_passwordless_redis_url=True AND a REDIS_URL that
            embeds a password in the `user:pass@host` portion; assert
            ValidationError raised at construction time (NOT silently
            accepted + handed to redis-py).
    WHEN: post-rotation steady state — Session 3 has flipped
          enforce_passwordless_redis_url to True (in a small
          follow-up PR that edits the public-api compose default
          from `:-false` to `:-true`) after Session 1 lands PR #150
          + the secret-rotation operator-action + confirms the
          deployed REDIS_URL is passwordless. An operator who
          copies a credential-bearing URL into a .env.local or
          Swarm secret value now gets a loud boot crash naming
          the field.
    WHY:  defense-in-depth on the passwordless-URL contract.
          REDIS_PASSWORD is the sole AUTH source — when the flag is
          on, the validator fails LOUDLY at Settings boot rather
          than allowing the URL-embedded credentials to silently
          take precedence over the `password=` argument that
          forwards REDIS_PASSWORD.
    """
    # ValidationError — the exception the round-8
    # `_reject_password_in_redis_url` field validator raises (via
    # pydantic v2's `ValueError` → `ValidationError` translation
    # path) when the round-11 `enforce_passwordless_redis_url` flag
    # is True AND the URL embeds credentials. The `with
    # pytest.raises(...)` block asserts the validator fires.
    from pydantic import ValidationError

    # Settings — see the per-import comment in the redis_client
    # block above; reused here to drive
    # `enforce_passwordless_redis_url=True` + the credential-bearing
    # REDIS_URL that the validator must reject.
    from app.config import Settings

    with pytest.raises(ValidationError, match="REDIS_URL must be passwordless"):
        Settings(
            enforce_passwordless_redis_url=True,
            redis_url="redis://:leaked-password@some-host:6379/0",
        )


def test_redis_url_without_embedded_password_is_accepted_when_flag_is_on():
    """WHY: regression guard — the validator MUST accept the standard
           passwordless production + local-development forms verbatim
           even when the enforce flag is on.

    WHAT: instantiate the Settings model with
          enforce_passwordless_redis_url=True + three passwordless
          REDIS_URL forms (local docker-compose; production single-
          primary by hostname; full URL with port + database); assert
          no ValidationError raised.
    WHEN: every CI run — defends against a future tightening of the
          validator that accidentally rejects a legitimate URL when
          the flag is enabled.
    """
    # Settings — see the per-import comment on the redis_client
    # block above; reused here to drive
    # `enforce_passwordless_redis_url=True` against three passwordless
    # URL forms (none of which should trigger the validator's
    # rejection branch).
    from app.config import Settings

    # local docker-compose
    Settings(
        enforce_passwordless_redis_url=True,
        redis_url="redis://localhost:6379/0",
    )
    # production hostname
    Settings(
        enforce_passwordless_redis_url=True,
        redis_url="redis://yral-v2-redis-primary:6379",
    )
    # other port + database
    Settings(
        enforce_passwordless_redis_url=True,
        redis_url="redis://some-host.internal:6380/2",
    )


def test_credential_bearing_redis_url_is_accepted_when_flag_is_off():
    """WHY: closes Codex PR #137 round-9 BLOCKER 3 — feature-flag
           safety net. Until Session 1 rotates the deployed Swarm /
           GitHub Secret REDIS_URL to passwordless shape AND
           Session 3 flips enforce_passwordless_redis_url to True
           in a follow-up compose-default PR, the pre-round-8
           credential-bearing URL form MUST keep working. This
           test proves the default-FALSE flag value lets a
           credential-bearing URL through without raising.

    WHAT: instantiate the Settings model WITHOUT setting
          enforce_passwordless_redis_url (default FALSE) + a
          credential-bearing REDIS_URL; assert NO exception raised +
          the URL is preserved verbatim in the constructed instance.
    WHEN: the PR-merge window between this PR landing and Session 3's
          follow-up that flips the flag TRUE (which itself only
          fires after Session 1 confirms the deployed REDIS_URL
          has been rotated to the passwordless shape). The deployed
          Swarm REDIS_URL may still carry the pre-round-8
          credential-bearing shape; the validator must NOT crash
          startup.
    """
    # Settings — see the per-import comment on the redis_client
    # block above; reused here with the flag at its default-FALSE
    # value so the validator's rejection branch is gated off + a
    # credential-bearing URL passes through unchanged.
    from app.config import Settings

    credential_bearing_url = "redis://:leaked-password@some-host:6379/0"
    settings = Settings(redis_url=credential_bearing_url)
    # URL preserved verbatim — no validator-side mutation, no
    # validation error.
    assert settings.redis_url == credential_bearing_url
    # Flag confirmed default-FALSE so a future refactor that flips
    # the default doesn't silently break this safety-net behavior.
    assert settings.enforce_passwordless_redis_url is False


def test_redis_url_with_empty_credentials_is_rejected_when_flag_is_on():
    """WHY: closes Codex PR #137 round-18 CONCERN — the prior
           validator condition `parsed.username or parsed.password`
           treated empty-string credentials as falsy, letting URL
           forms like `redis://:@host:6379/0` and `redis://@host:6379/0`
           slip through. redis-py's URL parser still interprets the
           `@` separator as a credential-bearing URL in those
           shapes, which would silently take precedence over the
           `password=` keyword argument that forwards REDIS_PASSWORD
           — defeating the passwordless-URL contract.

    WHAT: instantiate the Settings model with
          enforce_passwordless_redis_url=True against four
          empty-or-partial-credential URL shapes and assert all
          four raise ValidationError:
            1. `redis://:@host:6379/0`     — empty username, empty password
            2. `redis://user:@host:6379/0` — username, empty password
            3. `redis://:pass@host:6379/0` — empty username, password
            4. `redis://@host:6379/0`      — only the `@` separator
          The round-19 fix changed the validator's rejection
          condition from `parsed.username or parsed.password` to
          `"@" in parsed.netloc`, which catches all credential-
          separator forms regardless of whether the credentials
          themselves are empty.
    WHEN: every CI run — defends against a future refactor that
          reverts the round-19 condition back to the
          username-or-password truthiness check.
    """
    # ValidationError — the exception the round-8 validator raises
    # (via pydantic v2's ValueError → ValidationError translation
    # path) when the round-11 flag is True AND the URL netloc
    # contains the credential separator `@`. The `with
    # pytest.raises(...)` block asserts the validator fires for
    # each of the four credential-bearing shapes.
    from pydantic import ValidationError

    # Settings — see the per-import comment in the redis_client
    # block above; reused here to drive
    # `enforce_passwordless_redis_url=True` against four URL forms
    # the prior truthiness check let slip through.
    from app.config import Settings

    credential_bearing_shapes_with_empty_or_partial_credentials = [
        "redis://:@host:6379/0",
        "redis://user:@host:6379/0",
        "redis://:pass@host:6379/0",
        "redis://@host:6379/0",
    ]
    for url in credential_bearing_shapes_with_empty_or_partial_credentials:
        with pytest.raises(ValidationError, match="REDIS_URL must be passwordless"):
            Settings(
                enforce_passwordless_redis_url=True,
                redis_url=url,
            )


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
