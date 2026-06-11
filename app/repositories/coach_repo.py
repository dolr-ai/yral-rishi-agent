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
    proposed_section_change: dict | None = None,
) -> dict:
    """Insert a coach_messages row.

    `suggestions` (Coach UX overhaul, migration 031) is a JSONB list of
    3 short tappable strings rendered as chips below the opening coach
    message. NULL for creator turns and non-opening coach turns.

    `proposed_global_rule_override` (Coach Fix 1 PR-B, migration 034)
    is a JSONB blob of shape {"key": "<slug>", "value": "<label>"}.

    `proposed_section_change` (Coach Bucket 2 PR-2, migration 039) is a
    JSONB blob of shape {section_id, section_heading, section_editable,
    new_body, previous_body_sha256}. `target_section_id` is denormalised
    server-side from this blob so a future "all proposals against
    section X" filter can hit a typed indexable column.

    EXACTLY ONE of proposed_changes / proposed_global_rule_override /
    proposed_section_change should be set on any given coach turn — the
    apply endpoint dispatches on which is present. NULL on every other
    role/turn combination.
    """
    import json as _json

    # Coach PR-3 lifecycle: proposal rows (role=coach + any proposed_*
    # set) start as 'pending'. Everything else — creator turns, opening
    # greetings, receipts — is 'na' (not applicable to the lifecycle).
    # Same rule as the migration 035 backfill.
    is_proposal = role == "coach" and (
        proposed_changes is not None
        or proposed_global_rule_override is not None
        or proposed_section_change is not None
    )
    status = "pending" if is_proposal else "na"

    target_section_id: str | None = None
    if isinstance(proposed_section_change, dict):
        candidate = proposed_section_change.get("section_id")
        if isinstance(candidate, str) and candidate.strip():
            target_section_id = candidate.strip()

    # PR-2 follow-up (Rishi 2026-06-11): supersede-on-insert. The
    # moment a NEW proposal lands in a session, every OLDER pending
    # proposal in the same session must flip to 'superseded' so mobile
    # can disable the old card's Apply button immediately — before the
    # creator might tap an older Apply button and silently override the
    # newer suggestion. Pre-fix the supersede only ran inside /apply,
    # leaving a window where TWO pending proposals could co-exist.
    #
    # Wrap the INSERT + supersede in a single transaction so a partial
    # failure can't leave the session in an inconsistent state. Same
    # rule as supersede_and_apply().
    async with pool.acquire() as conn:
        async with conn.transaction():
            if is_proposal:
                await conn.execute(
                    """
                    UPDATE coach_messages
                    SET status = 'superseded',
                        status_changed_at = NOW()
                    WHERE coach_conversation_id = $1::uuid
                      AND status = 'pending'
                    """,
                    coach_conversation_id,
                )
            row = await conn.fetchrow(
                """
                INSERT INTO coach_messages (
                    coach_conversation_id, role, content,
                    proposed_changes, reasoning, suggestions,
                    proposed_global_rule_override,
                    proposed_section_change, target_section_id, status
                )
                VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7::jsonb,
                        $8::jsonb, $9, $10)
                RETURNING id, coach_conversation_id, role, content,
                          proposed_changes, reasoning, suggestions,
                          proposed_global_rule_override,
                          proposed_section_change, target_section_id, status,
                          status_changed_at, created_at
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
                _json.dumps(proposed_section_change)
                if proposed_section_change
                else None,
                target_section_id,
                status,
            )
    return dict(row)


async def list_messages(pool, coach_conversation_id: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, coach_conversation_id, role, content,
               proposed_changes, reasoning, suggestions,
               proposed_global_rule_override,
               proposed_section_change, target_section_id, status,
               status_changed_at, created_at
        FROM coach_messages
        WHERE coach_conversation_id = $1::uuid
        ORDER BY created_at ASC
        """,
        coach_conversation_id,
    )
    return [dict(r) for r in rows]


