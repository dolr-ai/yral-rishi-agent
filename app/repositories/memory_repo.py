import logging

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    return dict(row)


async def upsert(
    pool,
    user_id: str,
    influencer_id: str,
    category: str,
    key: str,
    value: str,
    source_message_id: str | None = None,
    confidence: float = 1.0,
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO user_memories (user_id, influencer_id, category, key, value, confidence, source_message_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (user_id, influencer_id, key)
        DO UPDATE SET value = $5, confidence = $6, source_message_id = $7, updated_at = NOW()
        RETURNING *
        """,
        user_id,
        influencer_id,
        category,
        key,
        value,
        confidence,
        source_message_id,
    )
    return _row_to_dict(row)


async def get_for_user_influencer(pool, user_id: str, influencer_id: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT category, key, value, confidence, updated_at
        FROM user_memories
        WHERE user_id = $1 AND influencer_id = $2
        ORDER BY updated_at DESC
        """,
        user_id,
        influencer_id,
    )
    return [_row_to_dict(r) for r in rows]


async def get_for_user_global(pool, user_id: str) -> list[dict]:
    """Get memories shared across all influencers (influencer_id IS NULL)."""
    rows = await pool.fetch(
        """
        SELECT category, key, value, confidence, updated_at
        FROM user_memories
        WHERE user_id = $1 AND influencer_id IS NULL
        ORDER BY updated_at DESC
        """,
        user_id,
    )
    return [_row_to_dict(r) for r in rows]


async def get_all_for_user(pool, user_id: str, influencer_id: str) -> list[dict]:
    """Get both influencer-specific and global memories for a user."""
    rows = await pool.fetch(
        """
        SELECT category, key, value, confidence, updated_at
        FROM user_memories
        WHERE user_id = $1 AND (influencer_id = $2 OR influencer_id IS NULL)
        ORDER BY confidence DESC, updated_at DESC
        """,
        user_id,
        influencer_id,
    )
    return [_row_to_dict(r) for r in rows]


async def delete_for_user(pool, user_id: str, influencer_id: str | None = None):
    if influencer_id:
        await pool.execute(
            "DELETE FROM user_memories WHERE user_id = $1 AND influencer_id = $2",
            user_id,
            influencer_id,
        )
    else:
        await pool.execute("DELETE FROM user_memories WHERE user_id = $1", user_id)
