"""Phase 19.2 — per-user daily LLM cost circuit breaker (DRAFT).

ADR
---
**Goal:** stop a single user (or attacker) from running up unbounded
Gemini cost on agent.rishi.yral.com. The Phase 25 multi-provider
architecture made cost more variable per-call; this breaker is the
last-defense ceiling.

**Mechanism:** Redis counter of $ spent per (user_id, UTC day). Pre-call
check in `llm_registry.call` rejects with `CostCeilingExceeded` when the
user has crossed their ceiling. Post-call increment via the existing
`_record_outcome` cost-recording path.

**Why Redis (not Postgres):** call-rate is high (~hundreds/min cluster-
wide). A Postgres round-trip on every LLM call adds 5-20ms each. Redis
INCRBYFLOAT is <1ms over the overlay network and survives replica
failover via Sentinel. Spend tallies are not authoritative — the
`llm_costs` table is. Redis is just the hot counter; if it disappears
the breaker fails-open (which is the right tradeoff: better to bill the
user than to refuse service silently).

**Default ceiling:** $1.00 per user per UTC day. Hot-editable via
`PATCH /admin/cost-ceiling/{user_id}` (admin JWT-gated, follows the
PATCH /admin/llm-routing pattern). The 19.6 dashboard tile gets a
"users currently near or over ceiling" widget — separate follow-up,
not in this PR.

**Failure mode (Sentry):** when a user hits the ceiling, fire one
Sentry event tagged `cost_ceiling_exceeded` per user per day (de-duped
in Redis so a chatty user doesn't spam Sentry). Operator sees the
event + can lift the ceiling temporarily via the admin endpoint.

**Cost recording integration:** the existing `_record_outcome` in
llm_registry.py already writes to `llm_costs` (migrations 027/028)
with the real $ amount per call. This module piggybacks on that —
NOT a separate cost-source-of-truth. After `_record_outcome` succeeds,
the per-user-day Redis key increments by the same amount.

DRAFT NOTES (Rishi review needed)
---------------------------------
- The default $1/user/day is a starting point. The 19.6 dashboard
  data will inform a better default. Currently config-only.
- The hot-edit endpoint (PATCH /admin/cost-ceiling) is NOT yet wired
  into routes/ — only the service layer is here. Wiring is a follow-up
  PR once the service shape is validated.
- The Redis key naming convention follows session_memory.py + Phase
  19.1 rate_limiter.py. TTL set to 48h so the day-rollover happens
  naturally without explicit cleanup.
- Tests are source-pin (no Redis required in CI); the integration
  test happens against a real Redis in deploy verification.
"""

import datetime
import logging
import os

logger = logging.getLogger(__name__)

# Default ceiling — config-overridable. Hot-edit via admin endpoint
# (not yet wired; planned follow-up PR).
DEFAULT_DAILY_CEILING_USD = float(
    os.environ.get("LLM_PER_USER_DAILY_CEILING_USD", "1.00")
)

# Redis key prefix. Day-keyed so the 48h TTL auto-clears yesterday's data.
_KEY_PREFIX = "llm_cost_breaker:user"

# Sentry de-dup key prefix — fires once per user per day.
_SENTRY_DEDUP_PREFIX = "llm_cost_breaker:sentry_fired"


class CostCeilingExceeded(Exception):
    """Raised by llm_registry.call when the calling user has spent
    >= their daily ceiling. Caller catches → returns 402 / structured
    error to mobile."""

    def __init__(self, user_id: str, spent_usd: float, ceiling_usd: float):
        self.user_id = user_id
        self.spent_usd = spent_usd
        self.ceiling_usd = ceiling_usd
        super().__init__(
            f"User {user_id} has spent ${spent_usd:.4f} of ${ceiling_usd:.2f} daily"
            f" LLM cost ceiling — call rejected"
        )


