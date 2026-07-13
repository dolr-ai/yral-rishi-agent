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
    image_urls_blurred: list[str] | None = None,
) -> None:
    """Flip a reserved row to succeeded + record cost. Idempotent on
    the state guard: a duplicate complete against an already-succeeded
    row is a no-op.

    `image_urls_blurred` is the parallel array of pre-blurred variants
    (migration 047). Optional for backwards compat with any older
    callers that don't produce blurred variants yet — passing None
    stores an empty array, and the route falls back to `image_urls`
    for non-subscribers in that case (design §5 rollout window)."""
    await pool.execute(
        """
        UPDATE influencer_collages
        SET state = 'succeeded',
            image_urls = $3,
            image_urls_blurred = $5,
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
        image_urls_blurred or [],
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


async def get_latest_succeeded(pool, bot_id: str, within_days: int = 7) -> dict | None:
    """Return the most-recent succeeded collage row for this bot within
    the last `within_days` calendar days (UTC), or None.

    Powers the endpoint's fallback-to-yesterday behavior (2026-07-13
    Sarvesh incident): when today's row landed in state='failed'
    because the provider's safety filter refused, the route serves the
    most-recent succeeded row instead of bubbling 502 to the user.
    Bounded by `within_days` so a truly stale bot (weeks-old success)
    doesn't silently look healthy — we bubble the real "failed" up in
    that case so the incident stays visible.

    A `within_days` of 0 disables the fallback (returns None even if a
    row exists). Callers use that as a paranoid switch — set the env
    var COLLAGE_FALLBACK_MAX_DAYS=0 to revert to the pre-2026-07-13
    "no fallback" behavior."""
    if within_days <= 0:
        return None
    row = await pool.fetchrow(
        """
        SELECT id, bot_id, generation_date, theme, image_urls,
               image_urls_blurred, state,
               cost_usd, generated_at, created_at, updated_at
        FROM influencer_collages
        WHERE bot_id = $1
          AND state = 'succeeded'
          AND generation_date >= CURRENT_DATE - $2::int
        ORDER BY generation_date DESC
        LIMIT 1
        """,
        bot_id,
        within_days,
    )
    return dict(row) if row else None


async def count_fallback_serves_last_24h(pool) -> int:
    """Dashboard-tile input: how many today-rows currently sit in
    state='failed'. Each of those, on a request, causes one fallback
    serve — so this is the upper-bound signal for "how often has the
    fallback path fired?". Real count would require an events table
    (Phase 1); this rollup is the ADHD-observability MVP.

    Returns 0 on any DB error so a busted query never breaks the
    dashboard (matches every other tile's degrade-open pattern)."""
    try:
        row = await pool.fetchrow(
            """
            SELECT COUNT(*)::int AS n
            FROM influencer_collages
            WHERE state = 'failed'
              AND generation_date >= CURRENT_DATE - 1
            """
        )
        return int(row["n"]) if row else 0
    except Exception:
        return 0


async def get(pool, bot_id: str, generation_date: date) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, bot_id, generation_date, theme, image_urls,
               image_urls_blurred, state,
               cost_usd, generated_at, created_at, updated_at
        FROM influencer_collages
        WHERE bot_id = $1 AND generation_date = $2
        """,
        bot_id,
        generation_date,
    )
    return dict(row) if row else None


async def get_by_id(pool, collage_id: str) -> dict | None:
    """Fetch a collage by its opaque UUID (migration 048). Preferred
    lookup path for mobile: chat messages store `collage_id` in the
    payload + refetch by it. Falls back to `get()` by (bot_id, date)
    for legacy messages that predate the UUID field."""
    row = await pool.fetchrow(
        """
        SELECT id, bot_id, generation_date, theme, image_urls,
               image_urls_blurred, state,
               cost_usd, generated_at, created_at, updated_at
        FROM influencer_collages
        WHERE id = $1
        """,
        collage_id,
    )
    return dict(row) if row else None


async def recent_themes(pool, bot_id: str, days: int = 7) -> list[str]:
    """Themes used for this bot in the last N days, newest first.
    Feeds the LLM theme generator's "don't repeat" constraint so
    users don't see the same scene twice in a week."""
    rows = await pool.fetch(
        """
        SELECT theme FROM influencer_collages
        WHERE bot_id = $1
          AND generation_date > (CURRENT_DATE - ($2::int * INTERVAL '1 day'))
        ORDER BY generation_date DESC
        """,
        bot_id,
        days,
    )
    return [r["theme"] for r in rows if r["theme"]]
