"""Repository for `reply_evaluations` (migration 044). Shape mirrors
the other Phase 19+ repos: one file per table-group, raw SQL via
asyncpg, no ORM. Write-side helper for the eval service, read-side
helper for the admin dashboard tile, recent-bot-replies helper for
the repetition score.
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def insert(
    pool,
    *,
    message_id: str,
    bot_id: str,
    user_id: str,
    text: str,
    evaluation,
) -> None:
    """One row per assistant reply. Idempotent via the message_id
    unique constraint — a fire-and-forget retry collapses to a no-op.

    `evaluation` is a services.reply_eval.L0Evaluation; we splat its
    fields one-for-one into the INSERT so a new column is a write-
    side change only."""
    await pool.execute(
        """
        INSERT INTO reply_evaluations (
            message_id, bot_id, user_id, text,
            leak_flags, repetition_score, emoji_count,
            char_length, ends_in_question
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
        ON CONFLICT (message_id) DO NOTHING
        """,
        message_id,
        bot_id,
        user_id,
        text,
        json.dumps(evaluation.leak_flags),
        evaluation.repetition_score,
        evaluation.emoji_count,
        evaluation.char_length,
        evaluation.ends_in_question,
    )


async def recent_bot_reply_texts(
    pool,
    *,
    bot_id: str,
    limit: int,
    exclude_message_id: str | None = None,
) -> list[str]:
    """The bot's last `limit` reply texts, newest first, excluding
    the message we're about to evaluate (so a reply doesn't compare
    against itself if the eval reran)."""
    rows = await pool.fetch(
        """
        SELECT text
        FROM reply_evaluations
        WHERE bot_id = $1
          AND ($2::varchar IS NULL OR message_id <> $2)
        ORDER BY created_at DESC
        LIMIT $3
        """,
        bot_id,
        exclude_message_id,
        limit,
    )
    return [r["text"] for r in rows]


async def summary_24h(pool) -> dict:
    """Aggregate for the /admin/dashboard tile. Returns:
      total: rows in the last 24h
      with_any_leak: rows where at least one leak_flag is true
      avg_repetition: mean repetition_score across the window
      median_repetition: 50th-percentile repetition_score
    All-zero if the table is empty or no rows in window."""
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*)                                       AS total,
            COUNT(*) FILTER (
                WHERE EXISTS (
                    SELECT 1
                    FROM jsonb_each(leak_flags) AS f(key, value)
                    WHERE value = 'true'::jsonb
                )
            )                                              AS with_any_leak,
            COALESCE(AVG(repetition_score), 0)::float      AS avg_repetition,
            COALESCE(
                percentile_cont(0.5) WITHIN GROUP (ORDER BY repetition_score),
                0
            )::float                                       AS median_repetition,
            MAX(created_at)                                AS most_recent
        FROM reply_evaluations
        WHERE created_at > NOW() - INTERVAL '24 hours'
        """
    )
    return {
        "total": int(row["total"] or 0),
        "with_any_leak": int(row["with_any_leak"] or 0),
        "avg_repetition": float(row["avg_repetition"] or 0.0),
        "median_repetition": float(row["median_repetition"] or 0.0),
        "most_recent": row["most_recent"],
    }


def humanize_age_seconds(ts) -> str:
    """Compact 'Xs/Xm/Xh ago' formatter for the dashboard tile."""
    if ts is None:
        return "—"
    age = int((datetime.now(timezone.utc) - ts).total_seconds())
    if age < 60:
        return f"{age}s ago"
    if age < 3600:
        return f"{age // 60}m ago"
    return f"{age // 3600}h ago"
