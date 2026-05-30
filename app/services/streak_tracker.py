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


async def update_all_streaks_once(pool) -> dict:
    """One pass: for every conversation with any user activity in the last
    365 days, recompute streak fields from the messages history.

    Returns a stats dict (used by the loop + smoke tests).
    """
    # We do this in one big SQL pass — cheap, atomic, no cross-row Python loop.
    # The CTE `latest` computes the latest user-message DATE per conversation;
    # the UPDATE rolls the streak forward / resets / keeps based on the gap.
    result = await pool.execute(
        """
        WITH latest AS (
            SELECT
                m.conversation_id,
                MAX(m.created_at::date) AS last_user_date
            FROM messages m
            WHERE m.role = 'user'
              AND m.created_at > NOW() - INTERVAL '365 days'
            GROUP BY m.conversation_id
        )
        UPDATE conversations c
        SET
            current_streak_days = CASE
                WHEN latest.last_user_date IS NULL THEN 0
                WHEN c.last_streak_date IS NULL THEN 1
                WHEN latest.last_user_date = c.last_streak_date THEN c.current_streak_days
                WHEN latest.last_user_date = c.last_streak_date + INTERVAL '1 day' THEN c.current_streak_days + 1
                WHEN latest.last_user_date > c.last_streak_date + INTERVAL '1 day' THEN 1
                ELSE c.current_streak_days
            END,
            longest_streak_days = GREATEST(
                c.longest_streak_days,
                CASE
                    WHEN latest.last_user_date IS NULL THEN 0
                    WHEN c.last_streak_date IS NULL THEN 1
                    WHEN latest.last_user_date = c.last_streak_date THEN c.current_streak_days
                    WHEN latest.last_user_date = c.last_streak_date + INTERVAL '1 day' THEN c.current_streak_days + 1
                    WHEN latest.last_user_date > c.last_streak_date + INTERVAL '1 day' THEN 1
                    ELSE c.current_streak_days
                END
            ),
            last_streak_date = COALESCE(latest.last_user_date, c.last_streak_date)
        FROM latest
        WHERE c.id = latest.conversation_id
        """
    )
    # result is "UPDATE N"
    try:
        updated = int(result.split()[-1])
    except (ValueError, IndexError):
        updated = -1

    # Reset streaks for conversations whose user hasn't sent in > 1 day —
    # the JOIN above only touches rows in `latest`, so dormant conversations
    # need a second pass.
    result2 = await pool.execute(
        """
        UPDATE conversations
        SET current_streak_days = 0
        WHERE current_streak_days > 0
          AND (last_streak_date IS NULL
               OR last_streak_date < CURRENT_DATE - INTERVAL '1 day')
        """
    )
    try:
        reset = int(result2.split()[-1])
    except (ValueError, IndexError):
        reset = -1

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
            logger.warning(f"streak_tracker pass failed (non-fatal): {e}")
        await asyncio.sleep(STREAK_UPDATE_INTERVAL_SEC)
