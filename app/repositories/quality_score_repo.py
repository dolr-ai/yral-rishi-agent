"""Phase 7.7 — bot_quality_scores DB helpers.

History table: one row per (bot, scoring_run). Latest-per-bot via the
idx_bqs_bot_recent index.
"""

import logging

logger = logging.getLogger(__name__)


async def insert(
    pool,
    bot_id: str,
    score_overall: float,
    score_in_character: float,
    score_response_quality: float,
    score_engagement: float,
    last_n_conversations: int,
    sample_size: int,
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO bot_quality_scores (
            bot_id, score_overall, score_in_character,
            score_response_quality, score_engagement,
            last_n_conversations, sample_size
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, bot_id, score_overall, score_in_character,
                  score_response_quality, score_engagement,
                  last_n_conversations, sample_size, created_at
        """,
        bot_id,
        score_overall,
        score_in_character,
        score_response_quality,
        score_engagement,
        last_n_conversations,
        sample_size,
    )
    return dict(row)


async def latest_for_bot(pool, bot_id: str) -> dict | None:
    """The most recent score for a bot, or None if it's never been scored."""
    row = await pool.fetchrow(
        """
        SELECT id, bot_id, score_overall, score_in_character,
               score_response_quality, score_engagement,
               last_n_conversations, sample_size, created_at
        FROM bot_quality_scores
        WHERE bot_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        bot_id,
    )
    return dict(row) if row else None
