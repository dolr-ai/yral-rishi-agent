import json
import uuid
import logging

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    return dict(row)


async def create(pool, user_id: str, influencer_id: str) -> dict:
    conversation_id = str(uuid.uuid4())
    row = await pool.fetchrow(
        """
        INSERT INTO conversations (id, user_id, influencer_id)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id, influencer_id) WHERE influencer_id IS NOT NULL
        DO NOTHING
        RETURNING id
        """,
        conversation_id,
        user_id,
        influencer_id,
    )
    if row is None:
        existing = await get_existing(pool, user_id, influencer_id)
        if existing is None:
            raise RuntimeError(
                f"ON CONFLICT matched but no row found for "
                f"user_id={user_id} influencer_id={influencer_id}"
            )
        return existing
    return await get_by_id(pool, row["id"])


async def get_by_id(pool, conversation_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT c.id, c.user_id, c.influencer_id, c.created_at, c.updated_at,
               c.metadata, c.conversation_type, c.participant_b_id,
               c.human_creator_takeover_active, c.human_creator_user_id,
               c.human_creator_takeover_started_at, c.user_last_message_at,
               c.human_creator_last_message_at,
               i.id as inf_id, i.name as inf_name,
               i.display_name as inf_display_name,
               i.avatar_url as inf_avatar_url,
               i.category as inf_category,
               i.suggested_messages as inf_suggested_messages,
               i.is_nsfw as inf_is_nsfw,
               i.parent_principal_id as inf_parent_principal_id
        FROM conversations c
        LEFT JOIN ai_influencers i ON c.influencer_id = i.id
        WHERE c.id = $1
        """,
        conversation_id,
    )
    return _row_to_dict(row) if row else None


async def get_existing(pool, user_id: str, influencer_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT c.id, c.user_id, c.influencer_id, c.created_at, c.updated_at,
               c.metadata, c.conversation_type, c.participant_b_id,
               i.id as inf_id, i.name as inf_name,
               i.display_name as inf_display_name,
               i.avatar_url as inf_avatar_url,
               i.category as inf_category,
               i.suggested_messages as inf_suggested_messages,
               i.is_nsfw as inf_is_nsfw
        FROM conversations c
        LEFT JOIN ai_influencers i ON c.influencer_id = i.id
        WHERE c.user_id = $1 AND c.influencer_id = $2
        """,
        user_id,
        influencer_id,
    )
    return _row_to_dict(row) if row else None


