"""Phase 7.5 — Soul File Coach DB helpers.

Three tables:
- coach_conversations: one per (creator, bot, session)
- coach_messages: turn-by-turn history within a session
- system_instructions_history: audit trail when a creator applies a change
"""

import logging

logger = logging.getLogger(__name__)


def _row(row) -> dict | None:
    return dict(row) if row else None


# ─── coach_conversations ──────────────────────────────────────────────────


async def create_session(pool, creator_user_id: str, bot_id: str) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO coach_conversations (creator_user_id, bot_id)
        VALUES ($1, $2)
        RETURNING id, creator_user_id, bot_id, created_at, updated_at
        """,
        creator_user_id,
        bot_id,
    )
    return dict(row)


async def get_session(pool, coach_conversation_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, creator_user_id, bot_id, created_at, updated_at
        FROM coach_conversations WHERE id = $1::uuid
        """,
        coach_conversation_id,
    )
    return _row(row)


async def touch_session(pool, coach_conversation_id: str):
    await pool.execute(
        "UPDATE coach_conversations SET updated_at = NOW() WHERE id = $1::uuid",
        coach_conversation_id,
    )


# ─── coach_messages ───────────────────────────────────────────────────────


async def add_message(
    pool,
    coach_conversation_id: str,
    role: str,
    content: str,
    proposed_changes: str | None = None,
    reasoning: str | None = None,
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO coach_messages (
            coach_conversation_id, role, content, proposed_changes, reasoning
        )
        VALUES ($1::uuid, $2, $3, $4, $5)
        RETURNING id, coach_conversation_id, role, content,
                  proposed_changes, reasoning, created_at
        """,
        coach_conversation_id,
        role,
        content,
        proposed_changes,
        reasoning,
    )
    return dict(row)


async def list_messages(pool, coach_conversation_id: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, coach_conversation_id, role, content,
               proposed_changes, reasoning, created_at
        FROM coach_messages
        WHERE coach_conversation_id = $1::uuid
        ORDER BY created_at ASC
        """,
        coach_conversation_id,
    )
    return [dict(r) for r in rows]


async def latest_proposal(pool, coach_conversation_id: str) -> dict | None:
    """Most recent coach message that includes proposed_changes — used by
    the /apply endpoint to find what to commit."""
    row = await pool.fetchrow(
        """
        SELECT id, coach_conversation_id, role, content,
               proposed_changes, reasoning, created_at
        FROM coach_messages
        WHERE coach_conversation_id = $1::uuid
          AND role = 'coach'
          AND proposed_changes IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        coach_conversation_id,
    )
    return _row(row)


# ─── system_instructions_history ──────────────────────────────────────────


async def record_application(
    pool,
    bot_id: str,
    coach_conversation_id: str | None,
    coach_message_id: str | None,
    previous_instructions: str,
    new_instructions: str,
    applied_by: str,
) -> dict:
    """Audit-log an applied system_instructions change.

    coach_conversation_id / coach_message_id are nullable so changes from
    other sources (e.g. Phase 7.6 A/B variant promotion) can use the same
    audit trail without faking foreign-key targets.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO system_instructions_history (
            bot_id, coach_conversation_id, coach_message_id,
            previous_instructions, new_instructions, applied_by
        )
        VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6)
        RETURNING id, bot_id, coach_conversation_id, coach_message_id,
                  previous_instructions, new_instructions,
                  applied_by, applied_at
        """,
        bot_id,
        coach_conversation_id,
        coach_message_id,
        previous_instructions,
        new_instructions,
        applied_by,
    )
    return dict(row)


async def history_for_bot(pool, bot_id: str, limit: int = 20) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, bot_id, coach_conversation_id, coach_message_id,
               previous_instructions, new_instructions,
               applied_by, applied_at
        FROM system_instructions_history
        WHERE bot_id = $1
        ORDER BY applied_at DESC
        LIMIT $2
        """,
        bot_id,
        limit,
    )
    return [dict(r) for r in rows]
