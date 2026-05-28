"""Pure helpers for the Chat-as-Human takeover feature (no FastAPI deps).

Kept separate so unit tests can import without spinning up the web stack.
"""

from datetime import datetime, timezone

TAKEOVER_TIMEOUT_SECONDS = 120


def remaining_seconds(user_last_message_at) -> int:
    """Seconds remaining before the takeover auto-releases."""
    if not user_last_message_at:
        return TAKEOVER_TIMEOUT_SECONDS
    if not isinstance(user_last_message_at, datetime):
        return TAKEOVER_TIMEOUT_SECONDS
    last = user_last_message_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    return max(0, int(TAKEOVER_TIMEOUT_SECONDS - elapsed))


def format_msg_for_response(msg: dict) -> dict:
    created_at = msg["created_at"]
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    return {
        "id": msg["id"],
        "conversation_id": msg.get("conversation_id"),
        "role": msg["role"],
        "content": msg.get("content"),
        "message_type": msg["message_type"],
        "media_urls": None,
        "audio_url": None,
        "audio_duration_seconds": None,
        "token_count": None,
        "created_at": created_at,
    }
