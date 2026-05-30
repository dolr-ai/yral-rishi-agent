"""Phase 19.2 — per-user daily LLM cost circuit breaker.

Why this shape
--------------
Same ADHD rule from memory feedback-adhd-observability-and-security-baseline:
  (a) the protection — refuse_or_count(user_id, …)
  (b) dashboard tile (live, flipping the placeholder)
  (c) email-digest section
  (d) hot-edit knob via PUT /admin/cost-breaker/config

Storage same as rate_limiter: cost_breaker_config table is durable
state; Redis holds the live values + per-user per-day spend counter
for fast O(1) checks on the request path.

Where this hooks in
-------------------
ai_client.generate_response + generate_response_stream are the LLM
choke points. They already accept `user_id: str | None = None`. The
caller pattern:

    if not await cost_breaker.check_and_record(user_id, model,
                                                 input_tokens, output_tokens, pool):
        return blocked_response

Pre-call (check): if the user's day-to-date spend ALREADY exceeds the
cap, refuse the call. Post-call (record): add this call's cost to the
counter. The pre-call check uses the pre-call counter value; small
amount of over-spend at the boundary (one extra call) is acceptable
for a defense control.

Pricing model
-------------
Per-1k-token rates. Defaults match Gemini Flash + OpenRouter common
models (2026-05 pricing). Keep updated as providers change rates.
"""

import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)


# ─── pricing (USD cents per 1k tokens) ──────────────────────────────────
#
# Source of truth is provider docs. Update when prices change. Numbers
# below are conservative — we round UP for a defense margin (overstate
# the cost a bit so the breaker trips slightly earlier than the literal
# bill).

PRICE_CENTS_PER_1K_TOKENS = {
    # Gemini Flash 2.5 — ~$0.075 / 1M input, $0.30 / 1M output → cents/1k
    "gemini-2.5-flash": {"in": Decimal("0.0075"), "out": Decimal("0.030")},
    "gemini-2.0-flash": {"in": Decimal("0.0075"), "out": Decimal("0.030")},
    "gemini-1.5-flash": {"in": Decimal("0.0075"), "out": Decimal("0.030")},
    # Gemini Pro — about 10x Flash
    "gemini-2.5-pro": {"in": Decimal("0.075"), "out": Decimal("0.30")},
    "gemini-1.5-pro": {"in": Decimal("0.075"), "out": Decimal("0.30")},
    # OpenRouter — varies wildly by model. Default to a conservative
    # mid-range estimate. Caller can override by passing the actual model.
    "default": {"in": Decimal("0.05"), "out": Decimal("0.15")},
}


