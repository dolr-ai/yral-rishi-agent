# ---------------------------------------------------------------------------
# conftest.py — shared pytest fixtures for the orchestrator's tests.
#
# ⭐ START HERE: this file defines the fixtures every test_*.py file in
# `tests/` can use without importing anything. pytest auto-discovers
# conftest.py at collection time and makes its fixtures available by
# parameter name to the tests in the same directory tree.
#
# Today's fixtures:
#   - `clean_settings_cache` (auto-use) — clears the `get_settings()`
#     lru_cache so monkeypatched env vars take effect between tests.
#   - `fake_redis` (auto-use) — patches `app.idempotency._redis` to a
#     fakeredis async client so the lifespan's `init_redis()` runs
#     against an in-memory Redis instead of attempting a real TCP
#     connection. Required because the F10 fixup (Codex PR-#96
#     BLOCKER 1) wired Redis into the FastAPI lifespan startup hook.
#   - `client` — FastAPI TestClient wired to `app.main.app`, runs
#     after the two auto-use fixtures so each test gets a clean
#     Redis + clean settings.
#
# WHY fakeredis AND NOT testcontainers-redis
# fakeredis is a pure-Python in-memory Redis impl — zero Docker
# requirement, zero TCP, runs everywhere pytest does. The orchestrator's
# Redis usage today is one GET + one SET-with-TTL per request — well
# inside fakeredis's compatibility surface. Day-5+ when we add Redis
# Streams or pub/sub for events, we may need to swap in
# testcontainers-redis; the Day-2-fixup scope doesn't need it.
#
# WHY TestClient INSTEAD OF httpx.AsyncClient + lifespan management?
# FastAPI's TestClient handles lifespan startup + shutdown automatically
# + speaks the right ASGI shape for FastAPI's routing. Per A2.1 — use
# the documented default rather than a hand-built async-client wrapper.
# The TestClient lifespan now (post-PR-#96 fixup) calls `init_redis()`
# at startup; the `fake_redis` auto-use fixture replaces the live
# Redis with fakeredis BEFORE the lifespan runs.
#
# WHY clean_settings_cache + fake_redis AS auto-use FIXTURES?
# Without them, the first test sets the env one way + opens a real-
# Redis connection, the lru_cache locks the settings, and every
# subsequent test in the same session sees the stale state regardless
# of monkeypatch. Auto-use means every test gets the clean slate by
# default.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# `Iterator` types the generator-style fixtures pytest expects (each
# `yield`s once then cleans up).
from collections.abc import Iterator

# `fakeredis.aioredis.FakeRedis` is a drop-in pure-Python async Redis
# client — same interface as `redis.asyncio.Redis` but stores all
# data in-memory. Used by the `fake_redis` fixture so the F10
# idempotency layer in run_turn has a working backend during tests
# without a real Redis container.
import fakeredis.aioredis

# `pytest` itself — for the `@pytest.fixture(...)` decorator and the
# `MonkeyPatch` type that lets fixtures mutate module state safely.
import pytest

# `TestClient` drives the FastAPI app in-process. It manages the
# lifespan (startup + shutdown) automatically; the F10 fixup added
# init_redis()/close_redis() to that lifespan so the fake_redis
# fixture MUST patch the module BEFORE the TestClient `with` block.
from fastapi.testclient import TestClient

# `app.idempotency` is where the F10 fixup lives — we override its
# module-level `_redis` global to a fakeredis client + replace its
# `init_redis` / `close_redis` callables with no-ops so the
# TestClient's lifespan doesn't try to talk to a real Redis.
import app.idempotency as app_idempotency

# `get_settings()` is the lru_cache'd typed-Settings accessor; we
# invalidate it before/after every test so env mutations land.
from app.config import get_settings

# The FastAPI `app` instance the TestClient wraps. Importing it here
# triggers Sentry/Langfuse/logging module-load init paths exactly
# once per pytest session.
from app.main import app


@pytest.fixture(autouse=True)
def clean_settings_cache() -> Iterator[None]:
    """Clear the `get_settings()` lru_cache before AND after each test.

    WHAT: invalidates the cached Settings instance so monkeypatched env
          vars take effect.
    WHEN: pytest invokes this auto-use fixture once per test function.
    WHY:  `app.config.get_settings` is `@lru_cache(maxsize=1)` — once
          called, it returns the same Settings forever. Without this
          fixture, env-var mutations between tests would leak.
    """
    # Pre-test clear — in case a prior test left a stale entry.
    get_settings.cache_clear()
    yield
    # Post-test clear — keeps the next test isolated regardless of
    # whether the current test triggered a settings read.
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[fakeredis.aioredis.FakeRedis]:
    """Swap the live Redis client for an in-memory fakeredis instance.

    WHAT: builds a fresh `fakeredis.aioredis.FakeRedis()`, assigns it
          to `app.idempotency._redis`, and stubs init_redis /
          close_redis to no-ops so the TestClient lifespan doesn't
          attempt a real Redis connection or close.
    WHEN: pytest invokes this auto-use fixture per test function. A
          fresh FakeRedis means each test starts with an empty cache.
    WHY:  the F10 idempotency fixup (Codex PR-#96 BLOCKER 1) added
          Redis to the FastAPI lifespan startup hook. Without this
          fixture every test would fail at lifespan startup with
          `ConnectionRefusedError` against `redis://localhost:6379`.
    """
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Override the module-level _redis directly — the same handle
    # get_redis() returns from inside the run_turn handler.
    monkeypatch.setattr(app_idempotency, "_redis", fake)

    # Stub init_redis to a no-op so the FastAPI lifespan startup hook
    # doesn't overwrite our patched _redis with a real connection.
    async def _noop_init() -> None:
        # `_redis` is already set above; idempotent like the real one.
        return None

    # Stub close_redis similarly — the lifespan shutdown hook would
    # otherwise close the fakeredis client and set _redis back to
    # None, breaking any test that runs the lifespan twice.
    async def _noop_close() -> None:
        return None

    monkeypatch.setattr(app_idempotency, "init_redis", _noop_init)
    monkeypatch.setattr(app_idempotency, "close_redis", _noop_close)

    yield fake

    # No explicit teardown needed — `monkeypatch.setattr` auto-reverts
    # at fixture exit. `fake` is just an in-memory dict; GC handles it.


@pytest.fixture()
def client(fake_redis: fakeredis.aioredis.FakeRedis) -> Iterator[TestClient]:
    """FastAPI TestClient wired to the orchestrator's app.

    WHAT: provides a `requests`-style synchronous client that drives the
          app through its full middleware + routing stack, with
          fakeredis already patched in via the `fake_redis` fixture.
    WHEN: yielded into every test that asks for a `client` parameter.
    WHY:  exercises the real Starlette routing + middleware chain so
          tests reflect how production requests actually flow.
    """
    with TestClient(app) as test_client:
        yield test_client


# ===========================================================================
# RELATED FILES:
#   test_run_turn.py     — uses all three fixtures above
#   ../app/main.py       — the FastAPI `app` TestClient wraps
#   ../app/config.py     — the lru_cache the clean_settings_cache fixture
#                          invalidates
#   ../app/run_turn.py   — the handler the run_turn tests exercise
#   ../app/idempotency.py
#                        — Redis F10 dedup layer the fake_redis fixture
#                          patches
#   __init__.py          — package marker
# ===========================================================================