async def list_by_user(
    pool,
    user_id: str,
    influencer_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    if influencer_id:
        rows = await pool.fetch(
            """
            SELECT c.id, c.user_id, c.influencer_id, c.created_at, c.updated_at,
                   c.metadata, c.conversation_type,
                   i.id as inf_id, i.name as inf_name,
                   i.display_name as inf_display_name,
                   i.avatar_url as inf_avatar_url,
                   i.category as inf_category,
                   i.suggested_messages as inf_suggested_messages,
               i.is_nsfw as inf_is_nsfw,
                   COUNT(m.id) as message_count,
                   (SELECT COUNT(*) FROM messages m2
                    WHERE m2.conversation_id = c.id
                    AND m2.is_read = FALSE AND m2.role = 'assistant') as unread_count
            FROM conversations c
            JOIN ai_influencers i ON c.influencer_id = i.id
            LEFT JOIN messages m ON c.id = m.conversation_id
            WHERE c.user_id = $1 AND c.influencer_id = $2
                  AND i.is_active != 'discontinued'
                  AND c.user_id NOT IN (SELECT id FROM ai_influencers)
            GROUP BY c.id, i.id
            ORDER BY c.updated_at DESC
            LIMIT $3 OFFSET $4
            """,
            user_id,
            influencer_id,
            limit,
            offset,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT c.id, c.user_id, c.influencer_id, c.created_at, c.updated_at,
                   c.metadata, c.conversation_type,
                   i.id as inf_id, i.name as inf_name,
                   i.display_name as inf_display_name,
                   i.avatar_url as inf_avatar_url,
                   i.category as inf_category,
                   i.suggested_messages as inf_suggested_messages,
               i.is_nsfw as inf_is_nsfw,
                   COUNT(m.id) as message_count,
                   (SELECT COUNT(*) FROM messages m2
                    WHERE m2.conversation_id = c.id
                    AND m2.is_read = FALSE AND m2.role = 'assistant') as unread_count
            FROM conversations c
            JOIN ai_influencers i ON c.influencer_id = i.id
            LEFT JOIN messages m ON c.id = m.conversation_id
            WHERE c.user_id = $1
                  AND i.is_active != 'discontinued'
                  AND c.user_id NOT IN (SELECT id FROM ai_influencers)
            GROUP BY c.id, i.id
            ORDER BY c.updated_at DESC
            LIMIT $2 OFFSET $3
            """,
            user_id,
            limit,
            offset,
        )
    return [_row_to_dict(r) for r in rows]


async def count_by_user(pool, user_id: str, influencer_id: str | None = None) -> int:
    if influencer_id:
        return await pool.fetchval(
            """
            SELECT COUNT(*) FROM conversations c
            JOIN ai_influencers i ON c.influencer_id = i.id
            WHERE c.user_id = $1 AND c.influencer_id = $2
                  AND i.is_active != 'discontinued'
                  AND c.user_id NOT IN (SELECT id FROM ai_influencers)
            """,
            user_id,
            influencer_id,
        )
    return await pool.fetchval(
        """
        SELECT COUNT(*) FROM conversations c
        JOIN ai_influencers i ON c.influencer_id = i.id
        WHERE c.user_id = $1
              AND i.is_active != 'discontinued'
              AND c.user_id NOT IN (SELECT id FROM ai_influencers)
        """,
        user_id,
    )


async def list_by_influencer(
    pool,
    influencer_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT c.id, c.user_id, c.influencer_id, c.created_at, c.updated_at,
               c.metadata, c.conversation_type,
               COUNT(m.id) as message_count,
               (SELECT COUNT(*) FROM messages m2
                WHERE m2.conversation_id = c.id
                AND m2.is_read = FALSE AND m2.role = 'user') as unread_count
        FROM conversations c
        LEFT JOIN messages m ON c.id = m.conversation_id
        WHERE c.influencer_id = $1
        GROUP BY c.id
        ORDER BY c.updated_at DESC
        LIMIT $2 OFFSET $3
        """,
        influencer_id,
        limit,
        offset,
    )
    return [_row_to_dict(r) for r in rows]


async def count_by_influencer(pool, influencer_id: str) -> int:
    return await pool.fetchval(
        "SELECT COUNT(*) FROM conversations WHERE influencer_id = $1",
        influencer_id,
    )


async def get_last_messages_batch(pool, conversation_ids: list[str]) -> list[dict]:
    if not conversation_ids:
        return []
    rows = await pool.fetch(
        """
        SELECT m1.conversation_id, m1.content, m1.role,
               m1.created_at, m1.status, m1.is_read
        FROM messages m1
        INNER JOIN (
            SELECT conversation_id, MAX(created_at) as max_created
            FROM messages
            WHERE conversation_id = ANY($1)
            GROUP BY conversation_id
        ) m2 ON m1.conversation_id = m2.conversation_id
           AND m1.created_at = m2.max_created
        """,
        conversation_ids,
    )
    return [_row_to_dict(r) for r in rows]


async def update_metadata(pool, conversation_id: str, metadata: dict):
    await pool.execute(
        """
        UPDATE conversations
        SET metadata = $1, updated_at = NOW()
        WHERE id = $2
        """,
        json.dumps(metadata),
        conversation_id,
    )


async def delete(pool, conversation_id: str):
    await pool.execute(
        "DELETE FROM conversations WHERE id = $1",
        conversation_id,
    )
