"""Spicy chat gate — auth handoff between the native app and the
spicy web brand (track 2a).

Design: docs/spicy-chat-gate-design-2026-06-28.md §4.7
Contract: docs/amorae-v2-contract-2026-07-01.md §1

The problem this solves: mobile is logged in with a native JWT, but
the spicy brand lives on a different domain — cookies/localStorage
don't cross, and the raw JWT MUST NOT ride in the URL (query string
leaks via history, browser cloud-sync, Referer, server/analytics logs;
our JWT is long-lived + not sig-verified → a leak is account-takeover).

Solution: a one-time exchange ticket. The native app calls `mint()`
with its JWT to get an opaque ~60s single-use ticket, embeds only
the ticket in the URL, and the web brand's server calls `exchange()`
server-to-server (X-Amorae-Secret) to redeem it → gets the user
identity, marks the ticket consumed.

Storage: Redis with TTL. Single-use is enforced by the atomic
`DEL` on exchange — if `DEL` returns 0 the ticket was already
consumed (or expired, or never existed) and we reject.

Redis degrade-open: if Redis is unavailable at mint time we raise
so the app surfaces a real error (a silent failure would land the
user on the web brand with no ticket to exchange). We do NOT
degrade-open at exchange time either — no ticket = no identity
= amorae has nothing to sign the user in with.
"""

import json
import logging
import os
import secrets

logger = logging.getLogger(__name__)


# 60s per contract §1 + design §4.7. Long enough for the app→browser
# hop + the web page to POST /handoff/exchange. Short enough that a
# leaked URL is nearly-immediately dead.
TICKET_TTL_SEC = 60

# 256 bits of entropy in url-safe form. Not a JWT — the ticket carries
# no user info on its own; the mapping ticket→identity lives ONLY in
# Redis, gated by the shared secret on the exchange endpoint.
_TICKET_BYTES = 32

_REDIS_KEY_PREFIX = "spicy:handoff:"


def _redis_key(ticket: str) -> str:
    return _REDIS_KEY_PREFIX + ticket


# ─── Redis lazy client (mirrors discovery_feed / cost_breaker pattern) ──


_redis_client = None


async def _get_redis():
    """Same shape as discovery_feed._get_redis. Returns None on init
    failure. Callers must convert None to an appropriate error — see
    class docstring; degrade-open is NOT acceptable for the handoff
    (no Redis = no ticket = no login on the brand)."""
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
        logger.warning("spicy_handoff: Redis init failed: %s", e)
        return None


async def mint(
    *,
    user_id: str,
    bot_handle: str | None = None,
    is_anonymous: bool = False,
) -> str:
    """Create a fresh single-use ticket bound to (user_id, bot_handle,
    is_anonymous) with TICKET_TTL_SEC TTL. Returns the opaque ticket
    string. Raises RuntimeError on Redis failure — the route layer
    turns that into a 503 so the app surfaces a real error instead of
    landing the user on the brand with a ticket that will never
    exchange."""
    redis = await _get_redis()
    if redis is None:
        raise RuntimeError("spicy_handoff: Redis unavailable at mint time")

    ticket = secrets.token_urlsafe(_TICKET_BYTES)
    payload = json.dumps(
        {
            "user_id": user_id,
            "bot_handle": bot_handle,
            "is_anonymous": bool(is_anonymous),
        }
    )
    # SETEX = SET + EX in one round trip; also NX would let us reject
    # a collision, but 256 bits of entropy makes collision practically
    # impossible — bet on TTL alone to keep the code simple.
    await redis.setex(_redis_key(ticket), TICKET_TTL_SEC, payload)
    return ticket


async def exchange(ticket: str) -> dict | None:
    """Redeem a ticket exactly once. Returns the identity payload
    on success, None on any failure (unknown / expired / already
    consumed / malformed / Redis down).

    Single-use enforcement: GETDEL is atomic — a concurrent second
    exchange either gets the payload OR gets nothing, never both.
    Redis <6.2 falls back to GET+DEL which is racy; we require 6.2+
    (matches the swarm's redis:7 image)."""
    redis = await _get_redis()
    if redis is None:
        return None
    try:
        raw = await redis.getdel(_redis_key(ticket))
    except Exception as e:
        logger.warning("spicy_handoff: exchange Redis error: %s", e)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("spicy_handoff: exchange payload parse failed: %s", e)
        return None
