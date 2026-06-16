"""Phase 21α.B6 — cost circuit breaker.

Closes the missing ENFORCE layer the 2026-06-14 H2 WON'T FIX decision
depends on. Without B6 the safety net was 2-of-3 (H11 DETECTS but
doesn't block; Phase 19.1 enforces rate-but-not-cost). With B6 in
enforce mode, a bypass user driving cost past threshold gets a 503
automatically — Sentry alert via H11 is no longer the only signal.

## The 5 hard properties (per 2026-06-16 brief)

1. **DEFAULT OPEN.** Config row `b6_enabled` ships `false`. Migration
   alone changes ZERO runtime behaviour. Code is dormant until
   Rishi flips via SQL UPDATE.
2. **FAIL OPEN ON ERRORS.** Every conceivable failure (config table
   missing, Redis down, Postgres down, malformed values, shadow-log
   INSERT raises, …) falls through to `_ALLOW`. Never block on a
   tooling failure — that's how H2 broke alpha on 2026-06-14.
3. **HOT-EDIT KILL SWITCH.** Config lives in `circuit_breaker_config`
   table mirrored to Redis hash `cb:config` with 60s TTL. A single
   `UPDATE … SET value='false' WHERE key='b6_enabled'` disables in
   1 second + the next cache refresh.
4. **SHADOW MODE FIRST.** `b6_enforce` ships `false`. Trips log to
   `circuit_breaker_events` table + Sentry breadcrumb but don't block.
   Only flip enforce after ≥7 days of zero shadow trips on YRAL-team
   principals (Q3 in 2026-06-16 brief).
5. **MOBILE-SAFE RESPONSE SHAPE.** When B6 enforces, raise
   `CostCircuitBreakerOpen`. The FastAPI exception handler in main.py
   translates to **503 + Retry-After: 3600**. Mobile already renders a
   friendly "Try again later" on 503 (gated on Sarvesh confirmation
   per Q2). NEVER 402 (H2's mistake). NEVER 429 (wrong semantic —
   that's Phase 19.1's rate limiter).

## Where the check lives

Inserted at the top of `llm_registry._do_complete()` — the post-PR-#293
chokepoint shared by primary + fallback paths. This protects EVERY LLM
call (chat + SSE + images + 6 background processes), not just the
chat-route paths a middleware would catch.

## Why SQL aggregation + Redis cache, not a Redis counter

PR #289's design used a Redis counter incremented post-call. That
broke on PR #293's primary-vs-fallback split (double-count when both
fire, miscount when fallback bypasses post-cost-recording). We use
the canonical `llm_costs` ledger H11 already aggregates from + cache
the result in Redis with a 10s TTL. Single source of truth; cost
ledger is durable; cache makes the hot-path latency <5ms.
"""

import logging
import os
from typing import NamedTuple

logger = logging.getLogger(__name__)


# ─── exception that triggers the 503 ────────────────────────────────────


class CostCircuitBreakerOpen(Exception):
    """Raised from `cost_breaker.check()` when B6 is in ENFORCE mode AND
    a threshold has tripped. The FastAPI exception handler installed in
    `app/main.py` catches this + returns 503 + Retry-After header.

    Per the 2026-06-16 brief Q2: response shape is the one mobile
    already handles, NOT a new envelope. 503 is what mobile sees today
    on any backend hiccup; the breaker's "service taking a break" is
    exactly that semantic.
    """

    __slots__ = ("scope", "cost_seen_usd", "threshold_usd", "retry_after_sec")

    def __init__(
        self,
        scope: str,
        cost_seen_usd: float,
        threshold_usd: float,
        retry_after_sec: int = 3600,
    ):
        self.scope = scope  # 'per_user_daily' | 'global_hourly'
        self.cost_seen_usd = cost_seen_usd
        self.threshold_usd = threshold_usd
        self.retry_after_sec = retry_after_sec
        super().__init__(
            f"cost circuit breaker open: {scope} "
            f"(${cost_seen_usd:.4f} ≥ ${threshold_usd:.4f})"
        )