def _key_for_today(user_id: str) -> str:
    """Day-bucketed Redis key. UTC day so cluster-wide consistency."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return f"{_KEY_PREFIX}:{user_id}:{today}"


def _sentry_dedup_key(user_id: str) -> str:
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return f"{_SENTRY_DEDUP_PREFIX}:{user_id}:{today}"


async def _redis():
    """Same pattern as session_memory.py + rate_limiter.py. Returns
    None if Redis unreachable — caller fails open."""
    try:
        import redis.asyncio as aioredis

        from redis_config import get_redis_url

        url = get_redis_url()
        if url:
            return aioredis.from_url(url, decode_responses=True)
        return aioredis.Redis(
            host=os.environ.get("REDIS_HOST", "redis-primary"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            password=os.environ.get("REDIS_PASSWORD") or None,
            decode_responses=True,
        )
    except Exception as e:
        logger.debug("llm_cost_breaker: Redis init failed (fail-open): %s", e)
        return None


async def current_spend_usd(user_id: str) -> float:
    """Today's spend total for `user_id` in USD. Returns 0.0 on
    Redis-unavailable (fail-open)."""
    redis = await _redis()
    if not redis:
        return 0.0
    try:
        val = await redis.get(_key_for_today(user_id))
        return float(val) if val else 0.0
    except Exception as e:
        logger.debug("llm_cost_breaker: read failed (fail-open): %s", e)
        return 0.0
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass


async def ceiling_for(user_id: str) -> float:
    """Per-user ceiling. Today: env-var-only default. Tomorrow:
    overridable per-user via `llm_user_cost_ceilings` table — same
    PATCH endpoint shape as llm_routing_admin. Returns the EFFECTIVE
    ceiling for this user RIGHT NOW."""
    # Per-user override lookup — placeholder. Future PR adds the
    # cost_ceilings table + admin endpoint. For now, everyone shares
    # the env-default.
    return DEFAULT_DAILY_CEILING_USD


async def check_or_reject(user_id: str | None) -> None:
    """Pre-call gate. Raises CostCeilingExceeded if the user is
    over their daily ceiling. Fails OPEN on Redis-unreachable —
    `current_spend_usd` returns 0.0, so the check passes.

    `user_id=None` (background loops with no user context) is
    bypassed — background cost is bounded by the kill-switch
    framework, not this per-user breaker."""
    if not user_id:
        return
    spent = await current_spend_usd(user_id)
    ceiling = await ceiling_for(user_id)
    if spent >= ceiling:
        await _maybe_fire_sentry(user_id, spent, ceiling)
        raise CostCeilingExceeded(user_id, spent, ceiling)


async def increment(user_id: str | None, cost_usd: float) -> None:
    """Add `cost_usd` to today's spend for `user_id`. Called post-LLM
    by the cost-recording path. No-op on user_id=None, zero cost, or
    Redis-unreachable. Sets TTL to 48h so the day-bucket auto-clears."""
    if not user_id or cost_usd <= 0:
        return
    redis = await _redis()
    if not redis:
        return
    try:
        key = _key_for_today(user_id)
        new_total = await redis.incrbyfloat(key, cost_usd)
        await redis.expire(key, 48 * 3600)
        # If this increment pushed us over the ceiling, fire Sentry
        # (de-duped) so the operator sees it. The post-call increment
        # is the FIRST point we know the cost — pre-call check used
        # the pre-increment value.
        ceiling = await ceiling_for(user_id)
        if new_total >= ceiling:
            await _maybe_fire_sentry(user_id, float(new_total), ceiling)
    except Exception as e:
        logger.debug("llm_cost_breaker: increment failed (non-fatal): %s", e)
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass


async def _maybe_fire_sentry(user_id: str, spent: float, ceiling: float) -> None:
    """Sentry-event de-dup so the operator gets ONE alert per user per
    day, not one per blocked call."""
    redis = await _redis()
    if not redis:
        # Without Redis we can't de-dup; just log and skip to avoid
        # Sentry spam.
        logger.warning(
            "llm_cost_breaker: user=%s spent=$%.4f ceiling=$%.2f "
            "(Redis unavailable for Sentry dedup; skipping alert)",
            user_id,
            spent,
            ceiling,
        )
        return
    try:
        dedup_key = _sentry_dedup_key(user_id)
        # SET NX — atomic "set if not exists." Returns truthy on first
        # set, falsy if already set today.
        first_fire = await redis.set(dedup_key, "1", ex=48 * 3600, nx=True)
        if first_fire:
            try:
                import sentry_sdk

                sentry_sdk.capture_message(
                    f"LLM cost ceiling exceeded — user={user_id} spent=${spent:.4f} ceiling=${ceiling:.2f}",
                    level="warning",
                )
            except Exception as e:
                logger.debug("Sentry capture failed: %s", e)
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass
