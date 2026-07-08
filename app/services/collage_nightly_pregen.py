"""Nightly pre-gen loop for Request Images (design §4 §1c HYBRID).

Every day at 04:00 UTC, iterate LoRA-enabled active bots and generate
today's collage IF the reservation row doesn't already exist. Users
who tap Request Images later in the day get an instant response
instead of a 45–65s wait.

Design semantic: 04:00 UTC is early enough that most user timezones
(India starts around 09:30 IST, Southeast Asia similar) haven't
opened the app for the day yet — pre-gen finishes before the peak
usage window starts. If a user DOES request between 00:00 UTC and
04:00 UTC (before the pre-gen fires), they still get the on-demand
path — reservation lock is idempotent.

Which bots: those with `ai_influencers.lora_weights_url IS NOT NULL`
AND `is_active = 'active'`. Phase 0 = Tara only. A future "hot bots"
gate (design §1c — ≥N chats OR ≥M image-requests over 7d) will
prune to actively-used bots when the catalog grows past 5-10.

Cost guardrail: same daily-budget hard cap as the on-demand path
(image_collage._check_budget). Pre-gen contributes to the same
counter, so an operator flipping COLLAGE_DAILY_BUDGET_HARD_USD down
stops both paths symmetrically.

Kill-switch: `collage_pregen` → `ENABLE_COLLAGE_PREGEN_LOOP`.
Defaults OFF so operators explicitly opt-in — new background loops
that spend money should never surprise on first deploy (Rishi's
2026-05-29 Gemini burn lesson).
"""

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)


# 04:00 UTC per design doc §4. Not env-tunable because the cadence
# needs to be stable — moving it around means users on the edge get
# inconsistent instant-vs-wait UX day to day.
_PREGEN_HOUR_UTC = 4


def _seconds_until_next_pregen_utc() -> int:
    """Compute the exact sleep to the next 04:00 UTC. If we're past
    04:00 today, target 04:00 tomorrow."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=_PREGEN_HOUR_UTC, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


async def _list_pregen_candidates(pool) -> list[dict]:
    """LoRA-enabled active bots. Ordered by id so the pre-gen sweep is
    deterministic (helps debugging + partial-run resumability)."""
    rows = await pool.fetch(
        """
        SELECT id, lora_weights_url
        FROM ai_influencers
        WHERE lora_weights_url IS NOT NULL
          AND is_active = 'active'
        ORDER BY id
        """
    )
    return [dict(r) for r in rows]


async def pregen_one_pass(pool) -> dict:
    """One pass = iterate candidate bots, skip any with today's row
    already succeeded, orchestrate() the rest.

    Returns per-bot stats: {generated, skipped, failed}. Used by the
    loop's INFO log line + admin visibility.

    Isolated from the loop so tests can assert behavior without
    dealing with timers."""
    from repositories import influencer_collage_repo
    from services import image_collage, theme_generator

    today = _today_utc()
    candidates = await _list_pregen_candidates(pool)

    stats = {"candidates": len(candidates), "generated": 0, "skipped": 0, "failed": 0}
    for bot in candidates:
        bot_id = bot["id"]
        existing = await influencer_collage_repo.get(pool, bot_id, today)
        if existing and existing["state"] == "succeeded":
            stats["skipped"] += 1
            continue

        theme = await theme_generator.generate_daily_theme(pool, bot_id)
        result = await image_collage.orchestrate(
            pool,
            user_id="__pregen__",  # synthetic; real user_ids are IC principals
            bot_id=bot_id,
            theme=theme,
            lora_weights_url=bot["lora_weights_url"],
            consume_quota=False,  # pregen doesn't burn any user's quota
        )
        if result.get("status") == "ready":
            stats["generated"] += 1
            logger.info(
                "collage_pregen: bot=%s pregen OK, theme=%r",
                bot_id,
                (theme or "")[:120],
            )
        else:
            stats["failed"] += 1
            logger.warning(
                "collage_pregen: bot=%s pregen FAILED status=%r reason=%r",
                bot_id,
                result.get("status"),
                result.get("reason"),
            )
    return stats


async def collage_pregen_loop():
    """Sleep until next 04:00 UTC → one pass → repeat every 24h.

    Kill-switch pattern lifted from streak_loop / video_ideas_loop.
    A pass failure is logged with the exception + traceback and does
    NOT crash the loop — a bad Gemini or Replicate day tomorrow should
    still see the loop try again the day after."""
    from database import get_pool
    from kill_switch import is_enabled

    while True:
        try:
            sleep_for = _seconds_until_next_pregen_utc()
            logger.info(
                "collage_pregen: sleeping %ds until next 04:00 UTC pass",
                sleep_for,
            )
            await asyncio.sleep(sleep_for)

            if not is_enabled("collage_pregen"):
                logger.info("collage_pregen: kill-switch off; skipping today's pass")
                continue

            pool = await get_pool()
            t0 = time.monotonic()
            stats = await pregen_one_pass(pool)
            elapsed = time.monotonic() - t0
            logger.info(
                "collage_pregen: pass complete in %.1fs %s",
                elapsed,
                stats,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(
                "collage_pregen pass failed (non-fatal) [%s]: %r",
                type(e).__name__,
                e,
            )