# ─── config keys + defaults ─────────────────────────────────────────────
#
# DEFAULTS = in-code fallback used when:
#  - config table missing (migration 040 not applied yet)
#  - specific row missing (operator deleted it)
#  - Redis empty and Postgres unreachable
#  - value malformed (NaN, negative, wrong shape)
#
# All defaults bias toward DEFAULT OPEN: `b6_enabled=False` means the
# whole module is dormant. Threshold defaults match migration seeds
# but matter only when enabled.

_DEFAULTS: dict[str, str] = {
    "b6_enabled": "false",
    "b6_enforce": "false",
    "b6_per_user_daily_usd": "1.0",
    "b6_global_hourly_usd": "20.0",
    "b6_process_allowlist": "",
    "b6_cache_ttl_sec": "10",
    "b6_response_retry_after_sec": "3600",
    "b6_yral_team_principal_ids": "",
}

_CONFIG_CACHE_KEY = "cb:config"
_CONFIG_CACHE_TTL_SEC = 60  # config cache; cost-rollup cache TTL is separate

_USER_DAILY_CACHE_PREFIX = "cb:user_daily:"  # + user_id
_GLOBAL_HOURLY_CACHE_KEY = "cb:global_hourly"


# ─── allow/block result ─────────────────────────────────────────────────


class _CheckResult(NamedTuple):
    allowed: bool
    reason: str  # 'disabled' | 'shadow' | 'allowlist' | 'under_threshold' |
    # 'fail_open_<which>' | 'per_user_daily' | 'global_hourly'
    cost_seen_usd: float = 0.0
    threshold_usd: float = 0.0


_ALLOW_DISABLED = _CheckResult(True, "disabled")
_ALLOW_PROCESS_ALLOWLIST = _CheckResult(True, "allowlist")
_ALLOW_UNDER_THRESHOLD = _CheckResult(True, "under_threshold")


# ─── Redis client (lazy, mirrors rate_limiter pattern) ──────────────────


_redis_client = None


async def _get_redis():
    """Same shape as session_memory + rate_limiter + cost_alerts. None
    on Redis-down so callers fail open (no cache → Postgres fallback)."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        from redis_config import get_redis_url

        url = get_redis_url()
        if url:
            _redis_client = aioredis.from_url(url, decode_responses=True)
        else:
            _redis_client = aioredis.Redis(
                host=os.environ.get("REDIS_HOST", "redis-primary"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                password=os.environ.get("REDIS_PASSWORD") or None,
                decode_responses=True,
            )
        return _redis_client
    except Exception as e:
        logger.warning("cost_breaker: Redis init failed (fail open): %s", e)
        return None


# ─── config load / hot-edit ─────────────────────────────────────────────


async def get_config() -> dict[str, str]:
    """Read current config. Redis-first (fast O(1)), fall through to
    Postgres on cache miss, fall through to in-code DEFAULTS on any
    error. The fail-open posture means a malformed/missing config never
    blocks the call path."""
    redis = await _get_redis()
    if redis is not None:
        try:
            vals = await redis.hgetall(_CONFIG_CACHE_KEY)
            if vals:
                # Merge over defaults so any key missing from Redis still
                # resolves to a safe value
                merged = dict(_DEFAULTS)
                merged.update({k: v for k, v in vals.items() if k in _DEFAULTS})
                return merged
        except Exception as e:
            logger.warning("cost_breaker: redis config read failed (fail open): %s", e)

    # Cache miss or Redis down — try Postgres
    try:
        from database import get_pool

        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT key, value FROM circuit_breaker_config WHERE key = ANY($1::text[])",
            list(_DEFAULTS.keys()),
        )
        merged = dict(_DEFAULTS)
        for r in rows:
            merged[r["key"]] = r["value"]
        # Best-effort re-populate Redis for next reader
        if redis is not None:
            try:
                await redis.hset(_CONFIG_CACHE_KEY, mapping=merged)
                await redis.expire(_CONFIG_CACHE_KEY, _CONFIG_CACHE_TTL_SEC)
            except Exception:
                pass  # cache write failure must NEVER block
        return merged
    except Exception as e:
        # Table missing (migration 040 not applied) or DB down — every
        # value falls back to DEFAULTS, which means b6_enabled='false',
        # which means DEFAULT OPEN. This is the canonical fail-open path.
        logger.warning(
            "cost_breaker: db config read failed (fail open with defaults): %s", e
        )
        return dict(_DEFAULTS)


async def update_config(key: str, value: str, updated_by: str) -> None:
    """Hot-edit one config row. Writes DB first (durable) then Redis
    (live cache). Mirrors rate_limiter.update_limit semantics so a
    Redis-write failure leaves the durable DB state correct."""
    if key not in _DEFAULTS:
        raise ValueError(f"unknown config key: {key!r}")
    from database import get_pool

    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO circuit_breaker_config (key, value, updated_at, updated_by)
        VALUES ($1, $2, NOW(), $3)
        ON CONFLICT (key) DO UPDATE SET
            value      = EXCLUDED.value,
            updated_at = NOW(),
            updated_by = EXCLUDED.updated_by
        """,
        key,
        value,
        updated_by,
    )
    redis = await _get_redis()
    if redis is not None:
        try:
            await redis.hset(_CONFIG_CACHE_KEY, key, value)
            await redis.expire(_CONFIG_CACHE_KEY, _CONFIG_CACHE_TTL_SEC)
        except Exception as e:
            logger.warning("cost_breaker: redis cache update failed (DB ok): %s", e)


