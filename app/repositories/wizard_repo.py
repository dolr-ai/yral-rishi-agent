"""Phase 7.9 — wizard_sessions DB helpers."""

import json
import logging

logger = logging.getLogger(__name__)


async def create_session(pool, creator_user_id: str, concept: str) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO wizard_sessions (creator_user_id, concept, questions, answers)
        VALUES ($1, $2, '[]'::jsonb, '{}'::jsonb)
        RETURNING id, creator_user_id, concept, questions, answers,
                  draft_system_instructions, draft_display_name,
                  draft_category, draft_initial_greeting, committed_bot_id,
                  created_at
        """,
        creator_user_id,
        concept,
    )
    return _normalize(dict(row))


async def get_session(pool, session_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, creator_user_id, concept, questions, answers,
               draft_system_instructions, draft_display_name,
               draft_category, draft_initial_greeting, committed_bot_id,
               created_at
        FROM wizard_sessions WHERE id = $1::uuid
        """,
        session_id,
    )
    return _normalize(dict(row)) if row else None


async def set_questions(pool, session_id: str, questions: list[dict]):
    await pool.execute(
        """
        UPDATE wizard_sessions
        SET questions = $1::jsonb, updated_at = NOW()
        WHERE id = $2::uuid
        """,
        json.dumps(questions),
        session_id,
    )


async def record_answer(pool, session_id: str, key: str, value: str) -> dict:
    """Merge {key: value} into the answers JSONB. Returns the updated row."""
    row = await pool.fetchrow(
        """
        UPDATE wizard_sessions
        SET answers = answers || jsonb_build_object($1::text, $2::text),
            updated_at = NOW()
        WHERE id = $3::uuid
        RETURNING id, creator_user_id, concept, questions, answers,
                  draft_system_instructions, draft_display_name,
                  draft_category, draft_initial_greeting, committed_bot_id,
                  created_at
        """,
        key,
        value,
        session_id,
    )
    return _normalize(dict(row)) if row else None


async def save_draft(
    pool,
    session_id: str,
    system_instructions: str,
    display_name: str,
    category: str,
    initial_greeting: str,
):
    await pool.execute(
        """
        UPDATE wizard_sessions
        SET draft_system_instructions = $1,
            draft_display_name = $2,
            draft_category = $3,
            draft_initial_greeting = $4,
            updated_at = NOW()
        WHERE id = $5::uuid
        """,
        system_instructions,
        display_name,
        category,
        initial_greeting,
        session_id,
    )


async def mark_committed(pool, session_id: str, bot_id: str):
    await pool.execute(
        """
        UPDATE wizard_sessions
        SET committed_bot_id = $1, updated_at = NOW()
        WHERE id = $2::uuid
        """,
        bot_id,
        session_id,
    )


def _normalize(row: dict) -> dict:
    """asyncpg returns JSONB as Python objects (lists/dicts) for the
    `jsonb` type when set up with default codecs; ensure that's the case."""
    if isinstance(row.get("questions"), str):
        try:
            row["questions"] = json.loads(row["questions"])
        except (json.JSONDecodeError, TypeError):
            row["questions"] = []
    if isinstance(row.get("answers"), str):
        try:
            row["answers"] = json.loads(row["answers"])
        except (json.JSONDecodeError, TypeError):
            row["answers"] = {}
    return row
