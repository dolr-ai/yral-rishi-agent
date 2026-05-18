# ---------------------------------------------------------------------------
# conftest.py — shared pytest fixtures for the orchestrator's tests.
#
# ⭐ START HERE: this file defines the fixtures every test_*.py file in
# `tests/` can use without importing anything. pytest auto-discovers
# conftest.py at collection time and makes its fixtures available by
# parameter name to the tests in the same directory tree.
#
# Today's fixtures:
#   - `clean_settings_cache` — clears the `get_settings()` lru_cache so
#     env-var changes inside a test take effect. Required because
#     `app.config.get_settings` caches the Settings instance forever
#     (cheap on hot path, awkward for tests that need different env).
#   - `client` — FastAPI TestClient wired to `app.main.app`, plus
#     auto-uses `clean_settings_cache` so tests can mutate env via
#     monkeypatch and see fresh settings on the next request.
#
# WHY TestClient INSTEAD OF httpx.AsyncClient + lifespan management?
# FastAPI's TestClient handles lifespan startup + shutdown automatically
# + speaks the right ASGI shape for FastAPI's routing. Per A2.1 — use the
# documented default rather than a hand-built async-client wrapper.
#
# WHY clean_settings_cache AS AN auto-use FIXTURE?
# Without it, the first test sets the env one way, the lru_cache locks
# it, and every subsequent test in the same session sees stale settings
# regardless of monkeypatch. The cache clear runs BEFORE every test so
# each test starts from a clean Settings parse.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
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


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """FastAPI TestClient wired to the orchestrator's app.

    WHAT: provides a `requests`-style synchronous client that drives the
          app through its full middleware + routing stack.
    WHEN: yielded into every test that asks for a `client` parameter.
    WHY:  exercises the real Starlette routing + middleware chain so
          tests reflect how production requests actually flow.
    """
    with TestClient(app) as test_client:
        yield test_client


# ===========================================================================
# RELATED FILES:
#   test_run_turn.py     — uses both fixtures above
#   ../app/main.py       — the FastAPI `app` TestClient wraps
#   ../app/config.py     — the lru_cache the clean_settings_cache fixture
#                          invalidates
#   ../app/run_turn.py   — the handler the run_turn tests exercise
#   __init__.py          — package marker
# ===========================================================================
