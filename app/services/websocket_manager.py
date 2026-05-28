import asyncio
import json
import logging

from fastapi import WebSocket

import config

logger = logging.getLogger(__name__)

_connections: dict[str, list[WebSocket]] = {}
_lock = asyncio.Lock()
_redis_pubsub_task: asyncio.Task | None = None

REDIS_CHANNEL = "ws_events"


async def connect(user_id: str, websocket: WebSocket):
    async with _lock:
        if user_id not in _connections:
            _connections[user_id] = []
        _connections[user_id].append(websocket)


async def disconnect(user_id: str, websocket: WebSocket):
    async with _lock:
        if user_id in _connections:
            _connections[user_id] = [
                ws for ws in _connections[user_id] if ws is not websocket
            ]
            if not _connections[user_id]:
                del _connections[user_id]


async def _send_to_user_local(user_id: str, message: str):
    if user_id not in _connections:
        return

    dead_connections = []
    for ws in _connections.get(user_id, []):
        try:
            await ws.send_text(message)
        except Exception:
            dead_connections.append(ws)

    if dead_connections:
        async with _lock:
            if user_id in _connections:
                _connections[user_id] = [
                    ws for ws in _connections[user_id] if ws not in dead_connections
                ]
                if not _connections[user_id]:
                    del _connections[user_id]


async def _get_redis():
    """Get async Redis client. Returns None if Redis is not configured."""
    try:
        import redis.asyncio as aioredis

        redis_url = config._env("REDIS_URL")
        if redis_url:
            return aioredis.from_url(redis_url)

        redis_host = config._env("REDIS_HOST", "redis-primary")
        redis_port = config._env_int("REDIS_PORT", 6379)
        redis_password = config._env("REDIS_PASSWORD")
        return aioredis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password or None,
            decode_responses=True,
        )
    except Exception as e:
        logger.debug(f"Redis not available for pub/sub: {e}")
        return None


async def _publish(user_id: str, message: str):
    """Publish event to Redis for cross-node delivery. Falls back to local-only."""
    try:
        redis = await _get_redis()
        if redis:
            payload = json.dumps({"user_id": user_id, "message": message})
            await redis.publish(REDIS_CHANNEL, payload)
            await redis.aclose()
            return
    except Exception as e:
        logger.debug(f"Redis publish failed, using local-only: {e}")

    await _send_to_user_local(user_id, message)


async def start_redis_subscriber():
    """Background task: subscribe to Redis channel and deliver events to local WebSockets."""
    try:
        redis = await _get_redis()
        if not redis:
            logger.info("Redis not available — WebSocket events are local-only")
            return

        pubsub = redis.pubsub()
        await pubsub.subscribe(REDIS_CHANNEL)
        logger.info("Redis pub/sub subscriber started for cross-node WebSocket events")

        async for raw_message in pubsub.listen():
            if raw_message["type"] != "message":
                continue
            try:
                data = json.loads(raw_message["data"])
                user_id = data["user_id"]
                message = data["message"]
                await _send_to_user_local(user_id, message)
            except Exception as e:
                logger.debug(f"Redis subscriber message handling error: {e}")
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.warning(f"Redis subscriber died (WS events will be local-only): {e}")


async def broadcast_new_message(
    user_id: str,
    conversation_id: str,
    message: dict,
    influencer: dict,
    unread_count: int,
):
    event = json.dumps(
        {
            "event": "new_message",
            "data": {
                "conversation_id": conversation_id,
                "message": message,
                "influencer": influencer,
                "unread_count": unread_count,
            },
        }
    )
    await _publish(user_id, event)


async def broadcast_conversation_read(user_id: str, conversation_id: str, read_at: str):
    event = json.dumps(
        {
            "event": "conversation_read",
            "data": {
                "conversation_id": conversation_id,
                "unread_count": 0,
                "read_at": read_at,
            },
        }
    )
    await _publish(user_id, event)


async def broadcast_typing_status(
    user_id: str,
    conversation_id: str,
    influencer_id: str,
    is_typing: bool,
):
    event = json.dumps(
        {
            "event": "typing_status",
            "data": {
                "conversation_id": conversation_id,
                "influencer_id": influencer_id,
                "is_typing": is_typing,
            },
        }
    )
    await _publish(user_id, event)


async def broadcast_event(user_id: str, event_name: str, data: dict):
    """Generic broadcast — reuses the same Redis pub/sub channel."""
    event = json.dumps({"event": event_name, "data": data})
    await _publish(user_id, event)
