# ---------------------------------------------------------------------------
# conftest.py — shared pytest fixtures for tests/contract/.
#
# ⭐ START HERE: every test in this folder gets `client` (placeholder
# flag ON — the default for contract tests) or `client_flag_off`
# (placeholder flag OFF — for the 503 path) injected automatically.
# No autouse Redis mock anymore — Codex round-3 BLOCKER 2 swapped
# /health/ready from a real sync `redis.ping()` to a 503-fallback
# stub, so tests no longer need to mock Redis to keep the readiness
# probe quiet. Tests that want to exercise the future 200 path
# monkey-patch `health_routes._check_redis_reachable` to True
# explicitly.
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
# WHY NO LONGER AN AUTOUSE REDIS MOCK?
# Codex PR #97 round-2 BLOCKER 5 wired /health/ready to a sync
# `redis.ping()` call + introduced an autouse mock to keep tests
# from flaking against the dev's local Redis. Codex round-3 BLOCKER 2
# replaced the sync ping with the F9-honest 503-fallback stub +
# coordinator preference: ship the 503 now, wire the real async
# Sentinel-aware check in a follow-up PR once DEP-006 (Session 1
# Sentinel config) lands. With the stub now returning False
# unconditionally, no Redis connection happens during tests — the
# autouse fixture became redundant. Tests that want the future 200
# happy-path can monkey-patch `health_routes._check_redis_reachable`
# directly.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# pytest — defines the @fixture decorator + monkeypatch primitive.
import pytest

# fastapi.testclient — wraps the FastAPI app in a synchronous test
# client; httpx underneath, no real socket binding.
from fastapi.testclient import TestClient

# feature_flag module — the dependency `client` overrides to no-op
# (placeholder gate becomes a pass-through so the stub body runs).
from app.api.feature_flag import require_day_2_placeholder_flag_enabled

# main module — the FastAPI app instance under test; both clients wrap
# this instance + share the same routing + middleware stack.
from app.main import app


def _flag_on_noop() -> None:
    """Override that makes the placeholder-flag dependency a no-op.

    WHAT: returns None, so handlers proceed as if the flag were True.
    WHEN: applied via app.dependency_overrides in the `client` fixture.
    WHY:  every contract test runs with the flag ON to hit the real
          handler bodies; the 503-on-flag-off path has its own fixture.
    """
    return None


# Default Authorization header injected by `client` + `client_flag_off`
# so Day-2 + round-1 test bodies (~50 tests) don't need per-call header
# parameters. The placeholder auth dep accepts ANY non-empty Bearer
# token; this string is purely a "I sent a header" signal for the
# tests, NOT a real credential. Added per Codex PR #97 round-5 ITEM 4.
_DEFAULT_TEST_AUTH_HEADERS = {"Authorization": "Bearer test-placeholder-token-r5"}


@pytest.fixture
def client():
    """A FastAPI TestClient where the Day-2 placeholder flag is ON +
    a default `Authorization: Bearer ...` header is auto-sent.

    WHAT: dependency-overrides require_day_2_placeholder_flag_enabled to
          the no-op above so every handler runs its stub body; bakes a
          default Bearer header into every outgoing request via
          TestClient's constructor-level `headers=` kwarg so the new
          ITEM-4 placeholder auth dep accepts the request.
    WHEN: every contract test that asserts an endpoint's happy-path
          envelope + DTO shape uses this fixture.
    WHY:  contract assertions are about the WIRE shape; we need the
          handler body to actually run for that. Default-headers
          absorbs the ITEM-4 auth wiring without touching ~50 test
          bodies that don't care about the auth gate (the auth-edge
          tests use `client_no_auth` instead).
    """
    app.dependency_overrides[require_day_2_placeholder_flag_enabled] = _flag_on_noop
    try:
        with TestClient(app, headers=_DEFAULT_TEST_AUTH_HEADERS) as test_client:
            yield test_client
    finally:
        # Restore the real dependency so the next test (which may use
        # `client_flag_off`) sees the default behavior.
        app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)


@pytest.fixture
def client_flag_off():
    """A FastAPI TestClient where the Day-2 placeholder flag is OFF +
    a default `Authorization: Bearer ...` header is auto-sent.

    WHAT: no dependency override on the placeholder gate (so the real
          gate's 503 fires); same default Bearer header as `client` so
          tests reach the flag dep AFTER passing the auth dep.
    WHEN: tests that assert the production-default behavior (no
          placeholder responses served) use this fixture.
    WHY:  the production-safety gate is contract-critical; if it
          silently disabled, a half-built v2 could serve stubs to real
          mobile traffic. Auth header is required so tests reach the
          flag dep (not blocked at the auth dep with 401).
    """
    # Explicit pop in case a prior test in the same session left an
    # override behind (defense in depth — fixture teardown should have
    # done this, but pop-if-present is idempotent).
    app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)
    with TestClient(app, headers=_DEFAULT_TEST_AUTH_HEADERS) as test_client:
        yield test_client


@pytest.fixture
def client_no_auth():
    """A FastAPI TestClient that does NOT auto-send Authorization +
    placeholder flag ON.

    WHAT: like `client` but without the default Bearer header. Used by
          the ITEM-4 auth-edge tests to assert the placeholder auth dep
          raises 401 envelope when the header is missing or malformed.
    WHEN: tests that explicitly probe the no-auth path (missing-header,
          malformed-Bearer, empty-token).
    WHY:  proves the auth gate is wired — the handler is UNREACHABLE
          without a valid Bearer header. Health probes (which don't
          depend on the auth gate per F9) can also use this client to
          verify they answer without auth.
    """
    app.dependency_overrides[require_day_2_placeholder_flag_enabled] = _flag_on_noop
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)


@pytest.fixture
def client_no_auth_flag_off():
    """A FastAPI TestClient with NO auth header + placeholder flag OFF.

    WHAT: no default auth header + no flag override.
    WHEN: rare — used by tests asserting "without auth, the 401 path
          fires BEFORE the 503 path." Dependency-evaluation order
          matters for some contract assertions.
    WHY:  defensive coverage for the dep-resolution order (auth dep
          on the router runs BEFORE per-handler placeholder-flag dep).
    """
    app.dependency_overrides.pop(require_day_2_placeholder_flag_enabled, None)
    with TestClient(app) as test_client:
        yield test_client


# ===========================================================================
# RELATED FILES:
#   ../../app/api/feature_flag.py        — the dependency being overridden
#   ../../app/api/auth_placeholder.py    — placeholder auth dep applied to
#                                          chat + influencer routers per
#                                          Codex PR #97 round-5 ITEM 4;
#                                          `client` + `client_flag_off`
#                                          send a default Bearer header to
#                                          satisfy it
#   ../../app/api/health_routes.py       — owns the real Sentinel-aware
#                                          readiness probe; tests
#                                          monkey-patch
#                                          `_check_redis_reachable` for
#                                          deterministic 200/503 results
#   ../../app/main.py                    — the FastAPI app instance under test
#   test_chat_routes.py                  — uses `client` + `client_flag_off`
#   test_influencer_routes.py            — same
#   test_health_routes.py                — uses `client_flag_off`
#   test_handler_auth_placeholder.py     — uses `client_no_auth` for the
#                                          ITEM-4 missing-auth tests
# ===========================================================================