def _parse_float(s: str | None, fallback: float) -> float:
    try:
        v = float(s)
        return v if v >= 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _parse_int(s: str | None, fallback: int) -> int:
    try:
        v = int(s)
        return v if v > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _parse_bool(s: str | None) -> bool:
    return (s or "").lower() in ("true", "1", "yes", "on")


def _parse_csv(s: str | None) -> tuple[str, ...]:
    if not s:
        return ()
    return tuple(x.strip() for x in s.split(",") if x.strip())


# ─── cost rollup queries (cached) ───────────────────────────────────────


async def _user_daily_cost(user_id: str, cache_ttl_sec: int) -> float:
    """Sum of `llm_costs.cost_usd` for this user since the start of the
    UTC day. Cached in Redis with the configured TTL (10s default).

    Cache miss → SQL aggregation. SQL or Redis errors → 0.0 (fail open).
    The fail-open semantic means: if we can't measure, we don't block.
    """
    cache_key = _USER_DAILY_CACHE_PREFIX + user_id
    redis = await _get_redis()
    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached is not None:
                return _parse_float(cached, 0.0)
        except Exception as e:
            logger.warning(
                "cost_breaker: user_daily cache read failed (fail open): %s", e
            )

    try:
        from database import get_pool

        pool = await get_pool()
        row = await pool.fetchrow(
            """
            SELECT COALESCE(SUM(cost_usd), 0)::float AS spent
            FROM llm_costs
            WHERE user_id = $1
              AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC')
            """,
            user_id,
        )
        spent = float(row["spent"]) if row else 0.0
    except Exception as e:
        logger.warning(
            "cost_breaker: user_daily aggregation failed (fail open with 0): %s", e
        )
        return 0.0

    if redis is not None:
        try:
            await redis.set(cache_key, str(spent), ex=cache_ttl_sec)
        except Exception:
            pass  # cache write best-effort
    return spent


async def _global_hourly_cost(cache_ttl_sec: int) -> float:
    """Sum of `llm_costs.cost_usd` across all users in the last rolling
    hour. Same fail-open semantics as `_user_daily_cost`."""
    redis = await _get_redis()
    if redis is not None:
        try:
            cached = await redis.get(_GLOBAL_HOURLY_CACHE_KEY)
            if cached is not None:
                return _parse_float(cached, 0.0)
        except Exception as e:
            logger.warning(
                "cost_breaker: global_hourly cache read failed (fail open): %s", e
            )

    try:
        from database import get_pool

        pool = await get_pool()
        row = await pool.fetchrow(
            """
            SELECT COALESCE(SUM(cost_usd), 0)::float AS spent
            FROM llm_costs
            WHERE created_at > now() - interval '1 hour'
            """
        )
        spent = float(row["spent"]) if row else 0.0
    except Exception as e:
        logger.warning(
            "cost_breaker: global_hourly aggregation failed (fail open with 0): %s", e
        )
        return 0.0

    if redis is not None:
        try:
            await redis.set(_GLOBAL_HOURLY_CACHE_KEY, str(spent), ex=cache_ttl_sec)
        except Exception:
            pass
    return spent


