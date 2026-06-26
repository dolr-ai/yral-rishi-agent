"""Phase 5.6 — daily streak tracking.

For each active conversation, compute the user's current streak — how many
consecutive days they've sent at least one message. Updates run once a day
in a background loop; gating on every send-message would add latency to
the hot path for a feature only used to display a badge.

Streak rules:
  - User sent today → streak unchanged (already counted)
  - User sent yesterday → streak += 1
  - User's last message was more than 1 day ago → streak resets to 0
  - longest_streak_days tracks the peak

Edge: time zones. Postgres `DATE` is naive; we use UTC. Streaks may shift by
a day for users in extreme time zones — acceptable for an engagement signal
where mobile renders a single integer.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

STREAK_UPDATE_INTERVAL_SEC = 24 * 60 * 60  # daily
INITIAL_DELAY_SEC = 5 * 60  # 5 min after startup

# Chunk + per-statement timeout. 2026-06-26: a single big UPDATE locked
# enough conversation rows to deadlock with concurrent send_message
# inserts (which fire the on-message trigger that updates
# conversations.updated_at) — Sentry #246 DeadlockDetectedError + #124
# TimeoutError. Fix: process in deterministically-ordered chunks under
# FOR UPDATE SKIP LOCKED, with a 10s per-statement cap so a single hot
# row never stalls the whole pass. Rows we can't lock this tick get
# picked up next tick — daily cadence, the lag is invisible.
CHUNK_SIZE = 500
STATEMENT_TIMEOUT_MS = 10_000

# Single source of truth for the streak math. Used by both update + reset
# passes via string formatting so the SQL stays readable and the CASE
# doesn't drift between the two columns it feeds (current + longest).
_NEW_STREAK_CASE = """
        CASE
            WHEN input.last_user_date IS NULL THEN 0
            WHEN c.last_streak_date IS NULL THEN 1
            WHEN input.last_user_date = c.last_streak_date THEN c.current_streak_days
            WHEN input.last_user_date = c.last_streak_date + INTERVAL '1 day' THEN c.current_streak_days + 1
            WHEN input.last_user_date > c.last_streak_date + INTERVAL '1 day' THEN 1
            ELSE c.current_streak_days
        END
"""

_UPDATE_CHUNK_SQL = f"""
    WITH locked AS (
        SELECT id FROM conversations
        WHERE id = ANY($1::varchar[])
        ORDER BY id
        FOR UPDATE SKIP LOCKED
    ),
    input AS (
        SELECT id, last_user_date
        FROM unnest($1::varchar[], $2::date[]) AS t(id, last_user_date)
    )
    UPDATE conversations c
    SET current_streak_days = {_NEW_STREAK_CASE},
        longest_streak_days = GREATEST(c.longest_streak_days, {_NEW_STREAK_CASE}),
        last_streak_date = COALESCE(input.last_user_date, c.last_streak_date)
    FROM input
    JOIN locked ON locked.id = input.id
    WHERE c.id = locked.id
"""

_RESET_CHUNK_SQL = """
    WITH locked AS (
        SELECT id FROM conversations
        WHERE id = ANY($1::varchar[])
        ORDER BY id
        FOR UPDATE SKIP LOCKED
    )
    UPDATE conversations c
    SET current_streak_days = 0
    FROM locked
    WHERE c.id = locked.id
      AND c.current_streak_days > 0
"""


def _parse_count(result: str) -> int:
    """asyncpg's execute() returns the command tag, e.g. 'UPDATE 42'."""
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        logger.warning("streak_tracker: could not parse asyncpg result %r", result)
        return 0


async def update_all_streaks_once(pool) -> dict:
    """One pass: for every conversation with any user activity in the last
    365 days, recompute streak fields from the messages history.

    Returns {"updated": N, "reset_to_zero": M}. With SKIP LOCKED, N/M
    count rows we successfully locked + touched this pass; rows held by
    a concurrent writer roll forward to the next pass.
    """
    # Step 1: snapshot per-conversation last user-message date. Pure read
    # on messages — no conversations lock held, no deadlock surface.
    rows = await pool.fetch(
        """
        SELECT m.conversation_id AS id,
               MAX(m.created_at::date) AS last_user_date
        FROM messages m
        WHERE m.role = 'user'
          AND m.created_at > NOW() - INTERVAL '365 days'
        GROUP BY m.conversation_id
        ORDER BY m.conversation_id
        """
    )

    updated = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i : i + CHUNK_SIZE]
        ids = [r["id"] for r in chunk]
        dates = [r["last_user_date"] for r in chunk]
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"
                )
                updated += _parse_count(
                    await conn.execute(_UPDATE_CHUNK_SQL, ids, dates)
                )

    # Step 2: reset streaks for dormant conversations (last_streak_date
    # older than 1 day, or NULL). Snapshot the candidate IDs first, then
    # chunk + SKIP LOCKED same as the forward pass.
    stale_rows = await pool.fetch(
        """
        SELECT id FROM conversations
        WHERE current_streak_days > 0
          AND (last_streak_date IS NULL
               OR last_streak_date < CURRENT_DATE - INTERVAL '1 day')
        ORDER BY id
        """
    )
    reset = 0
    for i in range(0, len(stale_rows), CHUNK_SIZE):
        chunk = stale_rows[i : i + CHUNK_SIZE]
        ids = [r["id"] for r in chunk]
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"
                )
                reset += _parse_count(await conn.execute(_RESET_CHUNK_SQL, ids))

    return {"updated": updated, "reset_to_zero": reset}


async def streak_loop():
    """Run update_all_streaks_once every 24h after an initial delay."""
    from database import get_pool
    from kill_switch import is_enabled

    await asyncio.sleep(INITIAL_DELAY_SEC)
    while True:
        try:
            # Emergency kill-switch — env-var symmetry with the Gemini
            # loops, even though streak_tracker doesn't call Gemini.
            # Lets ops kill the whole background side with one config.
            if not is_enabled("streak"):
                await asyncio.sleep(STREAK_UPDATE_INTERVAL_SEC)
                continue
            pool = await get_pool()
            t0 = asyncio.get_event_loop().time()
            stats = await update_all_streaks_once(pool)
            elapsed = asyncio.get_event_loop().time() - t0
            logger.info(
                f"streak_tracker: updated {stats['updated']}, "
                f"reset {stats['reset_to_zero']} in {elapsed:.1f}s"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Use logger.exception + repr — some asyncpg errors stringify
            # empty ("") which previously produced `… pass failed: ` with no
            # signal at all. exception() captures the traceback; the
            # `[{type(e).__name__}] {e!r}` prefix guarantees the level line
            # itself is searchable even when the traceback is collapsed.
            logger.exception(
                "streak_tracker pass failed (non-fatal) [%s]: %r",
                type(e).__name__,
                e,
            )
        await asyncio.sleep(STREAK_UPDATE_INTERVAL_SEC)
