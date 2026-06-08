"""Phase 21αβ.H11 — real-time LLM cost alerting.

Motivated by the 2026-06-08 quality_scorer leak: ~$22 of unnoticed
Gemini spend over 4 days, caught only because Rishi happened to glance
at Google Cloud billing. At prod scale that's a $400 incident.

Two periodic Sentry alerts + one email-digest section:

  1. Hourly Gemini cost — fire when last-hour Gemini spend > threshold
     (default $1.00/hr). One alert per UTC hour, NX-deduped on Redis.

  2. Async error spike — fire when non-success llm_costs.outcome rows
     in the last 5 min exceed a count threshold (default 10). One
     alert per 5-min bucket, NX-deduped on Redis. Separate from the
     existing async-process gemini-leak guard in llm_registry — that
     one fires on routing-table misconfiguration; this one catches
     runaway error spend that the routing table can't see (e.g. a
     downed provider returning 5xx in a tight loop).

  3. Daily 08:00 IST email digest — yesterday's cost broken down by
     (process, provider), sourced from llm_costs. Adds one new section
     to services.email_digest.

Fail-open semantics throughout: if Redis is down or Sentry can't be
imported, the loop logs and continues. The whole module is best-effort
observability — never block the request path.

Gated by the kill_switch entry "cost_alerts" (env: ENABLE_COST_ALERTS).
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─── config ───────────────────────────────────────────────────────────────

# Last-hour Gemini cost above this fires a Sentry alert (one per hour).
# $1/hr is the conservative default — Gemini steady-state cost on V2 is
# usually < $0.20/hr; sustained > $1/hr means a leak or a spike worth
# eyeballing.
COST_ALERT_HOURLY_GEMINI_USD = float(
    os.environ.get("COST_ALERT_HOURLY_GEMINI_USD", "1.0")
)

# Non-success llm_costs.outcome rows in the last 5 min above this fires
# the runaway-errors alert. 10 errors in 5 min = sustained 2/min — well
# above normal flakiness.
COST_ALERT_ASYNC_ERROR_COUNT = int(
    os.environ.get("COST_ALERT_ASYNC_ERROR_COUNT", "10")
)

# How often the loop wakes to evaluate both checks. 5 min matches the
# async-error bucket; the hourly check naturally falls out of a once-
# per-hour NX key.
COST_ALERT_TICK_SEC = int(os.environ.get("COST_ALERT_TICK_SEC", "300"))


# ─── alert dispatch ───────────────────────────────────────────────────────


async def _try_set_nx(key: str, ttl_sec: int) -> bool:
    """Acquire a deduplication key in Redis. Returns True if THIS process
    is the one allowed to fire the alert (key was newly set); False if
    another replica already fired it during this window OR Redis is down.
    Fail-closed-on-alert (better to drop an alert than fire 5 of them
    every 5 min during an outage)."""
    try:
        from services.session_memory import _get_redis

        redis = await _get_redis()
        if redis is None:
            return False
        result = await redis.set(key, "1", nx=True, ex=ttl_sec)
        return bool(result)
    except Exception as e:
        logger.warning("cost_alerts: NX dedupe failed (treating as not-acquired): %s", e)
        return False


def _capture(level: str, message: str, **tags) -> None:
    """Thin wrapper around sentry_sdk.capture_message. Never raises."""
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            for k, v in tags.items():
                scope.set_tag(k, v)
            sentry_sdk.capture_message(message, level=level)
    except Exception as e:
        logger.warning("cost_alerts: Sentry capture skipped: %s", e)


# ─── alert checks ─────────────────────────────────────────────────────────


async def _check_hourly_gemini_cost(pool) -> dict:
    """Query llm_costs for the last hour of Gemini spend. If above the
    threshold, fire one Sentry alert (NX-deduped on the UTC-hour bucket).
    Returns a small dict for the dashboard / digest preview."""
    row = await pool.fetchrow(
        """
        SELECT COALESCE(SUM(cost_usd), 0)::float AS cost_usd,
               COUNT(*)::int                     AS call_count
        FROM llm_costs
        WHERE provider = 'gemini'
          AND created_at > now() - interval '1 hour'
        """
    )
    cost = float(row["cost_usd"])
    calls = int(row["call_count"])
    fired = False
    if cost > COST_ALERT_HOURLY_GEMINI_USD:
        now = datetime.now(timezone.utc)
        bucket = now.strftime("%Y-%m-%d-%H")
        nx_key = f"cost_alert:hourly_gemini:{bucket}"
        if await _try_set_nx(nx_key, ttl_sec=3600):
            _capture(
                "warning",
                f"Hourly Gemini cost ${cost:.2f} exceeded ${COST_ALERT_HOURLY_GEMINI_USD:.2f}/hr threshold ({calls} calls in last hour)",
                alert="hourly_gemini_cost",
                bucket=bucket,
            )
            fired = True
    return {"cost_usd": cost, "call_count": calls, "fired": fired}


async def _check_async_error_spike(pool) -> dict:
    """Query llm_costs for non-success outcomes in the last 5 min. If
    above the threshold, fire one Sentry alert (NX-deduped on the 5-min
    bucket). 'outcome' enum lives in llm_registry._classify_outcome —
    everything that isn't 'success' counts."""
    row = await pool.fetchrow(
        """
        SELECT COUNT(*)::int                    AS error_count,
               COALESCE(SUM(cost_usd), 0)::float AS error_cost_usd
        FROM llm_costs
        WHERE outcome IS NOT NULL
          AND outcome != 'success'
          AND created_at > now() - interval '5 minutes'
        """
    )
    errors = int(row["error_count"])
    error_cost = float(row["error_cost_usd"])
    fired = False
    if errors > COST_ALERT_ASYNC_ERROR_COUNT:
        now = datetime.now(timezone.utc)
        # 5-min bucket: floor to the nearest 5-minute interval.
        bucket = now.strftime("%Y-%m-%d-%H-") + f"{(now.minute // 5) * 5:02d}"
        nx_key = f"cost_alert:async_errors:{bucket}"
        if await _try_set_nx(nx_key, ttl_sec=300):
            _capture(
                "error",
                f"LLM error spike: {errors} non-success outcomes in last 5 min (threshold {COST_ALERT_ASYNC_ERROR_COUNT}, ${error_cost:.2f} cost)",
                alert="async_error_spike",
                bucket=bucket,
            )
            fired = True
    return {"error_count": errors, "error_cost_usd": error_cost, "fired": fired}


