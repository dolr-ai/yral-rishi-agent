"""Redis pub/sub for cross-replica LLM routing cache invalidation.

The 2026-06-08 bug: agent runs as 2 replicas. Each has its own in-memory
`_db_overrides` cache in `llm_registry`. When an admin clicks Save/Reset
on the routing dashboard, the request lands on ONE replica — that
replica writes to the DB and refreshes ITS cache, but the other replica
keeps the stale cache and serves chat requests against it. Result: 50%
of chat-routing decisions diverge from the dashboard state.

Fix: when any replica writes to `llm_process_config`, broadcast a
"reload your cache" message via Redis pub/sub. Every replica runs a
background subscriber that listens on the same channel and calls
`llm_registry.reload_config_from_db(pool)` on receipt.

Mirror of `services/websocket_manager.py`'s Redis pattern:
  - Same Redis Sentinel substrate (already up + verified per DEV-6)
  - `_get_redis()` helper with `redis_config.get_redis_url()` fallback
  - Graceful degradation when Redis is unreachable: publish is a no-op
    (cache stays per-replica, original bug behavior — no regression).

Channel: `llm_routing_invalidate`.
Message shape: JSON `{"reason": "<short string>", "ts": <unix>}`. The
reason is for log-trail context; the receipt itself is what triggers
the cache reload, not the message contents. Subscribers don't read the
process name — they refresh the entire cache. This costs one extra DB
roundtrip per Save/Reset event, ~5ms each — completely fine at our
operator-action volume (handful of clicks per day).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time

logger = logging.getLogger(__name__)

LLM_ROUTING_CHANNEL = "llm_routing_invalidate"


async def _get_redis():
    """Return an async Redis client or None if Redis is not configured.
    Identical shape to websocket_manager._get_redis — same env fallback
    chain so both subsystems see the same Redis even in dev/test."""
    try:
        import redis.asyncio as aioredis

        try:
            from redis_config import get_redis_url

            url = get_redis_url()
            if url:
                return aioredis.from_url(url)
        except Exception:
            pass

        import config as _config

        host = _config._env("REDIS_HOST", "redis-primary")
        port = _config._env_int("REDIS_PORT", 6379)
        password = _config._env("REDIS_PASSWORD")
        return aioredis.Redis(
            host=host,
            port=port,
            password=password or None,
            decode_responses=True,
        )
    except Exception as e:
        logger.debug("llm_routing_pubsub: Redis not available: %s", e)
        return None


async def publish_invalidate(reason: str = "config-change") -> bool:
    """Broadcast a cache-invalidation message to all replicas. Returns
    True if Redis accepted the publish, False if Redis was unreachable
    (in which case the local replica's cache is still correct via the
    upsert/delete path's own reload_config_from_db call — only the
    OTHER replicas miss the invalidation, which is the bug we're fixing
    when Redis IS reachable)."""
    redis = await _get_redis()
    if not redis:
        logger.warning(
            "llm_routing_pubsub: Redis unreachable; cache invalidation "
            "did NOT propagate to other replicas. Reason=%s",
            reason,
        )
        return False
    try:
        payload = json.dumps({"reason": reason, "ts": _time.time()})
        await redis.publish(LLM_ROUTING_CHANNEL, payload)
        await redis.aclose()
        logger.info(
            "llm_routing_pubsub: published cache-invalidate (reason=%s)", reason
        )
        return True
    except Exception as e:
        logger.warning("llm_routing_pubsub: publish failed: %s", e)
        try:
            await redis.aclose()
        except Exception:
            pass
        return False


async def start_subscriber():
    """Background task: subscribe to LLM_ROUTING_CHANNEL and call
    `llm_registry.reload_config_from_db(pool)` on every message. Started
    from app/main.py's lifespan handler alongside the WebSocket
    subscriber.

    Failure mode: if Redis dies mid-run, the subscriber's `async for`
    loop raises; we log + return. The bug we're fixing only resurfaces
    until the next container restart, at which point this subscriber
    starts fresh. No silent failure mode."""
    redis = await _get_redis()
    if not redis:
        logger.info(
            "llm_routing_pubsub: Redis not available — cross-replica cache "
            "invalidation is OFF. Per-replica cache is still correct after "
            "Save/Reset, but other replicas will drift until the next "
            "container restart. Surface fix: get Redis Sentinel back online."
        )
        return

    try:
        pubsub = redis.pubsub()
        await pubsub.subscribe(LLM_ROUTING_CHANNEL)
        logger.info(
            "llm_routing_pubsub: subscriber started on channel=%s",
            LLM_ROUTING_CHANNEL,
        )

        # Lazy imports inside the loop to avoid circular dependency at
        # module import time (llm_registry imports nothing from here,
        # but main.py imports both).
        from database import get_pool
        from services import llm_registry

        async for raw_message in pubsub.listen():
            if raw_message.get("type") != "message":
                continue
            try:
                data = json.loads(raw_message.get("data") or "{}")
                reason = data.get("reason", "?")
            except Exception:
                reason = "?"
            try:
                pool = await get_pool()
                count = await llm_registry.reload_config_from_db(pool)
                logger.info(
                    "llm_routing_pubsub: cache reloaded (reason=%s, %d overrides)",
                    reason,
                    count,
                )
            except Exception as e:
                logger.warning(
                    "llm_routing_pubsub: reload_config_from_db failed: %s", e
                )
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.warning(
            "llm_routing_pubsub: subscriber died (cache invalidation OFF "
            "until next container restart): %s",
            e,
        )
