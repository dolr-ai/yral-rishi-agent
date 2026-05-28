"""Database helpers for human-creator takeover state."""

import logging

logger = logging.getLogger(__name__)


async def activate(pool, conversation_id: str, human_creator_user_id: str) -> dict:
    """Set takeover active. Fresh timestamps on every activation (Bug 3 fix).

    Returns the updated takeover state.
    """
    row = await pool.fetchrow(
        """
        UPDATE conversations
        SET human_creator_takeover_active = TRUE,
            human_creator_user_id = $2,
            human_creator_takeover_started_at = NOW(),
            user_last_message_at = NOW(),
            human_creator_last_message_at = NOW()
        WHERE id = $1
        RETURNING human_creator_takeover_started_at,
                  user_last_message_at,
                  human_creator_last_message_at
        """,
        conversation_id,
        human_creator_user_id,
    )
    return dict(row) if row else {}


async def deactivate_if_active(pool, conversation_id: str) -> bool:
    """Atomically deactivate takeover. Returns True only if the row WAS active.

    Idempotency guard (Bug 3 fix): manual release and sweep can race, but only
    the caller that actually flipped the flag should post the 'left' system message.
    """
    row = await pool.fetchrow(
        """
        UPDATE conversations
        SET human_creator_takeover_active = FALSE
        WHERE id = $1 AND human_creator_takeover_active = TRUE
        RETURNING id
        """,
        conversation_id,
    )
    return row is not None


async def update_creator_last_message(pool, conversation_id: str):
    """Reset the 2-min timer by stamping the creator's last activity (Bug 1 fix)."""
    await pool.execute(
        "UPDATE conversations SET human_creator_last_message_at = NOW() WHERE id = $1",
        conversation_id,
    )


async def update_user_last_message(pool, conversation_id: str):
    """Track user's last message during takeover (analytics-only, not the timer)."""
    await pool.execute(
        "UPDATE conversations SET user_last_message_at = NOW() WHERE id = $1",
        conversation_id,
    )


async def find_timed_out_takeovers(pool, timeout_minutes: int = 2) -> list[dict]:
    """Find conversations where the CREATOR has been silent past the timeout (Bug 1 fix).

    Uses idx_conversations_active_takeover partial index for cheap scans.
    """
    rows = await pool.fetch(
        """
        SELECT c.id, c.user_id, c.human_creator_user_id,
               i.display_name as bot_name
        FROM conversations c
        JOIN ai_influencers i ON i.id = c.influencer_id
        WHERE c.human_creator_takeover_active = TRUE
          AND c.human_creator_last_message_at < NOW() - INTERVAL '1 minute' * $1
        """,
        timeout_minutes,
    )
    return [dict(r) for r in rows]
