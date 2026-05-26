import json
import uuid
import logging

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    return dict(row)


async def create(
    pool,
    conversation_id: str,
    role: str,
    content: str | None,
    message_type: str,
    media_urls: list[str] | None = None,
    audio_url: str | None = None,
    audio_duration_seconds: int | None = None,
    token_count: int | None = None,
    client_message_id: str | None = None,
    sender_id: str | None = None,
) -> dict:
    message_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO messages (
            id, conversation_id, role, sender_id, content, message_type,
            media_urls, audio_url, audio_duration_seconds, token_count,
            client_message_id, status, is_read
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'delivered', FALSE)
        """,
        message_id,
        conversation_id,
        role,
        sender_id,
        content,
        message_type,
        json.dumps(media_urls or []),
        audio_url,
        audio_duration_seconds,
        token_count,
        client_message_id,
    )
    return await get_by_id(pool, message_id)


async def get_by_id(pool, message_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, conversation_id, role, sender_id, content, message_type,
               media_urls, audio_url, audio_duration_seconds, token_count,
               client_message_id, created_at, metadata, status, is_read
        FROM messages WHERE id = $1
        """,
        message_id,
    )
    return _row_to_dict(row) if row else None


async def get_by_client_id(
    pool,
    conversation_id: str,
    client_message_id: str,
) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, conversation_id, role, sender_id, content, message_type,
               media_urls, audio_url, audio_duration_seconds, token_count,
               client_message_id, created_at, metadata, status, is_read
        FROM messages
        WHERE conversation_id = $1 AND client_message_id = $2
        """,
        conversation_id,
        client_message_id,
    )
    return _row_to_dict(row) if row else None


async def get_assistant_reply(pool, message_id: str) -> dict | None:
    original = await get_by_id(pool, message_id)
    if not original:
        return None

    row = await pool.fetchrow(
        """
        SELECT id, conversation_id, role, sender_id, content, message_type,
               media_urls, audio_url, audio_duration_seconds, token_count,
               client_message_id, created_at, metadata, status, is_read
        FROM messages
        WHERE conversation_id = $1 AND role = 'assistant'
              AND created_at >= $2 AND id != $3
        ORDER BY created_at ASC LIMIT 1
        """,
        original["conversation_id"],
        original["created_at"],
        message_id,
    )
    return _row_to_dict(row) if row else None


async def list_by_conversation(
    pool,
    conversation_id: str,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc",
) -> list[dict]:
    order_clause = "ASC" if order.lower() == "asc" else "DESC"
    rows = await pool.fetch(
        f"""
        SELECT id, conversation_id, role, sender_id, content, message_type,
               media_urls, audio_url, audio_duration_seconds, token_count,
               client_message_id, created_at, metadata, status, is_read
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at {order_clause}
        LIMIT $2 OFFSET $3
        """,
        conversation_id,
        limit,
        offset,
    )
    return [_row_to_dict(r) for r in rows]


async def get_recent_for_context(
    pool, conversation_id: str, limit: int = 11
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, conversation_id, role, sender_id, content, message_type,
               media_urls, audio_url, audio_duration_seconds, token_count,
               client_message_id, created_at, metadata, status, is_read
        FROM messages
        WHERE conversation_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        conversation_id,
        limit,
    )
    return [_row_to_dict(r) for r in reversed(rows)]


async def get_recent_for_conversations_batch(
    pool,
    conversation_ids: list[str],
    limit_per_conv: int = 10,
) -> list[dict]:
    if not conversation_ids:
        return []
    rows = await pool.fetch(
        """
        WITH RankedMessages AS (
            SELECT id, conversation_id, role, sender_id, content, message_type,
                   media_urls, audio_url, audio_duration_seconds, token_count,
                   client_message_id, created_at, metadata, status, is_read,
                   ROW_NUMBER() OVER (
                       PARTITION BY conversation_id ORDER BY created_at DESC
                   ) as rn
            FROM messages WHERE conversation_id = ANY($1)
        )
        SELECT id, conversation_id, role, sender_id, content, message_type,
               media_urls, audio_url, audio_duration_seconds, token_count,
               client_message_id, created_at, metadata, status, is_read
        FROM RankedMessages
        WHERE rn <= $2
        ORDER BY conversation_id, created_at ASC
        """,
        conversation_ids,
        limit_per_conv,
    )
    return [_row_to_dict(r) for r in rows]


async def count_by_conversation(pool, conversation_id: str) -> int:
    return await pool.fetchval(
        "SELECT COUNT(*) FROM messages WHERE conversation_id = $1",
        conversation_id,
    )


async def count_unread(pool, conversation_id: str) -> int:
    return await pool.fetchval(
        """
        SELECT COUNT(*) FROM messages
        WHERE conversation_id = $1 AND is_read = FALSE AND role = 'assistant'
        """,
        conversation_id,
    )


async def mark_as_read(pool, conversation_id: str):
    await pool.execute(
        """
        UPDATE messages
        SET is_read = TRUE, status = 'read'
        WHERE conversation_id = $1 AND is_read = FALSE AND role = 'assistant'
        """,
        conversation_id,
    )


async def delete_by_conversation(pool, conversation_id: str) -> int:
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM messages WHERE conversation_id = $1",
        conversation_id,
    )
    if count > 0:
        await pool.execute(
            "DELETE FROM messages WHERE conversation_id = $1",
            conversation_id,
        )
    return count
