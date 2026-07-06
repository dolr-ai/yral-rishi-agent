"""Phase 0 Request Images track B — influencer_collages repository
(migration 046).

Three operations back the reservation-row race lock from design §1a:

  * reserve(bot_id, generation_date, theme) — INSERT ON CONFLICT DO
    NOTHING. Returns True iff THIS caller won the reservation. The
    composite PK (bot_id, generation_date) IS the lock; no Redis,
    no advisory locks, no application-level counter.

  * complete(bot_id, generation_date, image_urls, cost_usd) — flip
    state 'reserved' → 'succeeded' and populate the URLs. Guards on
    state='reserved' so a retried complete after fail can't overwrite
    a healthy row.

  * get(bot_id, generation_date) — read the row; None if the bot has
    no collage for that date.

state values: 'reserved' | 'succeeded' | 'failed'.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)


async def reserve(pool, bot_id: str, generation_date: date, theme: str) -> bool:
    """Try to claim today's collage for this bot. Returns True on
    successful claim; False when another concurrent requester already
    holds the reservation. When False, the caller should poll `get`
    for the winner's result."""
    row = await pool.fetchrow(
        """
        INSERT INTO influencer_collages
            (bot_id, generation_date, theme, image_urls, state)
        VALUES ($1, $2, $3, ARRAY[]::TEXT[], 'reserved')
        ON CONFLICT (bot_id, generation_date) DO NOTHING
        RETURNING bot_id
        """,
        bot_id,
        generation_date,
        theme,
    )
    return row is not None


async def complete(
    pool,
    bot_id: str,
    generation_date: date,
    image_urls: list[str],
    cost_usd: float,
) -> None:
    """Flip a reserved row to succeeded + record cost. Idempotent on
    the state guard: a duplicate complete against an already-succeeded
    row is a no-op."""
    await pool.execute(
        """
        UPDATE influencer_collages
        SET state = 'succeeded',
            image_urls = $3,
            cost_usd = $4,
            generated_at = NOW(),
            updated_at = NOW()
        WHERE bot_id = $1
          AND generation_date = $2
          AND state = 'reserved'
        """,
        bot_id,
        generation_date,
        image_urls,
        cost_usd,
    )


async def mark_failed(pool, bot_id: str, generation_date: date) -> None:
    """Flip a reserved row to failed. Follow-up watchdog / retry-elect
    machinery can DELETE + retry — kept out of this PR per brief."""
    await pool.execute(
        """
        UPDATE influencer_collages
        SET state = 'failed', updated_at = NOW()
        WHERE bot_id = $1
          AND generation_date = $2
          AND state = 'reserved'
        """,
        bot_id,
        generation_date,
    )


async def get(pool, bot_id: str, generation_date: date) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT bot_id, generation_date, theme, image_urls, state,
               cost_usd, generated_at, created_at, updated_at
        FROM influencer_collages
        WHERE bot_id = $1 AND generation_date = $2
        """,
        bot_id,
        generation_date,
    )
    return dict(row) if row else None
