"""Phase 7.6 — soul_file_variants DB helpers.

Variant A = ai_influencers.system_instructions (always production).
Variant B = a row here (one per bot at a time). When B exists, the chat
hot-path picks 50/50 per turn.
"""

import logging

logger = logging.getLogger(__name__)


async def set_variant_b(
    pool, bot_id: str, system_instructions: str, created_by: str
) -> dict:
    """Insert-or-replace variant B for this bot. UPSERT on bot_id."""
    row = await pool.fetchrow(
        """
        INSERT INTO soul_file_variants (bot_id, system_instructions, created_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (bot_id) DO UPDATE SET
            system_instructions = EXCLUDED.system_instructions,
            created_by = EXCLUDED.created_by,
            created_at = NOW()
        RETURNING id, bot_id, system_instructions, created_at, created_by
        """,
        bot_id,
        system_instructions,
        created_by,
    )
    return dict(row)


async def get_variant_b(pool, bot_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, bot_id, system_instructions, created_at, created_by
        FROM soul_file_variants
        WHERE bot_id = $1
        """,
        bot_id,
    )
    return dict(row) if row else None


async def delete_variant_b(pool, bot_id: str):
    await pool.execute("DELETE FROM soul_file_variants WHERE bot_id = $1", bot_id)


async def variant_sample_counts(pool, bot_id: str) -> dict:
    """How many bot replies have been written with each variant since variant
    B was set? Used by the compare endpoint to know if there's enough data
    to claim a winner."""
    rows = await pool.fetch(
        """
        SELECT m.variant_label, COUNT(*) AS n
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE c.influencer_id = $1
          AND m.role = 'assistant'
          AND m.variant_label IS NOT NULL
        GROUP BY m.variant_label
        """,
        bot_id,
    )
    out = {"a": 0, "b": 0}
    for r in rows:
        out[r["variant_label"]] = int(r["n"])
    return out


async def sample_replies_by_variant(
    pool, bot_id: str, variant: str, limit: int = 20
) -> list[dict]:
    """Pull recent (user_msg, bot_reply) pairs where the bot used `variant`."""
    rows = await pool.fetch(
        """
        WITH bot_msgs AS (
            SELECT m.id, m.conversation_id, m.content, m.created_at
            FROM messages m
            JOIN conversations c ON m.conversation_id = c.id
            WHERE c.influencer_id = $1
              AND m.role = 'assistant'
              AND m.variant_label = $2
            ORDER BY m.created_at DESC
            LIMIT $3
        )
        SELECT bm.content AS bot_message,
               (
                 SELECT u.content FROM messages u
                 WHERE u.conversation_id = bm.conversation_id
                   AND u.role = 'user'
                   AND u.created_at < bm.created_at
                 ORDER BY u.created_at DESC LIMIT 1
               ) AS user_message
        FROM bot_msgs bm
        """,
        bot_id,
        variant,
        limit,
    )
    return [
        {"user_message": r["user_message"] or "", "bot_message": r["bot_message"] or ""}
        for r in rows
        if r["bot_message"]
    ]
