import json
import logging
from datetime import datetime

from fastapi import APIRouter, Request, Query

from database import get_pool
from auth import get_current_user
from repositories import conversation_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/chat", tags=["Chat v3 — Unified inbox"])


def _format_dt(dt) -> str:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt) if dt else ""


@router.get("/conversations")
async def list_unified_conversations(
    request: Request,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
):
    user_id = get_current_user(request)
    pool = await get_pool()

    rows = await pool.fetch(
        """
        SELECT c.id, c.user_id, c.influencer_id, c.conversation_type,
               c.participant_b_id, c.created_at, c.updated_at,
               i.id as inf_id, i.name as inf_name,
               i.display_name as inf_display_name,
               i.avatar_url as inf_avatar_url,
               i.category as inf_category,
               i.suggested_messages as inf_suggested_messages,
               COUNT(m.id) as message_count,
               (SELECT COUNT(*) FROM messages m2
                WHERE m2.conversation_id = c.id
                AND m2.is_read = FALSE
                AND m2.sender_id != $1) as unread_count
        FROM conversations c
        LEFT JOIN ai_influencers i ON c.influencer_id = i.id
        LEFT JOIN messages m ON c.id = m.conversation_id
        WHERE (
            (c.conversation_type = 'ai_chat' AND c.user_id = $1
             AND (i.is_active IS NULL OR i.is_active != 'discontinued'))
            OR
            (c.conversation_type = 'human_chat'
             AND (c.user_id = $1 OR c.participant_b_id = $1))
        )
        GROUP BY c.id, i.id
        ORDER BY c.updated_at DESC
        LIMIT $2 OFFSET $3
        """,
        user_id,
        limit,
        offset,
    )

    total = await pool.fetchval(
        """
        SELECT COUNT(*) FROM conversations c
        LEFT JOIN ai_influencers i ON c.influencer_id = i.id
        WHERE (
            (c.conversation_type = 'ai_chat' AND c.user_id = $1
             AND (i.is_active IS NULL OR i.is_active != 'discontinued'))
            OR
            (c.conversation_type = 'human_chat'
             AND (c.user_id = $1 OR c.participant_b_id = $1))
        )
        """,
        user_id,
    )

    conv_ids = [r["id"] for r in rows]
    last_messages = (
        await conversation_repo.get_last_messages_batch(pool, conv_ids)
        if conv_ids
        else []
    )

    last_msg_map = {}
    for lm in last_messages:
        lm_created = lm["created_at"]
        if isinstance(lm_created, datetime):
            lm_created = lm_created.isoformat()
        last_msg_map[lm["conversation_id"]] = {
            "content": lm.get("content") or "",
            "role": lm["role"],
            "created_at": lm_created,
        }

    conversations = []
    for r in rows:
        conv_type = r["conversation_type"]
        base = {
            "id": r["id"],
            "user_id": r["user_id"],
            "conversation_type": conv_type,
            "created_at": _format_dt(r["created_at"]),
            "updated_at": _format_dt(r["updated_at"]),
            "message_count": r["message_count"],
            "unread_count": r.get("unread_count", 0),
            "last_message": last_msg_map.get(r["id"]),
        }

        if conv_type == "ai_chat":
            suggested = r.get("inf_suggested_messages")
            if isinstance(suggested, str):
                try:
                    suggested = json.loads(suggested)
                except (json.JSONDecodeError, TypeError):
                    suggested = None
            if r["message_count"] > 1:
                suggested = None

            base["influencer"] = {
                "id": r.get("inf_id") or r.get("influencer_id") or "",
                "name": r.get("inf_name") or "",
                "display_name": r.get("inf_display_name") or "",
                "avatar_url": r.get("inf_avatar_url") or "",
                "category": r.get("inf_category"),
                "suggested_messages": suggested,
            }
            base["peer_user"] = None
        elif conv_type == "human_chat":
            peer_id = r["participant_b_id"] if r["user_id"] == user_id else r["user_id"]
            base["influencer"] = None
            base["peer_user"] = {
                "id": peer_id,
                "display_name": None,
                "avatar_url": None,
            }

        conversations.append(base)

    return {
        "conversations": conversations,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
