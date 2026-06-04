"""Phase 22.3 — video_ideas DB helpers.

Mirrors `quality_score_repo.py`: insert (batch-aware), latest-for-bot,
mark-used. The nightly cron writes one batch per active bot per day
(`generate_all_once` in services/video_ideas.py). The creator-facing
endpoint reads the latest batch and the mobile-tap flips status='used'.
"""

import logging

logger = logging.getLogger(__name__)


def _row(row) -> dict | None:
    return dict(row) if row else None


async def insert_batch(
    pool,
    *,
    influencer_id: str,
    batch_date,  # datetime.date — Postgres binds to DATE
    ideas: list[dict],
) -> list[dict]:
    """Write one batch of ideas for a bot. Each idea dict must have
    `hook` + `idea_text`; `rank` is assigned by position in the list
    (1-indexed) to satisfy the UNIQUE (influencer_id, batch_date, rank)
    constraint.

    Returns the inserted rows. ON CONFLICT DO NOTHING so the nightly
    cron can safely re-run (idempotent — skips any (bot, date, rank)
    that already exists)."""
    inserted: list[dict] = []
    async with pool.acquire() as conn:
        for i, idea in enumerate(ideas, start=1):
            hook = (idea.get("hook") or "").strip()
            text = (idea.get("idea_text") or "").strip()
            if not hook or not text:
                continue
            row = await conn.fetchrow(
                """
                INSERT INTO video_ideas (
                    influencer_id, batch_date, rank, hook, idea_text
                )
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (influencer_id, batch_date, rank) DO NOTHING
                RETURNING id, influencer_id, batch_date, rank,
                          hook, idea_text, status, used_at, created_at
                """,
                influencer_id,
                batch_date,
                i,
                hook,
                text,
            )
            if row:
                inserted.append(dict(row))
    return inserted


async def latest_batch_for_bot(pool, influencer_id: str) -> list[dict]:
    """Most recent batch of ideas for a bot. Uses the
    idx_video_ideas_influencer_recent index (created_at DESC). Returns
    rows in rank order ASC for stable mobile rendering."""
    rows = await pool.fetch(
        """
        WITH last_batch AS (
            SELECT batch_date
            FROM video_ideas
            WHERE influencer_id = $1
            ORDER BY created_at DESC
            LIMIT 1
        )
        SELECT id, influencer_id, batch_date, rank, hook, idea_text,
               status, used_at, created_at
        FROM video_ideas
        WHERE influencer_id = $1
          AND batch_date = (SELECT batch_date FROM last_batch)
        ORDER BY rank ASC
        """,
        influencer_id,
    )
    return [dict(r) for r in rows]


async def bot_has_batch_for_date(pool, influencer_id: str, batch_date) -> bool:
    """Used by the nightly loop to skip bots that already have today's
    batch — supports safe re-running of `generate_all_once`."""
    val = await pool.fetchval(
        """
        SELECT 1 FROM video_ideas
        WHERE influencer_id = $1 AND batch_date = $2
        LIMIT 1
        """,
        influencer_id,
        batch_date,
    )
    return val is not None


async def mark_used(pool, idea_id: str) -> dict | None:
    """Flip status from 'fresh' to 'used' + set used_at = NOW().
    Idempotent: re-flipping a 'used' row leaves it 'used' with the
    original used_at preserved (only updates if currently fresh)."""
    row = await pool.fetchrow(
        """
        UPDATE video_ideas
        SET status = 'used', used_at = NOW()
        WHERE id = $1::uuid
          AND status = 'fresh'
        RETURNING id, influencer_id, batch_date, rank, hook, idea_text,
                  status, used_at, created_at
        """,
        idea_id,
    )
    if row:
        return dict(row)
    # If the row exists but was already used, return its current state
    # so callers can distinguish "not found" (None) from
    # "already used" (dict with status='used').
    existing = await pool.fetchrow(
        """
        SELECT id, influencer_id, batch_date, rank, hook, idea_text,
               status, used_at, created_at
        FROM video_ideas
        WHERE id = $1::uuid
        """,
        idea_id,
    )
    return _row(existing)
