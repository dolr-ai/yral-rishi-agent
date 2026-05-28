import logging

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    return dict(row)


def _vector_literal(values: list[float] | None) -> str | None:
    """asyncpg has no native pgvector codec; we cast a string literal in SQL."""
    if values is None:
        return None
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


async def upsert(
    pool,
    user_id: str,
    influencer_id: str | None,
    category: str,
    key: str,
    value: str,
    source_message_id: str | None = None,
    confidence: float = 1.0,
    embedding: list[float] | None = None,
) -> dict:
    """Upsert a memory. If embedding is provided, store it; otherwise leave the
    column NULL and let the backfill / next-write fill it in.

    influencer_id=None marks a global memory (Phase 4.6) — shared across every
    bot the user chats with. The unique index uses NULLS NOT DISTINCT
    (migration 009) so global rows dedupe on (user_id, NULL, key).

    On UPDATE (key collision), we overwrite the embedding too — the value
    changed, so the old embedding is stale.
    """
    emb_literal = _vector_literal(embedding)
    row = await pool.fetchrow(
        """
        INSERT INTO user_memories (
            user_id, influencer_id, category, key, value, confidence,
            source_message_id, embedding
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::vector)
        ON CONFLICT (user_id, influencer_id, key)
        DO UPDATE SET
            value = EXCLUDED.value,
            confidence = EXCLUDED.confidence,
            source_message_id = EXCLUDED.source_message_id,
            embedding = EXCLUDED.embedding,
            updated_at = NOW()
        RETURNING *
        """,
        user_id,
        influencer_id,
        category,
        key,
        value,
        confidence,
        source_message_id,
        emb_literal,
    )
    return _row_to_dict(row)


async def update_embedding(pool, memory_id: str, embedding: list[float]):
    """Set the embedding on an existing row. Used by the backfill script."""
    await pool.execute(
        "UPDATE user_memories SET embedding = $1::vector WHERE id = $2",
        _vector_literal(embedding),
        memory_id,
    )


async def list_missing_embedding(pool, limit: int = 50) -> list[dict]:
    """Used by backfill: rows that still need an embedding."""
    rows = await pool.fetch(
        """
        SELECT id, category, key, value
        FROM user_memories
        WHERE embedding IS NULL
        ORDER BY updated_at
        LIMIT $1
        """,
        limit,
    )
    return [_row_to_dict(r) for r in rows]


async def semantic_search(
    pool,
    user_id: str,
    influencer_id: str,
    query_embedding: list[float],
    top_k: int = 5,
) -> list[dict]:
    """Top-K memories by cosine distance to query_embedding.

    Falls back gracefully: rows with embedding=NULL are excluded by the
    ORDER BY (NULL sorts last under <=>). If query_embedding is empty,
    returns no rows.
    """
    if not query_embedding:
        return []
    rows = await pool.fetch(
        """
        SELECT category, key, value, confidence, updated_at,
               (embedding <=> $3::vector) AS distance
        FROM user_memories
        WHERE user_id = $1
          AND (influencer_id = $2 OR influencer_id IS NULL)
          AND embedding IS NOT NULL
        ORDER BY embedding <=> $3::vector
        LIMIT $4
        """,
        user_id,
        influencer_id,
        _vector_literal(query_embedding),
        top_k,
    )
    return [_row_to_dict(r) for r in rows]


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