# ─── event logging ──────────────────────────────────────────────────────


async def _log_event(
    *,
    user_id: str | None,
    process: str | None,
    provider: str | None,
    scope: str,
    cost_seen_usd: float,
    threshold_usd: float,
    enforce_mode: bool,
    call_blocked: bool,
) -> None:
    """Write one row to `circuit_breaker_events`. Best-effort: a row-
    write failure must NEVER block the LLM call (that would defeat the
    fail-open posture). Sentry breadcrumb (NOT capture_message — too
    noisy during shadow calibration) for filterable triage."""
    try:
        from database import get_pool

        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO circuit_breaker_events
                (user_id, process, provider, scope, cost_seen_usd,
                 threshold_usd, enforce_mode, call_blocked)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            user_id,
            process,
            provider,
            scope,
            cost_seen_usd,
            threshold_usd,
            enforce_mode,
            call_blocked,
        )
    except Exception as e:
        logger.warning("cost_breaker: event log INSERT failed (non-fatal): %s", e)

    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            category="cost-breaker",
            level="warning",
            message=(
                f"B6 {'block' if call_blocked else 'shadow'} trip: "
                f"{scope} ${cost_seen_usd:.4f}/${threshold_usd:.4f}"
            ),
            data={
                "scope": scope,
                "user_id": user_id,
                "process": process,
                "provider": provider,
                "cost_seen_usd": cost_seen_usd,
                "threshold_usd": threshold_usd,
                "enforce_mode": enforce_mode,
                "call_blocked": call_blocked,
            },
        )
    except Exception:
        pass  # Sentry being unavailable must NEVER block chat


# ─── the check ──────────────────────────────────────────────────────────


async def check(
    *,
    user_id: str | None,
    process: str,
    provider: str,
) -> _CheckResult:
    """Run the per-user-daily + global-hourly checks. Returns
    `_CheckResult`. Caller (`llm_registry._do_complete`) decides what
    to do based on `allowed`:

      - True  → proceed with the LLM call
      - False → raise `CostCircuitBreakerOpen(scope, cost, threshold)`

    In SHADOW mode (`b6_enforce=false`), returns True even on threshold
    trip; the trip is still logged so the 7-day shadow review can count
    it. The caller has no way to tell shadow apart from a true allow —
    that's intentional, the contract is "allowed/not allowed."

    FAIL OPEN: any unhandled exception returns `_CheckResult(True,
    "fail_open_unexpected")`. The async-friendly outer guard keeps this
    function from EVER raising into the chat path.
    """
    try:
        return await _check_inner(user_id=user_id, process=process, provider=provider)
    except Exception as e:
        # Belt-and-braces: any unexpected exception (corrupted Redis,
        # database connection refused mid-query, programmer error
        # surfacing as TypeError, …) falls through to allow.
        logger.warning(
            "cost_breaker: unexpected exception (fail open): %s: %s",
            type(e).__name__,
            e,
        )
        return _CheckResult(True, "fail_open_unexpected")


