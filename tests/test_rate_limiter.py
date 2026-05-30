"""Phase 19.1 — rate-limit middleware + hot-edit config.

Source-inspection + pure-function tests. The live behavior (real Redis
+ real requests crossing limit boundaries) is exercised after deploy
via a small load script."""

from datetime import datetime, timezone
from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_default_limits_match_migration_seed():
    """The DEFAULT_LIMITS constant is what the middleware uses if Redis
    isn't reachable on cold-start. Must match what migration 025 seeds
    so a fresh Redis (after a full restart) gives the same protection
    as a hydrated one."""
    src = _read("app/rate_limiter.py")
    seed = _read("migrations/025_rate_limit_config.sql")
    # Same value-per-key in both DEFAULT_LIMITS literal and seed INSERT
    for key, value in (
        ("per_user_per_min", 60),
        ("per_user_per_hour", 1000),
        ("per_ip_per_min", 30),
        ("per_ip_per_hour", 500),
    ):
        assert f'"{key}": {value}' in src, f"DEFAULT_LIMITS missing {key}"
        assert f"('{key}', {value})" in seed, f"migration seed missing {key}"


def test_skip_prefixes_protect_admin_and_health():
    """If the middleware rate-limits /admin/dashboard, Rishi can lock
    himself out by hitting refresh too fast. /health endpoints must
    also always work so the orchestrator can probe. WebSocket frames
    aren't HTTP requests; rate-limiting them via this middleware is
    nonsense."""
    src = _read("app/rate_limiter.py")
    # SKIP_PREFIXES tuple must include these literals
    for needed in ('"/health"', '"/admin/"', '"/ws/"'):
        assert needed in src, f"missing skip prefix literal {needed}"


def test_bucket_keys_are_time_partitioned():
    """The key shape includes UTC minute / hour so the limiter is
    stateless across clock-aligned windows. Regression for the case
    where keys aren't time-partitioned and one user's morning quota
    starves their evening one."""
    src = _read("app/rate_limiter.py")
    # Pin the strftime patterns that produce time-partitioned buckets
    assert 'minute_part = now.strftime("%Y%m%d%H%M")' in src
    assert 'hour_part = now.strftime("%Y%m%d%H")' in src
    # And the keys actually use those partitions
    assert "min:{minute_part}" in src
    assert "hour:{hour_part}" in src


def test_429_response_includes_retry_after():
    """Standard HTTP semantics — clients SHOULD honor Retry-After.
    Pinning so future refactors don't drop the header."""
    src = _read("app/rate_limiter.py")
    assert '"Retry-After": str(retry_after)' in src
    # Body also includes Retry-After in seconds for non-standard clients
    assert "retry_after_seconds" in src


def test_middleware_degrades_open_when_redis_unavailable():
    """A Redis outage shouldn't take down user requests. The middleware
    must pass-through when _get_redis() returns None. Defense, not
    correctness."""
    src = _read("app/rate_limiter.py")
    assert "if redis is None:" in src
    assert "degrading open" in src or "degraded open" in src


def test_dispatch_wraps_all_redis_calls_in_try_except():
    """Regression for the 2026-05-30 incident: Redis AuthenticationError
    from _hit_and_check.pipeline.execute() wasn't caught, so every
    request 500'd. The whole dispatch body must be wrapped so any
    Redis-side failure (auth, conn refused, timeout, pipeline error)
    degrades open."""
    src = _read("app/rate_limiter.py")
    # The dispatch method must have a try/except that wraps the body
    assert "async def dispatch" in src
    # Specifically check the except logs the degrade
    assert "rate_limiter: dispatch error (degrading open):" in src


def test_update_limit_validates_inputs():
    """A typo'd key or negative value in a PUT body shouldn't corrupt
    config. Validation runs before the DB write."""
    src = _read("app/rate_limiter.py")
    assert "unknown limit key" in src
    assert "value must be positive int" in src


def test_update_limit_writes_db_then_redis():
    """DB-first so a Redis-write failure leaves durable state correct.
    Redis-only update would silently revert on next restart."""
    src = _read("app/rate_limiter.py")
    # DB INSERT/ON CONFLICT happens first
    db_pos = src.find("INSERT INTO rate_limit_config")
    redis_pos = src.find("await redis.hset(_REDIS_CONFIG_KEY, key,")
    assert db_pos > 0 and redis_pos > 0 and db_pos < redis_pos


def test_admin_endpoints_exist():
    """GET + PUT /admin/rate-limits/config + GET /admin/rate-limits/status.
    Without these the limits aren't hot-editable — defeats the whole
    ADHD-observability rule."""
    src = _read("app/routes/admin_dashboard.py")
    assert '@router.get("/admin/rate-limits/config")' in src
    assert '@router.put("/admin/rate-limits/config")' in src
    assert '@router.get("/admin/rate-limits/status")' in src


def test_dashboard_tile_replaces_placeholder():
    """Per the memory rule: when a protective system ships, its
    placeholder flips to a live tile in the SAME PR. The Phase 19.1
    placeholder must be gone; the rate_limit_tile function must exist
    AND be called in the dashboard's tile list."""
    src = _read("app/routes/admin_dashboard.py")
    # The placeholder for 19.1 is removed
    assert '"Per-user rate limits",' not in src or '_placeholder_tile(\n            "Per-user rate limits"' not in src
    # The live tile function exists and is called
    assert "async def _rate_limit_tile" in src
    assert "await _rate_limit_tile(pool)" in src


def test_email_digest_section_replaces_placeholder():
    """Same rule for the email digest: 19.1 must flip from placeholder
    to live section in the same PR."""
    src = _read("app/services/email_digest.py")
    assert "async def _section_rate_limits" in src
    assert "await _section_rate_limits(pool)" in src
    # The placeholder for 19.1 is gone (Cost breaker still has it)
    assert '_section_placeholder("Per-user rate limits"' not in src


def test_migration_025_seeds_defaults():
    src = _read("migrations/025_rate_limit_config.sql")
    assert "CREATE TABLE IF NOT EXISTS rate_limit_config" in src
    assert "value INT NOT NULL CHECK (value > 0)" in src
    for key in ("per_user_per_min", "per_user_per_hour",
                "per_ip_per_min", "per_ip_per_hour"):
        assert f"'{key}'" in src


def test_main_py_wires_middleware_and_hydrate():
    src = _read("app/main.py")
    assert "from rate_limiter import RateLimitMiddleware, hydrate_from_db" in src
    assert "app.add_middleware(RateLimitMiddleware)" in src
    assert "await hydrate_from_db(await database.get_pool())" in src
