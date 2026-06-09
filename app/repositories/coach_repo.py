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


async def latest_session_for_bot(
    pool, creator_user_id: str, bot_id: str
) -> dict | None:
    """Coach UX overhaul (2026-06-04) — the most recent coach session for
    this (creator, bot) pair. POST /conversations/{bot_id} uses this to
    decide between resume (return the existing session id with
    resumed=true) and fresh (body {"fresh": true} → ignore, create new).
    Owning-creator check is implicit via creator_user_id."""
    row = await pool.fetchrow(
        """
        SELECT id, creator_user_id, bot_id, created_at, updated_at
        FROM coach_conversations
        WHERE creator_user_id = $1 AND bot_id = $2
        ORDER BY created_at DESC
        LIMIT 1
        """,
        creator_user_id,
        bot_id,
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
    suggestions: list[str] | None = None,
    proposed_global_rule_override: dict | None = None,
) -> dict:
    """Insert a coach_messages row.

    `suggestions` (Coach UX overhaul, migration 031) is a JSONB list of
    3 short tappable strings rendered as chips below the opening coach
    message. NULL for creator turns and non-opening coach turns.

    `proposed_global_rule_override` (Coach Fix 1 PR-B, migration 034)
    is a JSONB blob of shape {"key": "<slug>", "value": "<label>"}.
    EXACTLY ONE of proposed_changes and proposed_global_rule_override
    should be set on any given coach turn — the apply endpoint
    dispatches on which is present. NULL for non-proposal turns and
    for system-instructions-edit proposals.
    """
    import json as _json

    row = await pool.fetchrow(
        """
        INSERT INTO coach_messages (
            coach_conversation_id, role, content,
            proposed_changes, reasoning, suggestions,
            proposed_global_rule_override
        )
        VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
        RETURNING id, coach_conversation_id, role, content,
                  proposed_changes, reasoning, suggestions,
                  proposed_global_rule_override, created_at
        """,
        coach_conversation_id,
        role,
        content,
        proposed_changes,
        reasoning,
        _json.dumps(suggestions) if suggestions else None,
        _json.dumps(proposed_global_rule_override)
        if proposed_global_rule_override
        else None,
    )
    return dict(row)


async def list_messages(pool, coach_conversation_id: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, coach_conversation_id, role, content,
               proposed_changes, reasoning, suggestions,
               proposed_global_rule_override, created_at
        FROM coach_messages
        WHERE coach_conversation_id = $1::uuid
        ORDER BY created_at ASC
        """,
        coach_conversation_id,
    )
    return [dict(r) for r in rows]


async def latest_proposal(pool, coach_conversation_id: str) -> dict | None:
    """Most recent coach message that includes EITHER proposed_changes
    OR proposed_global_rule_override (Coach Fix 1 PR-B). Used by the
    /apply endpoint to find what to commit; that endpoint dispatches on
    which column is non-NULL."""
    row = await pool.fetchrow(
        """
        SELECT id, coach_conversation_id, role, content,
               proposed_changes, reasoning, suggestions,
               proposed_global_rule_override, created_at
        FROM coach_messages
        WHERE coach_conversation_id = $1::uuid
          AND role = 'coach'
          AND (proposed_changes IS NOT NULL
               OR proposed_global_rule_override IS NOT NULL)
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
