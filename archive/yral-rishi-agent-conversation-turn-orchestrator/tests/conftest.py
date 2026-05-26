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

# `Iterator` types the synchronous generator-style fixtures pytest
# expects (each `yield`s once then cleans up). `AsyncIterator` is the
# same shape for the `async_client` fixture used by the concurrent-
# POST regression test.
from collections.abc import AsyncIterator, Iterator

# `fakeredis.aioredis.FakeRedis` is a drop-in pure-Python async Redis
# client — same interface as `redis.asyncio.Redis` but stores all
# data in-memory. Used by the `fake_redis` fixture so the F10
# idempotency layer in run_turn has a working backend during tests
# without a real Redis container.
import fakeredis.aioredis

# `httpx.ASGITransport` + `httpx.AsyncClient` drive the FastAPI app
# in-process WITHIN the test's event loop. Used by the `async_client`
# fixture for the concurrent-POST test which can't use the sync
# TestClient (TestClient spins its own event loop for lifespan, which
# wouldn't share the fakeredis state correctly across two truly
# concurrent posts under `asyncio.gather`).
import httpx

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
# `init_redis` / `close_redis` callables with empty stubs so the
# TestClient's lifespan doesn't try to talk to a real Redis.
import app.idempotency as app_idempotency

# Capture the REAL `init_redis` at conftest module-load time. The
# `fake_redis` auto-use fixture later monkeypatches
# `app.idempotency.init_redis` to an empty stub; that replacement
# only affects the module-attribute lookup, not this local
# reference. Tests that need the unstubbed function (e.g. the
# round-4 BLOCKER 1 production-fail-closed regression test) use
# `_REAL_INIT_REDIS_FOR_TESTS()` to bypass the stub.
from app.idempotency import init_redis as _REAL_INIT_REDIS_FOR_TESTS

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

    # Stub `init_redis` to an empty-body coroutine so the FastAPI
    # lifespan startup hook doesn't overwrite our patched _redis
    # with a real connection. Renamed from `_noop_init` per Codex
    # round-3 BLOCKER 3 (B2 disallows the `noop` abbreviation).
    async def empty_initialize_redis_for_tests() -> None:
        # `_redis` is already set above; idempotent like the real one.
        return None

    # Stub `close_redis` similarly — the lifespan shutdown hook would
    # otherwise close the fakeredis client and set _redis back to
    # None, breaking any test that runs the lifespan twice. Renamed
    # from `_noop_close` per Codex round-3 BLOCKER 3.
    async def empty_close_redis_for_tests() -> None:
        return None

    monkeypatch.setattr(
        app_idempotency, "init_redis", empty_initialize_redis_for_tests
    )
    monkeypatch.setattr(
        app_idempotency, "close_redis", empty_close_redis_for_tests
    )

    # ---------------------------------------------------------------
    # Day-5 — also stub the Soul File + LLM lifespan helpers.
    # ---------------------------------------------------------------
    # The Day-5 PR added `init_soul_file_client()` +
    # `init_default_llm_client()` to the FastAPI lifespan. Both call
    # `get_settings()` at startup, which pre-fills the lru_cache
    # BEFORE the test body's `monkeypatch.setenv(...)` runs. Without
    # stubbing these to no-ops, the settings cache locks in the
    # default Settings (real-LLM=False, etc.) + every test that flips
    # an env var via monkeypatch silently fails.
    #
    # Tests that need real settings still work — `clean_settings_cache`
    # autouse clears the cache before each test body, so once the
    # lifespan no-ops here, the test body's first `get_settings()`
    # call (inside the route handler) reads the fresh env.
    #
    # The Day-5 tests separately stub the singletons via
    # `monkeypatch.setattr("app.run_turn.get_default_llm_client", ...)`
    # + the soul_file_client equivalent, so the lifespan-init no-ops
    # don't matter for them either.
    import app.soul_file_client as app_soul_file_client
    import app.llm_client as app_llm_client

    async def empty_initialize_soul_file_client_for_tests() -> None:
        return None

    async def empty_close_soul_file_client_for_tests() -> None:
        return None

    def empty_initialize_default_llm_client_for_tests() -> None:
        return None

    def empty_close_default_llm_client_for_tests() -> None:
        return None

    monkeypatch.setattr(
        app_soul_file_client,
        "init_soul_file_client",
        empty_initialize_soul_file_client_for_tests,
    )
    monkeypatch.setattr(
        app_soul_file_client,
        "close_soul_file_client",
        empty_close_soul_file_client_for_tests,
    )
    monkeypatch.setattr(
        app_llm_client,
        "init_default_llm_client",
        empty_initialize_default_llm_client_for_tests,
    )
    monkeypatch.setattr(
        app_llm_client,
        "close_default_llm_client",
        empty_close_default_llm_client_for_tests,
    )

    # The lifespan imports these names INTO app.main at module load
    # time. Python import-shadowing means the patches above only
    # affect the source modules; the main.py-side references stay
    # the originals. Patch those too — same pattern PR #96 round-3
    # established for `mark_complete`.
    import app.main as app_main

    monkeypatch.setattr(
        app_main,
        "init_soul_file_client",
        empty_initialize_soul_file_client_for_tests,
    )
    monkeypatch.setattr(
        app_main,
        "close_soul_file_client",
        empty_close_soul_file_client_for_tests,
    )
    monkeypatch.setattr(
        app_main,
        "init_default_llm_client",
        empty_initialize_default_llm_client_for_tests,
    )
    monkeypatch.setattr(
        app_main,
        "close_default_llm_client",
        empty_close_default_llm_client_for_tests,
    )

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


@pytest.fixture()
async def async_client(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> AsyncIterator[httpx.AsyncClient]:
    """httpx.AsyncClient + ASGITransport for the concurrent-POST test.

    WHAT: yields an `httpx.AsyncClient` driving the FastAPI app
          in-process via `ASGITransport`. Runs in the test's event
          loop so two `asyncio.gather`-ed POSTs share the same
          fakeredis state + truly race on the `SET NX` critical
          section in `app.idempotency.acquire_or_check`.
    WHEN: requested only by the concurrent-POST test
          (`test_run_turn_concurrent_same_key_same_body_*`); other
          tests use the sync TestClient `client` fixture.
    WHY:  TestClient spins its own event loop for lifespan, which
          means two concurrent calls via TestClient.post would NOT
          share the same loop and the SET NX race wouldn't fire as
          a real concurrent race. AsyncClient + ASGITransport keeps
          everything on ONE event loop — the directive's intended
          regression gate for Codex round-3 BLOCKER 1b.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver",
    ) as test_client:
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