async def _check_inner(
    *,
    user_id: str | None,
    process: str,
    provider: str,
) -> _CheckResult:
    cfg = await get_config()

    # DEFAULT OPEN: master kill switch.
    if not _parse_bool(cfg.get("b6_enabled")):
        return _ALLOW_DISABLED

    # Process allowlist bypass (e.g. admin tools that must always run).
    allowlist = _parse_csv(cfg.get("b6_process_allowlist"))
    if process in allowlist:
        return _ALLOW_PROCESS_ALLOWLIST

    enforce = _parse_bool(cfg.get("b6_enforce"))
    cache_ttl = _parse_int(cfg.get("b6_cache_ttl_sec"), 10)
    per_user_threshold = _parse_float(cfg.get("b6_per_user_daily_usd"), 1.0)
    global_threshold = _parse_float(cfg.get("b6_global_hourly_usd"), 20.0)

    # Per-user-daily check (only if we have a user_id — background
    # processes with user_id=None skip this; they're still subject to
    # the global-hourly check below).
    if user_id:
        spent_today = await _user_daily_cost(user_id, cache_ttl)
        if spent_today >= per_user_threshold:
            await _log_event(
                user_id=user_id,
                process=process,
                provider=provider,
                scope="per_user_daily",
                cost_seen_usd=spent_today,
                threshold_usd=per_user_threshold,
                enforce_mode=enforce,
                call_blocked=enforce,
            )
            if enforce:
                return _CheckResult(
                    False, "per_user_daily", spent_today, per_user_threshold
                )
            # Shadow mode: logged but allowed
            return _CheckResult(True, "shadow", spent_today, per_user_threshold)

    # Global-hourly check
    spent_this_hour = await _global_hourly_cost(cache_ttl)
    if spent_this_hour >= global_threshold:
        await _log_event(
            user_id=user_id,
            process=process,
            provider=provider,
            scope="global_hourly",
            cost_seen_usd=spent_this_hour,
            threshold_usd=global_threshold,
            enforce_mode=enforce,
            call_blocked=enforce,
        )
        if enforce:
            return _CheckResult(
                False, "global_hourly", spent_this_hour, global_threshold
            )
        return _CheckResult(True, "shadow", spent_this_hour, global_threshold)

    return _ALLOW_UNDER_THRESHOLD


# ─── enforcement helper — caller raises this ────────────────────────────


def raise_if_blocked(result: _CheckResult, retry_after_sec: int = 3600) -> None:
    """If the check came back blocked, raise `CostCircuitBreakerOpen`.
    Caller wraps `check()` + this in one place (llm_registry); the
    FastAPI exception handler in main.py translates the exception
    to a 503 + Retry-After response."""
    if not result.allowed:
        raise CostCircuitBreakerOpen(
            scope=result.reason,
            cost_seen_usd=result.cost_seen_usd,
            threshold_usd=result.threshold_usd,
            retry_after_sec=retry_after_sec,
        )


# ─── helpers for admin endpoint + dashboard tile ────────────────────────


async def recent_events(limit: int = 100, since_hours: int = 24) -> list[dict]:
    """Read the recent shadow + enforce trips for the admin endpoint /
    dashboard. Cheap query on the partial index."""
    try:
        from database import get_pool

        pool = await get_pool()
        rows = await pool.fetch(
            f"""
            SELECT id, occurred_at, user_id, process, provider, scope,
                   cost_seen_usd, threshold_usd, enforce_mode, call_blocked
            FROM circuit_breaker_events
            WHERE occurred_at > now() - interval '{int(since_hours)} hours'
            ORDER BY occurred_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("cost_breaker: recent_events query failed: %s", e)
        return []


async def status_summary() -> dict:
    """One-shot snapshot for the admin endpoint + dashboard tile.
    Includes the current config + last 24h trip counts by scope."""
    cfg = await get_config()
    counts: dict[str, int] = {"per_user_daily": 0, "global_hourly": 0, "blocked": 0}
    try:
        from database import get_pool

        pool = await get_pool()
        rows = await pool.fetch(
            """
            SELECT scope,
                   COUNT(*)::int                                AS total,
                   COUNT(*) FILTER (WHERE call_blocked)::int    AS blocked
            FROM circuit_breaker_events
            WHERE occurred_at > now() - interval '24 hours'
            GROUP BY scope
            """
        )
        for r in rows:
            counts[r["scope"]] = int(r["total"])
            counts["blocked"] += int(r["blocked"])
    except Exception as e:
        logger.warning("cost_breaker: status_summary query failed: %s", e)

    return {
        "config": cfg,
        "last_24h_trips": counts,
        "yral_team_principal_ids": list(
            _parse_csv(cfg.get("b6_yral_team_principal_ids"))
        ),
    }
