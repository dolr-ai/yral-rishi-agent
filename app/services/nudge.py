"""First-turn nudge: re-engage when user goes idle mid-chat.

If a user starts a conversation but doesn't reply after the initial greeting
or first exchange, the bot sends a follow-up nudge to draw them back in.

Nudge triggers:
- Conversation has exactly 1 message (the greeting) and user hasn't replied in 5 min
- Conversation has 2-3 messages and user hasn't replied in 10 min
"""

import logging

from repositories import message_repo, influencer_repo
from services import llm_registry
import config

logger = logging.getLogger(__name__)

NUDGE_PROMPT = """You are {display_name}. A user started chatting with you but went quiet.

Last messages:
{last_messages}

Generate a short, playful follow-up to re-engage them. Rules:
- Stay in character
- Be light and casual, not pushy
- Reference the conversation if possible
- Under 2 sentences
- Don't ask "are you still there?" — that's boring

Generate ONLY the nudge text."""


async def should_nudge(pool, conversation_id: str, idle_minutes: int = 5) -> bool:
    """Check if a conversation qualifies for a nudge."""
    msg_count = await message_repo.count_by_conversation(pool, conversation_id)
    if msg_count < 1 or msg_count > 4:
        return False

    last_msg = await pool.fetchrow(
        """
        SELECT role, created_at FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at DESC LIMIT 1
        """,
        conversation_id,
    )
    if not last_msg or last_msg["role"] != "assistant":
        return False

    # Cap: skip if we've already sent the max unanswered nudges (default 1).
    # If the user didn't respond to our previous nudge, sending another
    # is spam. The cap resets when the user sends a message — at that
    # point count_unanswered_nudge starts at 0 again.
    unanswered = await message_repo.count_unanswered_nudge(pool, conversation_id)
    if unanswered >= message_repo.NUDGE_CAP_WITHOUT_REPLY:
        return False

    from datetime import datetime, timezone, timedelta

    threshold = idle_minutes if msg_count <= 2 else idle_minutes * 2
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold)
    return last_msg["created_at"].replace(tzinfo=timezone.utc) < cutoff


async def generate_nudge(pool, conversation_id: str, influencer_id: str) -> str | None:
    """Generate a nudge message for an idle conversation."""
    if not config.GEMINI_API_KEY:
        return None

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        return None

    recent = await message_repo.get_recent_for_context(pool, conversation_id, 5)
    last_messages = "\n".join(
        f"{m['role']}: {m.get('content', '')[:100]}" for m in recent if m.get("content")
    )

    prompt = NUDGE_PROMPT.format(
        display_name=inf.get("display_name", "Bot"),
        last_messages=last_messages or "(initial greeting only)",
    )

    try:
        response = await llm_registry.call(
            process="nudge_generation",
            messages=[
                {"role": "system", "content": inf.get("system_instructions", "")},
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
            max_tokens=256,
        )
        return response.content.strip() if response.content else None
    except Exception as e:
        logger.warning(f"Nudge generation failed: {e}")
        return None
