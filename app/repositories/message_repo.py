import json
import logging
import uuid
from datetime import date

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
    is_proactive: bool = False,
    is_nudge: bool = False,
    variant_label: str | None = None,
    collage_id: str | None = None,
    collage_bot_id: str | None = None,
    collage_date: date | None = None,
) -> dict:
    """Persist a message row. The collage_* triple (2026-07-13,
    migration 050) is the self-healing render reference (design §5) —
    populated by the route layer only when the incoming payload has
    them; nullable columns forgive any combination.

    Same shape/optional-kwarg pattern as media_urls + audio_url —
    the SYMMETRY rule Rishi cares about (Rule 1)."""
    message_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO messages (
            id, conversation_id, role, sender_id, content, message_type,
            media_urls, audio_url, audio_duration_seconds, token_count,
            client_message_id, status, is_read, is_proactive, is_nudge,
            variant_label, collage_id, collage_bot_id, collage_date
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'delivered', FALSE,
            $12, $13, $14, $15::uuid, $16, $17
        )
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
        is_proactive,
        is_nudge,
        variant_label,
        str(collage_id) if collage_id is not None else None,
        collage_bot_id,
        collage_date,
    )
    return await get_by_id(pool, message_id)


# Task 2 (Phase 5 polish) — proactive-quality helpers.
# Phase 6.3p polish — nudge cap (parallel pattern). 1, not 3 — nudges
# fire every 15 min during early-conversation idle; one try is plenty.
# If the user didn't respond, they're not engaging right now.

PROACTIVE_CAP_WITHOUT_REPLY = 3
NUDGE_CAP_WITHOUT_REPLY = 1


async def count_unanswered_proactive(pool, conversation_id: str) -> int:
    """Count proactive messages sent since the last user reply in this
    conversation. Used by the engagement loop to enforce a 3-cap.

    "Since the last user reply" = since the most recent row with role='user'.
    If the user has never replied, the count is just all proactive rows.
    """
    row = await pool.fetchrow(
        """
        WITH last_user AS (
            SELECT COALESCE(MAX(created_at), 'epoch'::timestamp) AS ts
            FROM messages
            WHERE conversation_id = $1 AND role = 'user'
        )
        SELECT COUNT(*) AS n
        FROM messages, last_user
        WHERE conversation_id = $1
          AND is_proactive = TRUE
          AND created_at > last_user.ts
        """,
        conversation_id,
    )
    return int(row["n"]) if row else 0


async def count_unanswered_nudge(pool, conversation_id: str) -> int:
    """Count nudge messages sent since the last user reply. Used by the
    Phase 6 nudge engine to enforce a 1-cap (see NUDGE_CAP_WITHOUT_REPLY).
    Same shape as count_unanswered_proactive — kept symmetric so future
    cap-pattern audits see both at once."""
    row = await pool.fetchrow(
        """
        WITH last_user AS (
            SELECT COALESCE(MAX(created_at), 'epoch'::timestamp) AS ts
            FROM messages
            WHERE conversation_id = $1 AND role = 'user'
        )
        SELECT COUNT(*) AS n
        FROM messages, last_user
        WHERE conversation_id = $1
          AND is_nudge = TRUE
          AND created_at > last_user.ts
        """,
        conversation_id,
    )
    return int(row["n"]) if row else 0


async def recent_proactive_texts(
    pool, conversation_id: str, limit: int = 3
) -> list[str]:
    """Fetch the last N proactive messages' text — passed to Gemini as
    "don't repeat these" context when generating the next one."""
    rows = await pool.fetch(
        """
        SELECT content
        FROM messages
        WHERE conversation_id = $1 AND is_proactive = TRUE
        ORDER BY created_at DESC
        LIMIT $2
        """,
        conversation_id,
        limit,
    )
    return [r["content"] for r in rows if r["content"]]


async def get_by_id(pool, message_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, conversation_id, role, sender_id, content, message_type,
               media_urls, audio_url, audio_duration_seconds, token_count,
               client_message_id, created_at, metadata, status, is_read,
               collage_id, collage_bot_id, collage_date
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
               client_message_id, created_at, metadata, status, is_read,
               collage_id, collage_bot_id, collage_date
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
               client_message_id, created_at, metadata, status, is_read,
               collage_id, collage_bot_id, collage_date
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
               client_message_id, created_at, metadata, status, is_read,
               collage_id, collage_bot_id, collage_date
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


async def list_recent_for_spicy_context(
    pool, conversation_id: str, limit: int
) -> list[dict]:
    """Track 2b — return the last `limit` (role, content) rows for the
    conversation, oldest-first, filtered to real text messages the web
    surface can seed context from.

    Filter: role IN ('user','assistant') AND content IS NOT NULL AND
    content <> '' — strips system rows + empty/media-only rows that
    would be useless context. Content-level SFW tightening lands with
    track 2c; for pre-2c messages this is a spicy-going-to-spicy read
    within the same user↔bot relationship (Level 2 concern is WRITE
    isolation, not READ), so no user-facing risk.

    Oldest-first matches typical chat-history ordering — amorae feeds
    the list straight into its own context builder without reversing.
    """
    # SELECT with limit N off the newest end, then reverse for oldest-
    # first. Same shape as get_recent_for_context but with the
    # role/content filter baked in so amorae never sees rows it can't
    # use.
    rows = await pool.fetch(
        """
        SELECT role, content
        FROM messages
        WHERE conversation_id = $1
          AND role IN ('user', 'assistant')
          AND content IS NOT NULL
          AND content <> ''
        ORDER BY created_at DESC
        LIMIT $2
        """,
        conversation_id,
        limit,
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def get_recent_for_context(
    pool, conversation_id: str, limit: int = 11
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, conversation_id, role, sender_id, content, message_type,
               media_urls, audio_url, audio_duration_seconds, token_count,
               client_message_id, created_at, metadata, status, is_read,
               collage_id, collage_bot_id, collage_date
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
                   collage_id, collage_bot_id, collage_date,
                   ROW_NUMBER() OVER (
                       PARTITION BY conversation_id ORDER BY created_at DESC
                   ) as rn
            FROM messages WHERE conversation_id = ANY($1)
        )
        SELECT id, conversation_id, role, sender_id, content, message_type,
               media_urls, audio_url, audio_duration_seconds, token_count,
               client_message_id, created_at, metadata, status, is_read,
               collage_id, collage_bot_id, collage_date
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


async def count_unread(pool, conversation_id: str, viewer_principal: str) -> int:
    # "Unread" = messages the viewer didn't send. Works for both AI (bot's
    # sender_id != user_id) and H2H (peer's sender_id != viewer). The old
    # role='assistant' filter silently returned 0 for H2H since both peers
    # send role='user'. PR #228 trailing-edge bug; see memory file
    # feedback_list_vs_detail_endpoint_gap.md.
    return await pool.fetchval(
        """
        SELECT COUNT(*) FROM messages
        WHERE conversation_id = $1 AND is_read = FALSE AND sender_id != $2
        """,
        conversation_id,
        viewer_principal,
    )


async def mark_as_read(pool, conversation_id: str, viewer_principal: str):
    # Symmetric to count_unread above — mark "messages I didn't send" as read.
    # The old role='assistant' filter no-op'd for H2H, so recipients calling
    # POST /conversations/{id}/read never cleared their unread badge.
    await pool.execute(
        """
        UPDATE messages
        SET is_read = TRUE, status = 'read'
        WHERE conversation_id = $1 AND is_read = FALSE AND sender_id != $2
        """,
        conversation_id,
        viewer_principal,
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
