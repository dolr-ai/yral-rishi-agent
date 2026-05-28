"""Phase 4.7: ephemeral per-session state in Redis.

Distinct from long-term memory in Postgres:
- Postgres `user_memories` stores durable facts (name, hobbies, ...)
- Redis session_memory stores derived signals that matter for ~1 hour:
  most recent mood, current topic. Falls off naturally after TTL.

If Redis is unavailable, every function in this module degrades to no-op
silently. The agent never blocks on session memory.

Mood is detected via a lightweight heuristic (emoji + keyword), not a
separate LLM call — Phase 4.4's embedding already adds ~150ms to the hot
path, we don't want to compound that.
"""

import json
import logging
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

SESSION_TTL_SEC = 3600  # 1 hour
SESSION_KEY_PREFIX = "session:"


_redis_client = None


async def _get_redis():
    """Lazily-initialized async Redis client. Mirrors websocket_manager._get_redis
    so we share the same connection pool / credentials code path."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        redis_url = config._env("REDIS_URL")
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
        logger.warning(f"session_memory: Redis init failed (degrading to no-op): {e}")
        return None


_MOOD_KEYWORDS = {
    "happy": [
        "😊",
        "🙂",
        "😀",
        "😄",
        "❤️",
        "🥰",
        "great",
        "awesome",
        "love",
        "amazing",
        "wonderful",
        "yay",
    ],
    "sad": [
        "😢",
        "😭",
        "😞",
        "💔",
        "sad",
        "depressed",
        "lonely",
        "feel bad",
        "miss you",
        "crying",
    ],
    "excited": [
        "🎉",
        "🔥",
        "🚀",
        "⚡",
        "excited",
        "can't wait",
        "pumped",
        "stoked",
        "thrilled",
    ],
    "stressed": [
        "😩",
        "😫",
        "😰",
        "tired",
        "exhausted",
        "stressed",
        "anxious",
        "overwhelmed",
        "burnt out",
    ],
}


def detect_mood(text: str) -> str:
    """Cheap rule-based mood detection. Returns 'neutral' if no match.

    Order matters slightly: 'tired' beats 'love' because emotional-state
    words are more diagnostic than positive affirmations. We just check
    each bucket in dict-insertion order and take the first hit.
    """
    if not text:
        return "neutral"
    lower = text.lower()
    for mood, markers in _MOOD_KEYWORDS.items():
        if any(m.lower() in lower for m in markers):
            return mood
    return "neutral"


def _key(user_id: str, conversation_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{user_id}:{conversation_id}"


async def update_from_user_message(
    user_id: str, conversation_id: str, text: str
) -> None:
    """Update Redis session state from the user's latest message. Non-fatal."""
    redis = await _get_redis()
    if not redis:
        return
    mood = detect_mood(text)
    payload = json.dumps(
        {
            "mood": mood,
            "last_user_text": (text or "")[:200],
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        await redis.set(_key(user_id, conversation_id), payload, ex=SESSION_TTL_SEC)
    except Exception as e:
        logger.debug(f"session_memory.update failed (non-fatal): {e}")


async def read(user_id: str, conversation_id: str) -> dict | None:
    """Read current session state. Returns None on miss / Redis down."""
    redis = await _get_redis()
    if not redis:
        return None
    try:
        raw = await redis.get(_key(user_id, conversation_id))
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug(f"session_memory.read failed (non-fatal): {e}")
        return None