# ─── digest section (consumed by services.email_digest) ───────────────────


async def section_llm_costs_yesterday(pool) -> dict:
    """Build the email-digest section showing yesterday's LLM spend
    broken down by (process, provider). 'Yesterday' = the UTC calendar
    day preceding the digest run. Top 10 rows by cost; rest collapsed."""
    rows = await pool.fetch(
        """
        SELECT process, provider,
               COUNT(*)::int                    AS calls,
               COALESCE(SUM(cost_usd), 0)::float AS cost_usd
        FROM llm_costs
        WHERE created_at >= (current_date - interval '1 day')::timestamp at time zone 'UTC'
          AND created_at <  current_date::timestamp at time zone 'UTC'
        GROUP BY process, provider
        ORDER BY cost_usd DESC
        LIMIT 50
        """
    )
    total = sum(float(r["cost_usd"]) for r in rows)
    lines = [f"Total: ${total:.2f} across {len(rows)} (process, provider) buckets"]
    top = rows[:10]
    if top:
        lines.append("Top 10:")
        for r in top:
            lines.append(
                f"  {r['process']:30s} {r['provider']:14s} "
                f"${float(r['cost_usd']):>7.2f}  ({int(r['calls'])} calls)"
            )
    if len(rows) > 10:
        rest_cost = sum(float(r["cost_usd"]) for r in rows[10:])
        lines.append(f"  (+ {len(rows) - 10} more buckets totaling ${rest_cost:.2f})")
    return {"title": "LLM cost — yesterday", "lines": lines}


# ─── background loop ──────────────────────────────────────────────────────


async def cost_alerts_loop():
    """Sleep COST_ALERT_TICK_SEC between checks. Both checks run every
    tick; NX dedup keeps Sentry from being spammed. Kill-switch gated."""
    from database import get_pool
    from kill_switch import is_enabled

    while True:
        try:
            await asyncio.sleep(COST_ALERT_TICK_SEC)
            if not is_enabled("cost_alerts"):
                continue
            pool = await get_pool()
            await _check_hourly_gemini_cost(pool)
            await _check_async_error_spike(pool)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("cost_alerts_loop tick failed (non-fatal): %s", e)
