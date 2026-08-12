"""Phase 21α.B6 — cost circuit breaker.

Two categories of tests:

  1. SOURCE-PIN tests — same shape as test_21ab_H11_cost_alerts.py.
     Defend the wiring at the symbol level so a refactor can't
     quietly disable the breaker, drop the 5 hard properties, or
     break the FastAPI/llm_registry integration points.

  2. BEHAVIOURAL tests — exercise `cost_breaker.check()` with stubbed
     Redis + Postgres to prove DEFAULT OPEN, SHADOW MODE, FAIL OPEN,
     and ENFORCE MODE behaviours match the design.

Real DB / real Redis is NOT required — every IO is monkey-patched.
Behavioural tests use plain `asyncio.run(...)` instead of
`pytest.mark.asyncio` because pytest-asyncio isn't a CI dependency
in this repo (matches the lighter-touch style of the rest of tests/).
"""

import asyncio
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Same shim other tests in this repo use — make `from services import …`
# importable. The repo doesn't run pytest with PYTHONPATH=app set.


# ══════════════════════════════════════════════════════════════════════
# 1. SOURCE-PIN tests (static; cheap; catch wiring regressions)
# ══════════════════════════════════════════════════════════════════════


def test_migration_040_creates_both_tables_and_index():
    src = (REPO / "migrations" / "040_circuit_breaker.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS circuit_breaker_config" in src
    assert "CREATE TABLE IF NOT EXISTS circuit_breaker_events" in src
    assert "CREATE INDEX IF NOT EXISTS idx_llm_costs_user_recent" in src


def test_migration_040_seeds_default_open():
    """The 5 hard properties #1 (DEFAULT OPEN) + #4 (SHADOW MODE
    FIRST) are encoded as migration seeds. If either drifts, the
    breaker would ship live + enforcing — exactly what 2026-06-16
    brief Q3 forbids."""
    src = (REPO / "migrations" / "040_circuit_breaker.sql").read_text()
    assert "('b6_enabled',                  'false'" in src
    assert "('b6_enforce',                  'false'" in src


def test_migration_040_seeds_q1_threshold_and_q2_retry_after():
    """Q1 (per-user-daily = $1) + Q2 (Retry-After: 3600) locked
    by Rishi 2026-06-16. Pin the literals so a future tuning PR
    has to consciously edit this test."""
    src = (REPO / "migrations" / "040_circuit_breaker.sql").read_text()
    assert "('b6_per_user_daily_usd',       '1.0'" in src
    assert "('b6_response_retry_after_sec', '3600'" in src


def test_migration_040_seeds_rishi_principal_in_yral_team():
    """Q3 (zero-trip on YRAL team principals) requires the
    principal list to ship pre-seeded with at least Rishi's
    known principal. Sarvesh/Saikat/Neha get hot-edited in
    via PATCH /admin/cost-breaker/config once known."""
    src = (REPO / "migrations" / "040_circuit_breaker.sql").read_text()
    assert "b6_yral_team_principal_ids" in src
    assert "k2adj-ox4zs-gaocq-d5ctl-ggx5k-ekucz-rvgnv-4pddz-mkjzc-es4cj-aae" in src


def test_migration_040_has_squawk_preamble():
    """Per I-Mig2 rule (#340): every migration sets lock_timeout +
    statement_timeout to bound the worst-case impact."""
    src = (REPO / "migrations" / "040_circuit_breaker.sql").read_text()
    assert "SET lock_timeout = '3s';" in src
    assert "SET statement_timeout = '60s';" in src


def test_cost_breaker_module_exposes_5_hard_properties():
    """All five hard properties from the 2026-06-16 brief must be
    discoverable in the module docstring so a future reader sees
    the contract before changing code."""
    src = (REPO / "app" / "services" / "cost_breaker.py").read_text()
    assert "DEFAULT OPEN" in src
    assert "FAIL OPEN" in src
    assert "HOT-EDIT KILL SWITCH" in src
    assert "SHADOW MODE FIRST" in src
    assert "MOBILE-SAFE RESPONSE SHAPE" in src


def test_cost_breaker_defines_required_symbols():
    src = (REPO / "app" / "services" / "cost_breaker.py").read_text()
    assert "class CostCircuitBreakerOpen" in src
    assert "async def check" in src
    assert "async def get_config" in src
    assert "async def update_config" in src
    assert "async def recent_events" in src
    assert "async def status_summary" in src
    assert "def raise_if_blocked" in src


def test_cost_breaker_defaults_are_default_open():
    """The in-code DEFAULTS fall-through chain must keep the breaker
    OFF when DB + Redis are both unreachable. If this drifts the
    very first deploy on a fresh node could start blocking."""
    from services import cost_breaker

    assert cost_breaker._DEFAULTS["b6_enabled"] == "false"
    assert cost_breaker._DEFAULTS["b6_enforce"] == "false"


def test_llm_registry_wires_b6_check_before_call():
    """The check MUST live in `_do_complete` — the post-PR-#293
    chokepoint shared by primary + fallback. A middleware-only
    integration would miss the 6 background processes."""
    src = (REPO / "app" / "services" / "llm_registry.py").read_text()
    assert "async def _do_complete" in src
    assert "from services import cost_breaker" in src
    assert "await _cb.check(" in src
    assert "_cb.raise_if_blocked(" in src


def test_main_wires_cost_breaker_exception_handler():
    """The 503 response shape is the H2-incident lesson: never
    invent a new error code mobile hasn't seen. The handler must
    return 503 + Retry-After (NOT 402 or 429)."""
    src = (REPO / "app" / "main.py").read_text()
    assert "from services.cost_breaker import CostCircuitBreakerOpen" in src
    assert "@app.exception_handler(CostCircuitBreakerOpen)" in src
    assert "status_code=503" in src
    assert 'headers={"Retry-After": str(exc.retry_after_sec)}' in src


def test_main_wires_admin_router():
    src = (REPO / "app" / "main.py").read_text()
    assert "admin_cost_breaker_router" in src


def test_admin_router_exposes_three_surfaces():
    src = (REPO / "app" / "routes" / "admin_cost_breaker.py").read_text()
    assert '"/admin/cost-breaker.json"' in src
    assert '"/admin/cost-breaker/config"' in src
    assert '"/admin/cost-breaker"' in src
    # PATCH is the hot-edit path used for the 1-second kill switch.
    assert "@router.patch(" in src


# ══════════════════════════════════════════════════════════════════════
# 2. BEHAVIOURAL tests — `cost_breaker.check()` decision matrix
# ══════════════════════════════════════════════════════════════════════


def _stub_module(monkeypatch, cfg: dict, user_daily: float = 0.0, hourly: float = 0.0):
    """Patch the cost_breaker module IO. Returns a list that
    `_log_event` appends to so the test can assert what shadow rows
    would have been written. No real DB / Redis touched."""
    from services import cost_breaker as cb

    async def fake_get_config():
        return {**cb._DEFAULTS, **cfg}

    async def fake_user_daily(user_id, ttl):
        return user_daily

    async def fake_global_hourly(ttl):
        return hourly

    logged: list[dict] = []

    async def fake_log_event(**kw):
        logged.append(kw)

    monkeypatch.setattr(cb, "get_config", fake_get_config)
    monkeypatch.setattr(cb, "_user_daily_cost", fake_user_daily)
    monkeypatch.setattr(cb, "_global_hourly_cost", fake_global_hourly)
    monkeypatch.setattr(cb, "_log_event", fake_log_event)
    return logged


def test_default_open_disabled_returns_allow(monkeypatch):
    """Hard property #1. b6_enabled=false → ALLOW + reason='disabled'.
    No log event written — disabled means dormant."""
    from services import cost_breaker as cb

    logged = _stub_module(monkeypatch, cfg={"b6_enabled": "false"}, user_daily=99.0)
    result = asyncio.run(cb.check(user_id="u1", process="chat", provider="gemini"))
    assert result.allowed is True
    assert result.reason == "disabled"
    assert logged == []


def test_shadow_mode_logs_but_allows(monkeypatch):
    """Hard property #4. b6_enabled=true + b6_enforce=false + over
    threshold → ALLOW (with reason='shadow') AND log a row."""
    from services import cost_breaker as cb

    logged = _stub_module(
        monkeypatch,
        cfg={
            "b6_enabled": "true",
            "b6_enforce": "false",
            "b6_per_user_daily_usd": "1.0",
        },
        user_daily=2.5,
    )
    result = asyncio.run(cb.check(user_id="u1", process="chat", provider="gemini"))
    assert result.allowed is True
    assert result.reason == "shadow"
    assert result.cost_seen_usd == 2.5
    assert result.threshold_usd == 1.0
    assert len(logged) == 1
    assert logged[0]["scope"] == "per_user_daily"
    assert logged[0]["enforce_mode"] is False
    assert logged[0]["call_blocked"] is False


def test_enforce_mode_blocks_per_user_daily(monkeypatch):
    from services import cost_breaker as cb

    logged = _stub_module(
        monkeypatch,
        cfg={
            "b6_enabled": "true",
            "b6_enforce": "true",
            "b6_per_user_daily_usd": "1.0",
        },
        user_daily=1.5,
    )
    result = asyncio.run(cb.check(user_id="u1", process="chat", provider="gemini"))
    assert result.allowed is False
    assert result.reason == "per_user_daily"
    assert len(logged) == 1
    assert logged[0]["enforce_mode"] is True
    assert logged[0]["call_blocked"] is True


def test_enforce_mode_blocks_global_hourly(monkeypatch):
    """Global-hourly check fires even when user_id=None (background
    process) — that's the only line of defence for the 6 background
    loops."""
    from services import cost_breaker as cb

    logged = _stub_module(
        monkeypatch,
        cfg={
            "b6_enabled": "true",
            "b6_enforce": "true",
            "b6_global_hourly_usd": "20.0",
        },
        hourly=25.0,
    )
    result = asyncio.run(
        cb.check(user_id=None, process="background_video", provider="vllm")
    )
    assert result.allowed is False
    assert result.reason == "global_hourly"
    assert logged[0]["scope"] == "global_hourly"


def test_under_threshold_allows_silently(monkeypatch):
    from services import cost_breaker as cb

    logged = _stub_module(
        monkeypatch,
        cfg={
            "b6_enabled": "true",
            "b6_enforce": "true",
            "b6_per_user_daily_usd": "1.0",
            "b6_global_hourly_usd": "20.0",
        },
        user_daily=0.3,
        hourly=5.0,
    )
    result = asyncio.run(cb.check(user_id="u1", process="chat", provider="gemini"))
    assert result.allowed is True
    assert result.reason == "under_threshold"
    assert logged == []


def test_process_allowlist_bypasses_check(monkeypatch):
    """Operator escape valve. Allowlisted process bypasses both
    thresholds even in ENFORCE mode."""
    from services import cost_breaker as cb

    logged = _stub_module(
        monkeypatch,
        cfg={
            "b6_enabled": "true",
            "b6_enforce": "true",
            "b6_process_allowlist": "admin_tool,smoke_test",
            "b6_per_user_daily_usd": "1.0",
        },
        user_daily=999.0,
    )
    result = asyncio.run(
        cb.check(user_id="u1", process="admin_tool", provider="gemini")
    )
    assert result.allowed is True
    assert result.reason == "allowlist"
    assert logged == []


def test_fail_open_on_inner_exception(monkeypatch):
    """Hard property #2. If `_check_inner` raises (e.g. a programmer
    error introduces a TypeError, DB raises mid-query, Redis client
    crashes), `check()` MUST return allowed=True. The opposite of
    H2's mistake."""
    from services import cost_breaker as cb

    async def boom(**kw):
        raise RuntimeError("simulated catastrophic failure")

    monkeypatch.setattr(cb, "_check_inner", boom)
    result = asyncio.run(cb.check(user_id="u1", process="chat", provider="gemini"))
    assert result.allowed is True
    assert result.reason == "fail_open_unexpected"


def test_fail_open_when_get_config_raises(monkeypatch):
    """get_config itself is wrapped — if Postgres + Redis are both
    down (table missing + Redis crashed), check() returns allow."""
    from services import cost_breaker as cb

    async def boom():
        raise RuntimeError("db + redis both unreachable")

    monkeypatch.setattr(cb, "get_config", boom)
    result = asyncio.run(cb.check(user_id="u1", process="chat", provider="gemini"))
    assert result.allowed is True
    assert result.reason == "fail_open_unexpected"


# ══════════════════════════════════════════════════════════════════════
# 3. Exception → 503 + Retry-After contract
# ══════════════════════════════════════════════════════════════════════


def test_raise_if_blocked_carries_retry_after():
    """The exception MUST surface retry_after_sec so the FastAPI
    handler can set the header. If this drops, mobile loses the
    'how long to wait' signal and may spin in a tight retry."""
    from services.cost_breaker import (
        CostCircuitBreakerOpen,
        _CheckResult,
        raise_if_blocked,
    )

    blocked = _CheckResult(False, "per_user_daily", 2.5, 1.0)
    with pytest.raises(CostCircuitBreakerOpen) as exc_info:
        raise_if_blocked(blocked, retry_after_sec=3600)
    assert exc_info.value.scope == "per_user_daily"
    assert exc_info.value.cost_seen_usd == 2.5
    assert exc_info.value.threshold_usd == 1.0
    assert exc_info.value.retry_after_sec == 3600


def test_raise_if_blocked_noop_when_allowed():
    from services.cost_breaker import _CheckResult, raise_if_blocked

    raise_if_blocked(_CheckResult(True, "disabled"), retry_after_sec=3600)
    raise_if_blocked(_CheckResult(True, "shadow", 2.5, 1.0), retry_after_sec=3600)


# ══════════════════════════════════════════════════════════════════════
# 4. Parser defensiveness — malformed config never blocks
# ══════════════════════════════════════════════════════════════════════


def test_parse_float_fallback_on_garbage():
    from services.cost_breaker import _parse_float

    assert _parse_float(None, 1.0) == 1.0
    assert _parse_float("NaN_for_breakfast", 1.0) == 1.0
    assert _parse_float("-5.0", 1.0) == 1.0  # negative thresholds = nonsense
    assert _parse_float("0.5", 1.0) == 0.5


def test_parse_int_fallback_on_garbage():
    from services.cost_breaker import _parse_int

    assert _parse_int(None, 3600) == 3600
    assert _parse_int("not_a_number", 3600) == 3600
    assert _parse_int("0", 3600) == 3600  # 0 retry-after is nonsense
    assert _parse_int("-1", 3600) == 3600
    assert _parse_int("60", 3600) == 60


def test_parse_bool_only_true_for_truthy_strings():
    from services.cost_breaker import _parse_bool

    for truthy in ("true", "True", "TRUE", "1", "yes", "on"):
        assert _parse_bool(truthy) is True, truthy
    for falsy in (None, "", "false", "False", "0", "no", "off", "random"):
        assert _parse_bool(falsy) is False, falsy


def test_parse_csv_strips_and_ignores_empties():
    from services.cost_breaker import _parse_csv

    assert _parse_csv("") == ()
    assert _parse_csv(None) == ()
    assert _parse_csv("a, b ,c,,d") == ("a", "b", "c", "d")