async def latest_proposal(pool, coach_conversation_id: str) -> dict | None:
    """Most recent coach message that includes EITHER proposed_changes
    OR proposed_global_rule_override (Coach Fix 1 PR-B). Used by
    backward-compat callers; new code should use `get_proposal_by_id`
    (PR-3 trust fix — the wrong-proposal-applied bug fix)."""
    row = await pool.fetchrow(
        """
        SELECT id, coach_conversation_id, role, content,
               proposed_changes, reasoning, suggestions,
               proposed_global_rule_override,
               proposed_section_change, target_section_id, status,
               status_changed_at, created_at
        FROM coach_messages
        WHERE coach_conversation_id = $1::uuid
          AND role = 'coach'
          AND (proposed_changes IS NOT NULL
               OR proposed_global_rule_override IS NOT NULL
               OR proposed_section_change IS NOT NULL)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        coach_conversation_id,
    )
    return _row(row)


async def pending_proposal(pool, coach_conversation_id: str) -> dict | None:
    """Coach Fix 4 + PR-3 — return the latest PENDING proposal in this
    session or None. Used by:
      - send-message action-verb fast path (Fix 4): "save it" + a
        pending exists → return {type: action, ...} and skip the LLM.
      - send-message + list-messages responses (PR-4): the
        `pending_proposal_exists` field that gates mobile's Save button.

    Post-PR-3 this is a typed lookup on `status='pending'` — was
    previously a join into system_instructions_history. The typed
    column is faster (the new partial index `idx_coach_messages_
    pending_per_session` lets this query return in O(log pending))
    AND captures discarded/superseded states the audit-table join
    couldn't see."""
    row = await pool.fetchrow(
        """
        SELECT id, coach_conversation_id, role, content,
               proposed_changes, reasoning, suggestions,
               proposed_global_rule_override,
               proposed_section_change, target_section_id, status,
               status_changed_at, created_at
        FROM coach_messages
        WHERE coach_conversation_id = $1::uuid
          AND role = 'coach'
          AND status = 'pending'
          AND (proposed_changes IS NOT NULL
               OR proposed_global_rule_override IS NOT NULL
               OR proposed_section_change IS NOT NULL)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        coach_conversation_id,
    )
    return _row(row)


async def get_proposal_by_id(
    pool, coach_conversation_id: str, proposal_id: str
) -> dict | None:
    """Coach PR-3 — fetch a SPECIFIC proposal by id, scoped to the
    session. Returns None if the id doesn't exist OR belongs to a
    different session (defense-in-depth against ID forgery from
    another creator's session).

    Returns the row regardless of status — callers (the /apply and
    /discard endpoints) inspect the status themselves so they can
    surface a precise 409 with the current state."""
    row = await pool.fetchrow(
        """
        SELECT id, coach_conversation_id, role, content,
               proposed_changes, reasoning, suggestions,
               proposed_global_rule_override,
               proposed_section_change, target_section_id, status,
               status_changed_at, created_at
        FROM coach_messages
        WHERE id = $1::uuid
          AND coach_conversation_id = $2::uuid
          AND role = 'coach'
          AND (proposed_changes IS NOT NULL
               OR proposed_global_rule_override IS NOT NULL
               OR proposed_section_change IS NOT NULL)
        LIMIT 1
        """,
        proposal_id,
        coach_conversation_id,
    )
    return _row(row)


async def supersede_and_apply(
    pool, coach_conversation_id: str, proposal_id: str
) -> None:
    """Coach PR-3 — transactionally:
      1. Mark every OTHER pending proposal in this session as
         'superseded' (creator may have scrolled back to an older
         card; once they apply ANY proposal the other pending ones
         are no longer actionable).
      2. Mark the chosen proposal as 'applied'.

    Both UPDATEs run in a single transaction so the lifecycle stays
    consistent — after this returns, the session has exactly one
    `applied` row + zero `pending` rows.

    Caller is responsible for: status==pending check BEFORE calling
    this (cleaner 409 surface); the system_instructions UPDATE on the
    bot row (this helper only touches coach_messages.status)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE coach_messages
                SET status = 'superseded',
                    status_changed_at = NOW()
                WHERE coach_conversation_id = $1::uuid
                  AND status = 'pending'
                  AND id != $2::uuid
                """,
                coach_conversation_id,
                proposal_id,
            )
            await conn.execute(
                """
                UPDATE coach_messages
                SET status = 'applied',
                    status_changed_at = NOW()
                WHERE id = $1::uuid
                """,
                proposal_id,
            )


async def mark_discarded(pool, proposal_id: str) -> None:
    """Coach PR-3 — /discard endpoint marks the chosen proposal
    `discarded`. Does NOT touch other pending in the session — the
    creator may want to apply a different one. Idempotent: re-call
    on an already-discarded id is a no-op."""
    await pool.execute(
        """
        UPDATE coach_messages
        SET status = 'discarded',
            status_changed_at = NOW()
        WHERE id = $1::uuid
          AND status = 'pending'
        """,
        proposal_id,
    )


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