def estimate_cost_cents(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Multiply tokens × per-1k rate. Returns Decimal cents."""
    rates = PRICE_CENTS_PER_1K_TOKENS.get(model, PRICE_CENTS_PER_1K_TOKENS["default"])
    return (
        Decimal(input_tokens) * rates["in"] / 1000
        + Decimal(output_tokens) * rates["out"] / 1000
    )


# ─── config keys ─────────────────────────────────────────────────────────

CONFIG_KEYS = ("per_user_daily_cents", "per_user_daily_alert_cents")
DEFAULTS = {"per_user_daily_cents": 100, "per_user_daily_alert_cents": 50}

_REDIS_CONFIG_KEY = "cost:config"
_REDIS_TRIPS_KEY = "cost:trips:24h"


# ─── lazy Redis (mirrors rate_limiter / session_memory) ─────────────────


_redis_client = None


async def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        url = os.environ.get("REDIS_URL")
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
        logger.warning("cost_breaker: Redis init failed (degrading open): %s", e)
        return None


# ─── config load/save ────────────────────────────────────────────────────


async def get_current_caps() -> dict[str, int]:
    redis = await _get_redis()
    if redis is None:
        return dict(DEFAULTS)
    try:
        vals = await redis.hgetall(_REDIS_CONFIG_KEY)
        if not vals:
            return dict(DEFAULTS)
        return {k: int(v) for k, v in vals.items() if k in CONFIG_KEYS}
    except Exception as e:
        logger.warning("cost_breaker: config read failed: %s", e)
        return dict(DEFAULTS)


async def hydrate_from_db(pool) -> None:
    redis = await _get_redis()
    if redis is None:
        return
    try:
        rows = await pool.fetch("SELECT key, value_cents FROM cost_breaker_config")
        if not rows:
            return
        await redis.hset(
            _REDIS_CONFIG_KEY, mapping={r["key"]: str(r["value_cents"]) for r in rows}
        )
    except Exception as e:
        logger.warning("cost_breaker: hydrate failed: %s", e)


async def update_cap(pool, key: str, value_cents: int, updated_by: str) -> None:
    if key not in CONFIG_KEYS:
        raise ValueError(f"unknown config key: {key}")
    if not isinstance(value_cents, int) or value_cents <= 0:
        raise ValueError(f"value_cents must be positive int, got {value_cents!r}")
    await pool.execute(
        """
        INSERT INTO cost_breaker_config (key, value_cents, updated_at, updated_by)
        VALUES ($1, $2, NOW(), $3)
        ON CONFLICT (key) DO UPDATE SET
            value_cents = EXCLUDED.value_cents,
            updated_at = NOW(),
            updated_by = EXCLUDED.updated_by
        """,
        key,
        value_cents,
        updated_by,
    )
    redis = await _get_redis()
    if redis is not None:
        try:
            await redis.hset(_REDIS_CONFIG_KEY, key, str(value_cents))
        except Exception as e:
            logger.warning("cost_breaker: redis sync failed (DB ok): %s", e)


# ─── pre-call check ──────────────────────────────────────────────────────


def _today_key(user_id: str, today: date) -> str:
    return f"cost:user:{user_id}:day:{today.isoformat()}"


async def check(user_id: str) -> tuple[bool, Decimal, int]:
    """Return (allowed, spent_cents, cap_cents). allowed=True means the
    user is under the cap and the next call can proceed.

    Degrades open: if Redis is unavailable, allow the call (defense,
    not correctness — see rate_limiter for the same pattern)."""
    if not user_id:
        # Anonymous calls aren't tracked per-user — let through. IP-based
        # cost control could come in a future PR but isn't needed today.
        return True, Decimal(0), 0
    redis = await _get_redis()
    if redis is None:
        return True, Decimal(0), 0
    try:
        caps = await get_current_caps()
        cap = caps.get("per_user_daily_cents", DEFAULTS["per_user_daily_cents"])
        today = datetime.now(timezone.utc).date()
        raw = await redis.get(_today_key(user_id, today))
        spent = Decimal(raw) if raw else Decimal(0)
        return spent < cap, spent, cap
    except Exception as e:
        logger.warning("cost_breaker: check failed (degrading open): %s", e)
        return True, Decimal(0), 0


# ─── post-call record ────────────────────────────────────────────────────


async def record(
    pool,
    user_id: str | None,
    model: str,
    input_tokens: int,
    output_tokens: int,
    request_id: str | None = None,
) -> Decimal:
    """Add this call's cost to the user's daily counter (Redis) AND
    durable log (DB). Returns the cost in cents.

    Both writes are best-effort — a Redis outage doesn't block the
    durable DB write; a DB write failure doesn't block the Redis
    counter (which the breaker actually reads from)."""
    cost = estimate_cost_cents(model, input_tokens, output_tokens)
    if not user_id:
        return cost

    # Redis increment — what the breaker actually reads
    redis = await _get_redis()
    if redis is not None:
        try:
            today = datetime.now(timezone.utc).date()
            key = _today_key(user_id, today)
            pipe = redis.pipeline()
            pipe.incrbyfloat(key, float(cost))
            # Auto-expire 48h after the day starts so old counters don't
            # accumulate in Redis. 48h gives a 24h grace for time-zone
            # confusion.
            pipe.expire(key, 86400 * 2)
            await pipe.execute()
        except Exception as e:
            logger.warning("cost_breaker: redis record failed: %s", e)

    # Durable DB log — used for post-mortem + billing reconciliation
    if pool is not None:
        try:
            await pool.execute(
                """
                INSERT INTO llm_cost_log
                    (user_id, day, model, input_tokens, output_tokens,
                     cost_cents, request_id)
                VALUES ($1, CURRENT_DATE, $2, $3, $4, $5, $6)
                """,
                user_id,
                model,
                input_tokens,
                output_tokens,
                cost,
                request_id,
            )
        except Exception as e:
            logger.warning("cost_breaker: db log failed: %s", e)

    return cost


async def record_trip(pool, user_id: str, spent_cents: Decimal, cap_cents: int):
    """Append a trip event to the durable log + Redis sorted set.
    Sentry alert fires here so ops sees emerging hotspots."""
    if pool is not None:
        try:
            await pool.execute(
                """
                INSERT INTO cost_breaker_trips
                    (user_id, day, spent_cents, cap_cents)
                VALUES ($1, CURRENT_DATE, $2, $3)
                """,
                user_id,
                spent_cents,
                cap_cents,
            )
        except Exception as e:
            logger.warning("cost_breaker: db trip log failed: %s", e)

    redis = await _get_redis()
    if redis is not None:
        try:
            import time

            now_ts = int(time.time())
            await redis.zadd(_REDIS_TRIPS_KEY, {f"{user_id}:{now_ts}": now_ts})
            await redis.zremrangebyscore(_REDIS_TRIPS_KEY, 0, now_ts - 86400)
            await redis.expire(_REDIS_TRIPS_KEY, 90000)
        except Exception as e:
            logger.warning("cost_breaker: redis trip log failed: %s", e)

    # Soft Sentry alert — if SDK isn't present, no-op
    try:
        import sentry_sdk

        sentry_sdk.capture_message(
            f"cost_breaker tripped: user={user_id} spent={spent_cents} cap={cap_cents}",
            level="error",
        )
    except Exception:
        pass


# ─── status helper for dashboard + admin ─────────────────────────────────


async def get_status(pool) -> dict:
    caps = await get_current_caps()
    redis = await _get_redis()
    trips_24h = 0
    top_spenders: list[dict] = []
    if redis is not None:
        try:
            trips_24h = int(await redis.zcard(_REDIS_TRIPS_KEY))
        except Exception as e:
            logger.warning("cost_breaker: trips read failed: %s", e)
    # Top 10 spenders today from the durable log
    if pool is not None:
        try:
            rows = await pool.fetch(
                """
                SELECT user_id, SUM(cost_cents) AS spent_cents,
                       COUNT(*) AS calls
                FROM llm_cost_log
                WHERE day = CURRENT_DATE
                GROUP BY user_id
                ORDER BY spent_cents DESC
                LIMIT 10
                """
            )
            top_spenders = [
                {
                    "user_id": r["user_id"],
                    "spent_cents": float(r["spent_cents"]),
                    "calls": int(r["calls"]),
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("cost_breaker: top spenders read failed: %s", e)
    return {
        "redis_available": redis is not None,
        "caps": caps,
        "trips_24h": trips_24h,
        "top_spenders_today": top_spenders,
    }
