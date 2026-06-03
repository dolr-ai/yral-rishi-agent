"""Phase 23.4 — user_skill_state repository.

Raw asyncpg, SQL strings — same shape as memory_repo + conversation_repo.
Three operations: get(user, influencer), upsert(...), list_due() for
the proactive engagement loop.
"""

import json
import logging

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    """asyncpg Record → plain dict. state JSONB comes back as a str on
    some asyncpg versions / when there's no JSON codec registered, so
    parse defensively."""
    d = dict(row)
    state = d.get("state")
    if isinstance(state, str):
        try:
            d["state"] = json.loads(state)
        except (json.JSONDecodeError, TypeError):
            d["state"] = {}
    elif state is None:
        d["state"] = {}
    return d


async def get(pool, user_id: str, influencer_id: str) -> dict | None:
    """Return the row for this (user, influencer) pair, or None if no
    onboarding has happened yet. Caller (chat.py onboarding hook) uses
    None as the signal to fire the skill's onboarding_prompt."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, influencer_id, skill_slug, state,
                   next_event_at, last_event_at, status,
                   created_at, updated_at
            FROM user_skill_state
            WHERE user_id = $1 AND influencer_id = $2
            """,
            user_id,
            influencer_id,
        )
    return _row_to_dict(row) if row else None


async def upsert(
    pool,
    *,
    user_id: str,
    influencer_id: str,
    skill_slug: str,
    state: dict,
    next_event_at=None,
    status: str = "active",
) -> dict:
    """Insert-or-replace the row for this (user, influencer) pair.
    Called from:
      - First-turn onboarding (chat.py) — initial setup write
      - Proactive loop (services/proactive.py) — runtime.last_event_at update
      - PATCH /api/v1/skills/{influencer_id}/preferences — user-edited setup

    state is merged-on-conflict via JSONB || so the runtime half isn't
    blown away when onboarding writes a fresh setup half. The full
    merge happens at the SQL level via state || EXCLUDED.state."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO user_skill_state
                (user_id, influencer_id, skill_slug, state, next_event_at, status, updated_at)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, NOW())
            ON CONFLICT (user_id, influencer_id) DO UPDATE
                SET skill_slug    = EXCLUDED.skill_slug,
                    state         = user_skill_state.state || EXCLUDED.state,
                    next_event_at = COALESCE(EXCLUDED.next_event_at, user_skill_state.next_event_at),
                    status        = EXCLUDED.status,
                    updated_at    = NOW()
            RETURNING id, user_id, influencer_id, skill_slug, state,
                      next_event_at, last_event_at, status,
                      created_at, updated_at
            """,
            user_id,
            influencer_id,
            skill_slug,
            json.dumps(state or {}),
            next_event_at,
            status,
        )
    return _row_to_dict(row)


async def mark_event_fired(
    pool,
    *,
    user_id: str,
    influencer_id: str,
    next_event_at,
) -> None:
    """Called by the proactive loop after a check-in (or briefing, etc.)
    is delivered. Updates last_event_at to NOW() and advances
    next_event_at. Separate from upsert() because the proactive path
    doesn't need to re-write state JSONB on every tick."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_skill_state
            SET last_event_at = NOW(),
                next_event_at = $3,
                updated_at = NOW()
            WHERE user_id = $1 AND influencer_id = $2
            """,
            user_id,
            influencer_id,
            next_event_at,
        )


async def list_due(pool, *, limit: int = 50) -> list[dict]:
    """Active rows whose next_event_at has passed. Hot query for the
    proactive engagement loop. Index idx_user_skill_state_due makes
    this O(due_rows) not O(all_rows)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_id, influencer_id, skill_slug, state,
                   next_event_at, last_event_at, status,
                   created_at, updated_at
            FROM user_skill_state
            WHERE status = 'active'
              AND next_event_at IS NOT NULL
              AND next_event_at <= NOW()
            ORDER BY next_event_at ASC
            LIMIT $1
            """,
            limit,
        )
    return [_row_to_dict(r) for r in rows]


async def pause(pool, *, user_id: str, influencer_id: str) -> None:
    """Set status to 'paused' — keeps the row + state but stops the
    proactive loop from firing. PATCH /preferences uses this."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_skill_state
            SET status = 'paused', updated_at = NOW()
            WHERE user_id = $1 AND influencer_id = $2
            """,
            user_id,
            influencer_id,
        )


async def resume(pool, *, user_id: str, influencer_id: str) -> None:
    """Set status back to 'active'. Caller should also set next_event_at
    to a fresh value via upsert (otherwise the row sits idle)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE user_skill_state
            SET status = 'active', updated_at = NOW()
            WHERE user_id = $1 AND influencer_id = $2
            """,
            user_id,
            influencer_id,
        )
