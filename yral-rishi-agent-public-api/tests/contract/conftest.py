# ---------------------------------------------------------------------------
# conftest.py — shared pytest fixtures for tests/contract/.
#
# ⭐ START HERE: every test in this folder gets `client` (placeholder
# flag ON — the default for contract tests) or `client_flag_off`
# (placeholder flag OFF — for the 503 path) injected automatically.
#
# WHY TWO CLIENTS INSTEAD OF ONE FLIPPABLE FLAG?
# Tests should be order-independent. If a test flips a global flag and
# forgets to flip it back, the next test sees the wrong state. Two
# separate TestClient instances — each with its own dependency override
# graph — gives every test a clean known-state without per-test
# fixture-cleanup boilerplate.
#
# WHY OVERRIDE THE DEPENDENCY INSTEAD OF SETTING AN ENV VAR?
# Two reasons:
#   1. `get_settings()` is lru_cache'd. Setting the env var after the
#      first call wouldn't take effect without `get_settings.cache_clear()`,
#      and the cache is process-wide — clearing it from a test would
#      affect Sentry / Langfuse init that already ran at import time.
#   2. FastAPI's `app.dependency_overrides` is designed for exactly this
#      use case: replace a dependency function with a test-shape function
#      for the duration of the override block.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import pytest
from fastapi.testclient import TestClient

from app.api.feature_flag import require_day_2_placeholder_flag_enabled
from app.main import app


def _flag_on_noop() -> None:
    """Override that makes the placeholder-flag dependency a no-op.

    WHAT: returns None, so handlers proceed as if the flag were True.
    WHEN: applied via app.dependency_overrides in the `client` fixture.
    WHY:  every contract test runs with the flag ON to hit the real
          handler bodies; the 503-on-flag-off path has its own fixture.
    """
    return None


@pytest.fixture
def client():
    """A FastAPI TestClient where the Day-2 placeholder flag is ON.

    WHAT: dependency-overrides require_day_2_placeholder_flag_enabled to
          the no-op above, so every handler runs its stub body and
          returns the schema-valid ApiResponse.
    WHEN: every contract test that asserts an endpoint's happy-path
          envelope + DTO shape uses this fixture.
    WHY:  contract assertions are about the WIRE shape; we need the
          handler body to actually run for that.
    """
    app.dependency_overrides[require_day_2_placeholder_flag_enabled] = _flag_on_noop
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        # Restore the real dependency so the next test (which may use
        # `client_flag_off`) sees the default behavior.
        app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)


@pytest.fixture
def client_flag_off():
    """A FastAPI TestClient where the Day-2 placeholder flag is OFF.

    WHAT: no dependency override — the real
          require_day_2_placeholder_flag_enabled reads settings (default
          False) and raises 503.
    WHEN: tests that assert the production-default behavior (no
          placeholder responses served) use this fixture.
    WHY:  the production-safety gate is contract-critical; if it
          silently disabled, a half-built v2 could serve stubs to real
          mobile traffic.
    """
    # Explicit pop in case a prior test in the same session left an
    # override behind (defense in depth — fixture teardown should have
    # done this, but pop-if-present is idempotent).
    app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)
    with TestClient(app) as test_client:
        yield test_client


# ===========================================================================
# RELATED FILES:
#   ../../app/api/feature_flag.py  — the dependency being overridden
#   ../../app/main.py              — the FastAPI app instance under test
#   test_chat_routes.py            — uses both `client` and `client_flag_off`
#   test_influencer_routes.py      — same
#   test_health_routes.py          — uses `client_flag_off` only (health
#                                    endpoints do NOT depend on the flag —
#                                    that's part of the contract for them)
# ===========================================================================
