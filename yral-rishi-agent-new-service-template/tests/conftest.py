# ---------------------------------------------------------------------------
# conftest.py — shared pytest fixtures for the template's unit tests.
#
# ⭐ START HERE: this file defines fixtures every `test_*.py` in `tests/`
# can use without importing anything. pytest auto-discovers conftest.py
# at collection time + makes its fixtures available by parameter name
# to tests in the same directory tree.
#
# Today's fixtures (both autouse — apply to every test without an
# explicit request):
#   - `clear_get_settings_cache_between_tests` — clears the
#     `lru_cache` on `get_settings()` so monkeypatched env vars take
#     effect between tests. Without this, the first test that calls
#     `get_settings()` caches a Settings instance; subsequent tests
#     setting different env vars would still see the cached values.
#   - `reset_redis_module_singleton_between_tests` — clears the
#     module-level `_redis` reference in `app/redis_client.py` so
#     each test starts with a fresh init_redis state. Without this,
#     a test that successfully ran init_redis would leak a live
#     client into the next test's namespace.
#
# WHY conftest.py LIVES IN tests/ (NOT THE SERVICE FOLDER ROOT)
# pytest looks for conftest.py in the directory of the test files +
# every parent up to the rootdir. Placing it next to the tests scopes
# the fixtures to test code only — production code doesn't accidentally
# pick up the autouse fixtures via stray imports.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# Shared pytest fixture decorator + monkeypatch builtin live here.
import pytest

# Direct access to the lru_cache-decorated singleton so the autouse
# fixture can invalidate it between tests.
from app.config import get_settings

# `app.redis_client` is imported as a module so the autouse fixture
# can reset its `_redis` module-level singleton without touching the
# function-level globals or relying on monkeypatch's restore behavior.
import app.redis_client as redis_client_module


@pytest.fixture(autouse=True)
def clear_get_settings_cache_between_tests():
    """Clear the `get_settings()` lru_cache before AND after every test.

    WHAT: calls `get_settings.cache_clear()` at fixture setup + teardown.
    WHEN: autouse — runs for every test in this directory tree.
    WHY:  pydantic-settings parses env vars ONCE at first
          `get_settings()` call + caches the result via lru_cache. A
          test that monkeypatches env vars sees the parse only on the
          first call; subsequent tests in the same process see the
          cached Settings unless we explicitly invalidate. Clearing
          on both ends means the test's monkeypatched env vars take
          effect AND the next test isn't polluted by this test's
          monkeypatched state.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def reset_redis_module_singleton_between_tests():
    """Close + reset `app.redis_client._redis` between every test.

    WHAT: if `_redis` holds a live async client, await its
          `aclose()` to drain pending commands + tear down the
          connection pool; then set `_redis = None`. Runs at
          BOTH fixture setup + teardown so the next test starts
          clean AND this test's leftover client (if any) is
          closed before its event loop tears down.
    WHEN: autouse — runs for every test in this directory tree.
    WHY:  Codex PR #151 round-4 CONCERN: the previous synchronous
          fixture just nulled `_redis` without closing — current
          safety-gate tests don't open a real client, but a future
          test that DOES would leak connections + hide cleanup bugs.
          redis.asyncio.Redis's `aclose()` is the proper async
          shutdown (drains pending commands; releases the underlying
          TCP socket + connection-pool entries). Calling `aclose()`
          on a None or already-closed client must be guarded — the
          `is not None` check covers None; a double-close on an
          already-aclose'd client is a no-op in redis-py 5.x.
    """
    # Setup: if a prior test crashed before its teardown branch ran
    # (or the autouse-fixture-ordering left a stale client), close
    # it now. Guards against an extremely rare cross-test leak.
    if redis_client_module._redis is not None:
        await redis_client_module._redis.aclose()
    redis_client_module._redis = None
    yield
    # Teardown: same shape — drain + close + null. Runs after every
    # test regardless of pass/fail/raise.
    if redis_client_module._redis is not None:
        await redis_client_module._redis.aclose()
    redis_client_module._redis = None


# ===========================================================================
# RELATED FILES:
#   test_redis_client_safety_gates.py
#                              — first test module: verifies the
#                                production-fail-closed gate +
#                                sentinel-config validation paths
#   ../app/config.py           — Settings model + get_settings() cache
#   ../app/redis_client.py     — module under test in safety-gate tests
#   ../pyproject.toml          — declares pytest + pytest-asyncio deps
#                                + [tool.pytest.ini_options] config
# ===========================================================================
