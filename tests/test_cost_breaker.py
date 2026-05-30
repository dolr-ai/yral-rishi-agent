"""Phase 19.2 — cost circuit breaker.

Source-inspection + pure-function tests. Live trip behavior is
exercised after deploy via a small script that hammers the LLM
through a test JWT until the cap is hit."""

from decimal import Decimal
from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_default_caps_match_migration_seed():
    """DEFAULTS must equal what migration 026 seeds — same anti-drift
    guard as rate_limiter."""
    src = _read("app/cost_breaker.py")
    seed = _read("migrations/026_cost_breaker_config.sql")
    for key, value in (
        ("per_user_daily_cents", 100),
        ("per_user_daily_alert_cents", 50),
    ):
        assert f'"{key}": {value}' in src
        assert f"('{key}', {value})" in seed


def test_pricing_table_includes_gemini_and_default():
    """Without per-model pricing the breaker can't estimate spend.
    Default key catches unknown-model calls so we still count them."""
    src = _read("app/cost_breaker.py")
    for needed in ("gemini-2.5-flash", "gemini-2.5-pro", "default"):
        assert f'"{needed}"' in src


def test_estimate_cost_cents_pure_function():
    """1k input + 1k output at Flash rates = ~$0.0375 cents (negligible).
    Pinning so the math doesn't silently regress and undercount spend."""
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
    from cost_breaker import estimate_cost_cents

    c = estimate_cost_cents("gemini-2.5-flash", 1000, 1000)
    # 1000 input × 0.0075/1000 + 1000 output × 0.030/1000 = 0.0075 + 0.030
    assert c == Decimal("0.0375")


def test_estimate_cost_unknown_model_falls_back_to_default():
    """A new model name shouldn't make us silently count zero."""
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
    from cost_breaker import estimate_cost_cents

    c = estimate_cost_cents("definitely-not-a-real-model", 1000, 1000)
    # default: 0.05/1k input + 0.15/1k output → 0.05 + 0.15 = 0.20
    assert c == Decimal("0.20")


def test_check_returns_allowed_for_no_user_id():
    """Anonymous calls bypass the per-user breaker (they should still
    hit IP-based rate-limiting from Phase 19.1)."""
    import asyncio
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
    from cost_breaker import check

    allowed, spent, cap = asyncio.run(check(None))
    assert allowed is True
    assert spent == 0
    assert cap == 0


def test_breaker_degrades_open_on_redis_outage():
    """A Redis outage shouldn't refuse user requests. Mirror the
    rate_limiter degrades-open pattern."""
    src = _read("app/cost_breaker.py")
    assert "degrading open" in src or "degrades open" in src


def test_record_writes_both_redis_and_db():
    """Redis = live counter (breaker reads), DB = durable log (billing).
    Both must run; failure of one doesn't block the other."""
    src = _read("app/cost_breaker.py")
    # The Redis incrby happens via a pipe
    assert "pipe.incrbyfloat" in src
    # The DB INSERT happens via the pool
    assert "INSERT INTO llm_cost_log" in src
    # Both wrapped in their own try/except so one failure doesn't
    # short-circuit the other
    assert src.count("logger.warning") >= 4  # several best-effort sites


def test_trip_records_to_db_redis_and_sentry():
    """A trip is a real event — must land in the durable log, Redis
    counter, AND Sentry (so ops sees emerging hotspots)."""
    src = _read("app/cost_breaker.py")
    assert "INSERT INTO cost_breaker_trips" in src
    assert "zadd(_REDIS_TRIPS_KEY" in src
    assert "sentry_sdk.capture_message" in src
    assert "cost_breaker tripped" in src


def test_admin_endpoints_exist():
    """GET + PUT /admin/cost-breaker/config + GET /admin/cost-breaker/status."""
    src = _read("app/routes/admin_dashboard.py")
    assert '@router.get("/admin/cost-breaker/config")' in src
    assert '@router.put("/admin/cost-breaker/config")' in src
    assert '@router.get("/admin/cost-breaker/status")' in src


def test_dashboard_tile_replaces_placeholder():
    """Per the ADHD-observability memory rule, the Phase 19.2
    placeholder MUST flip to live in the same PR as the protection."""
    src = _read("app/routes/admin_dashboard.py")
    # Live tile function exists + is called
    assert "async def _cost_breaker_tile" in src
    assert "await _cost_breaker_tile(pool)" in src
    # Placeholder gone (cost-breaker no longer in the placeholder block)
    assert '_placeholder_tile(\n            "Cost circuit breaker"' not in src


def test_email_digest_section_replaces_placeholder():
    src = _read("app/services/email_digest.py")
    assert "async def _section_cost_breaker" in src
    assert "await _section_cost_breaker(pool)" in src
    assert '_section_placeholder("Cost circuit breaker"' not in src


def test_ai_client_hooks_pre_call_check_and_post_call_record():
    """Without the hook the breaker tracks nothing. Pin both sides:
    the pre-call check function reference + the post-call record."""
    src = _read("app/services/ai_client.py")
    assert "from cost_breaker import check as cost_check" in src
    assert "from cost_breaker import record as _cost_record" in src
    # COST_CAP error code wired so the response path can surface a
    # specific message rather than a generic error
    assert 'error_code="COST_CAP"' in src


def test_migration_026_creates_tables_and_seeds():
    src = _read("migrations/026_cost_breaker_config.sql")
    assert "CREATE TABLE IF NOT EXISTS cost_breaker_config" in src
    assert "CREATE TABLE IF NOT EXISTS llm_cost_log" in src
    assert "CREATE TABLE IF NOT EXISTS cost_breaker_trips" in src
    assert "value_cents BIGINT NOT NULL CHECK (value_cents > 0)" in src
    for key in ("per_user_daily_cents", "per_user_daily_alert_cents"):
        assert f"'{key}'" in src


def test_main_py_hydrates_cost_breaker_config():
    src = _read("app/main.py")
    assert "from cost_breaker import hydrate_from_db as cost_hydrate" in src
    assert "await cost_hydrate(await database.get_pool())" in src
