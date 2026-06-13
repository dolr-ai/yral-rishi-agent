"""Phase 21αβ.H2 — server-side billing.yral.com integration.

Mirrors mobile's `ChatAccessBillingDataSource.checkChatAccess` so server +
mobile agree on the same access decision for every chat-send.

Gap closed: V2 today trusts that anyone calling POST /messages already
passed the mobile paywall gate. A motivated user with a valid JWT can
bypass the mobile UI and hammer V2 directly → unbounded Gemini cost.
H2 makes V2 call the same `/google/chat-access/check` endpoint
server-side, with Redis caching so the latency cost is ~negligible.

## Resolution order

  1. Redis cache hit → return cached has_access (no network).
  2. Cache miss → call billing.yral.com /google/chat-access/check
     with the (user_id, bot_id) query params mobile uses.
  3. billing.yral.com timeout / 5xx / malformed JSON → log + Sentry
     `capture_message` (warning level) + fail-open (allow the chat).
     Negative cache the fail-open for a short TTL so we don't hammer
     billing during an outage.

## Fail-open posture is INTENTIONAL

A billing outage MUST NOT take down chat. The exposure is bounded by
the cost circuit breaker (21α.B6 / PR #289) which independently caps
per-user spend regardless of billing's opinion. chat-ai itself is
fail-open today (no check at all); fail-open here matches the
symmetry rule + preserves user experience during transient outages.

If the cost circuit breaker isn't merged yet, H2 still ships fail-open
— the exposure window is the duration of a billing outage, which is
rare; the alternative (fail-closed) would mean a transient billing
5xx blocks PAYING users.

## Standing rules

- Mobile contract is sacred. The endpoint path, query params, and
  response shape are byte-identical to mobile's
  ChatAccessBillingDataSource (see brief §2).
- 60s positive / 30s negative TTL — keeps billing.yral.com QPS down
  on steady state but lets a user-buys-access flow recover in <60s.
- 3s timeout on the upstream call. Caller stays under the chat-send
  budget.
"""

import logging

import config

logger = logging.getLogger(__name__)


CHECK_PATH = "/google/chat-access/check"
CACHE_KEY_PREFIX = "chat_access:"

# 60s positive cache: keeps billing.yral.com QPS down on steady state.
# A user who just-bought-access recovers in <60s without operator action.
DEFAULT_CACHE_TTL_SEC = 60

# 30s negative cache: even shorter so a denial → user-pays → retry flow
# unblocks quickly without hammering billing during the brief window.
NEGATIVE_CACHE_TTL_SEC = 30

# 3s upstream timeout. Chat-send already takes 1-3s at the LLM hop;
# we can't add unbounded billing latency on top. 3s is generous —
# billing.yral.com p99 is ~150ms on the existing mobile path.
BILLING_TIMEOUT_SEC = 3.0


class ChatAccessResult:
    """Tri-state result. `fail_open=True` distinguishes a billing-down
    pass-through (we allowed but didn't actually verify) from a real
    positive answer — useful for Sentry tagging + the future
    /admin/billing-cache dashboard."""

    __slots__ = ("has_access", "cache_hit", "fail_open")

    def __init__(
        self, has_access: bool, cache_hit: bool = False, fail_open: bool = False
    ):
        self.has_access = has_access
        self.cache_hit = cache_hit
        self.fail_open = fail_open


# ─── Redis client (lazy, mirrors session_memory pattern) ─────────────────


_redis_client = None


async def _get_redis():
    """Lazily-initialized async Redis client. Mirrors the
    session_memory + cost_alerts pattern: file-first URL (Swarm secret),
    env-var fallback, host/port/password fallback. Returns None when
    Redis is unreachable so callers can degrade (no caching → every
    request hits billing.yral.com, which is fine for an outage but
    expensive long-term)."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        from redis_config import get_redis_url

        redis_url = get_redis_url()
        if redis_url:
            _redis_client = aioredis.from_url(redis_url, decode_responses=True)
        else:
            _redis_client = aioredis.Redis(
                host=config._env("REDIS_HOST", "redis-primary"),
                port=config._env_int("REDIS_PORT", 6379),
                password=config._env("REDIS_PASSWORD") or None,
                decode_responses=True,
            )
        return _redis_client
    except Exception as e:
        logger.warning(
            "billing_client: Redis init failed (degrading to no-cache): %s", e
        )
        return None


async def _cache_get(key: str) -> bool | None:
    """Returns True/False if cached, None on miss (incl. Redis-down)."""
    r = await _get_redis()
    if r is None:
        return None
    try:
        val = await r.get(key)
    except Exception as e:
        logger.warning("billing_client: cache get failed for %s: %s", key, e)
        return None
    if val is None:
        return None
    # decode_responses=True returns str; treat "1" as True, "0" as False
    return val == "1"


async def _cache_set(key: str, has_access: bool, ttl_sec: int) -> None:
    r = await _get_redis()
    if r is None:
        return
    try:
        await r.set(key, "1" if has_access else "0", ex=ttl_sec)
    except Exception as e:
        logger.warning("billing_client: cache set failed for %s: %s", key, e)


# ─── main entry point ───────────────────────────────────────────────────


async def check_chat_access(user_id: str, bot_id: str) -> ChatAccessResult:
    """Return whether `user_id` has paid access to chat with `bot_id`.

    Order: Redis cache → billing.yral.com → fail-open on error.

    `user_id` is the JWT principal_id; `bot_id` is the influencer_id —
    same shape mobile sends to billing.yral.com.
    """
    key = f"{CACHE_KEY_PREFIX}{user_id}:{bot_id}"
    cached = await _cache_get(key)
    if cached is not None:
        return ChatAccessResult(has_access=cached, cache_hit=True)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=BILLING_TIMEOUT_SEC) as client:
            resp = await client.get(
                f"{config.BILLING_URL}{CHECK_PATH}",
                params={"user_id": user_id, "bot_id": bot_id},
            )
        resp.raise_for_status()
        body = resp.json()
        has_access = bool((body.get("data") or {}).get("has_access"))
        ttl = DEFAULT_CACHE_TTL_SEC if has_access else NEGATIVE_CACHE_TTL_SEC
        await _cache_set(key, has_access, ttl)
        return ChatAccessResult(has_access=has_access, cache_hit=False)
    except Exception as e:
        # httpx.HTTPError, TimeoutException, JSONDecodeError, redis errors
        # — anything that prevents getting a real answer falls through to
        # fail-open. We Sentry-tag with billing.* fields so a sustained
        # outage is visible without grepping logs.
        logger.warning(
            "billing_client: billing.yral.com unreachable for user=%s bot=%s: %s",
            user_id,
            bot_id,
            e,
        )
        try:
            import sentry_sdk

            with sentry_sdk.push_scope() as scope:
                scope.set_tag("billing.principal_id", user_id)
                scope.set_tag("billing.bot_id", bot_id)
                scope.set_tag("billing.outcome", "fail_open")
                sentry_sdk.capture_message(
                    f"billing.yral.com check_chat_access failed (fail-open): {e}",
                    level="warning",
                )
        except Exception:
            # Sentry being unavailable must NEVER block chat
            pass
        return ChatAccessResult(has_access=True, cache_hit=False, fail_open=True)
