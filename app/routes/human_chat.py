import asyncio
import json
import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Query

from database import get_pool
from auth import get_current_user
from repositories import message_repo
from services import websocket_manager, push_notifications

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat/human", tags=["Human Chat"])


def _format_message(msg: dict) -> dict:
    media_urls = msg.get("media_urls")
    if isinstance(media_urls, str):
        try:
            media_urls = json.loads(media_urls)
        except (json.JSONDecodeError, TypeError):
            media_urls = []
    if media_urls == []:
        media_urls = None

    if media_urls:
        from services import storage

        media_urls = [storage.generate_presigned_url(u) for u in media_urls if u]
        if not any(media_urls):
            media_urls = None

    audio_url = msg.get("audio_url")
    if audio_url and not audio_url.startswith("http"):
        from services import storage

        audio_url = storage.generate_presigned_url(audio_url)

    created_at = msg["created_at"]
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()

    return {
        "id": msg["id"],
        "conversation_id": msg.get("conversation_id"),
        "role": msg["role"],
        # Mobile needs sender_id to render "is this message mine?" bubble
        # alignment in H2H — role is always 'user' for both participants
        # so sender_id is the only disambiguator.
        "sender_id": msg.get("sender_id"),
        "content": msg.get("content"),
        "message_type": msg["message_type"],
        "media_urls": media_urls,
        "audio_url": audio_url,
        "audio_duration_seconds": msg.get("audio_duration_seconds"),
        "token_count": None,
        "created_at": created_at,
    }


@router.post("/conversations", status_code=201)
async def create_human_conversation(request: Request):
    user_id = get_current_user(request)
    pool = await get_pool()

    body = await request.json()
    participant_id = body.get("participant_id")
    if not participant_id:
        raise HTTPException(status_code=422, detail="participant_id is required")
    if participant_id == user_id:
        raise HTTPException(
            status_code=422, detail="Cannot create conversation with yourself"
        )

    existing = await pool.fetchrow(
        """
        SELECT id, user_id, influencer_id, conversation_type, participant_b_id,
               created_at, updated_at, metadata
        FROM conversations
        WHERE conversation_type = 'human_chat'
          AND ((user_id = $1 AND participant_b_id = $2)
               OR (user_id = $2 AND participant_b_id = $1))
        """,
        user_id,
        participant_id,
    )

    if existing:
        msg_count = await message_repo.count_by_conversation(pool, existing["id"])
        return {
            "id": existing["id"],
            "user_id": existing["user_id"],
            "conversation_type": "human_chat",
            "participant_b_id": existing["participant_b_id"],
            "created_at": existing["created_at"].isoformat()
            if isinstance(existing["created_at"], datetime)
            else str(existing["created_at"]),
            "updated_at": existing["updated_at"].isoformat()
            if isinstance(existing["updated_at"], datetime)
            else str(existing["updated_at"]),
            "message_count": msg_count,
        }

    conversation_id = str(uuid.uuid4())
    await pool.execute(
        """
        INSERT INTO conversations (id, user_id, conversation_type, participant_b_id)
        VALUES ($1, $2, 'human_chat', $3)
        """,
        conversation_id,
        user_id,
        participant_id,
    )

    return {
        "id": conversation_id,
        "user_id": user_id,
        "conversation_type": "human_chat",
        "participant_b_id": participant_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "message_count": 0,
    }


@router.get("/conversations")
async def list_human_conversations(
    request: Request,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
):
    user_id = get_current_user(request)
    pool = await get_pool()

    rows = await pool.fetch(
        """
        SELECT c.id, c.user_id, c.participant_b_id, c.conversation_type,
               c.created_at, c.updated_at, c.metadata,
               COUNT(m.id) as message_count,
               (SELECT COUNT(*) FROM messages m2
                WHERE m2.conversation_id = c.id
                AND m2.is_read = FALSE AND m2.sender_id != $1) as unread_count
        FROM conversations c
        LEFT JOIN messages m ON c.id = m.conversation_id
        WHERE c.conversation_type = 'human_chat'
          AND (c.user_id = $1 OR c.participant_b_id = $1)
        GROUP BY c.id
        ORDER BY c.updated_at DESC
        LIMIT $2 OFFSET $3
        """,
        user_id,
        limit,
        offset,
    )

    total = await pool.fetchval(
        """
        SELECT COUNT(*) FROM conversations
        WHERE conversation_type = 'human_chat'
          AND (user_id = $1 OR participant_b_id = $1)
        """,
        user_id,
    )

    conversations = []
    for r in rows:
        peer_id = r["participant_b_id"] if r["user_id"] == user_id else r["user_id"]
        created_at = (
            r["created_at"].isoformat()
            if isinstance(r["created_at"], datetime)
            else str(r["created_at"])
        )
        updated_at = (
            r["updated_at"].isoformat()
            if isinstance(r["updated_at"], datetime)
            else str(r["updated_at"])
        )

        conversations.append(
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "conversation_type": "human_chat",
                "participant_b_id": r["participant_b_id"],
                "peer_id": peer_id,
                "created_at": created_at,
                "updated_at": updated_at,
                "message_count": r["message_count"],
                "unread_count": r.get("unread_count", 0),
            }
        )

    return {
        "conversations": conversations,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/conversations/{conversation_id}/messages")
async def send_human_message(conversation_id: str, request: Request):
    user_id = get_current_user(request)
    pool = await get_pool()

    body = await request.json()
    content = body.get("content")
    message_type = body.get("message_type", "text")
    media_urls = body.get("media_urls")
    audio_url = body.get("audio_url")
    audio_duration_seconds = body.get("audio_duration_seconds")
    client_message_id = body.get("client_message_id")

    conv = await pool.fetchrow(
        "SELECT id, user_id, participant_b_id, conversation_type FROM conversations WHERE id = $1",
        conversation_id,
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv["conversation_type"] != "human_chat":
        raise HTTPException(status_code=400, detail="Not a human chat conversation")
    if conv["user_id"] != user_id and conv["participant_b_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    recipient_id = (
        conv["participant_b_id"] if conv["user_id"] == user_id else conv["user_id"]
    )

    if client_message_id:
        existing = await message_repo.get_by_client_id(
            pool, conversation_id, client_message_id
        )
        if existing:
            return {
                "user_message": _format_message(existing),
                "assistant_message": None,
            }

    user_msg = await message_repo.create(
        pool,
        conversation_id=conversation_id,
        role="user",
        content=content,
        message_type=message_type,
        media_urls=media_urls,
        audio_url=audio_url,
        audio_duration_seconds=audio_duration_seconds,
        client_message_id=client_message_id,
        sender_id=user_id,
    )

    formatted_msg = _format_message(user_msg)

    asyncio.create_task(
        websocket_manager.broadcast_new_message(
            user_id=recipient_id,
            conversation_id=conversation_id,
            message=formatted_msg,
            influencer={
                "id": user_id,
                "display_name": user_id[:8] + "...",
                "avatar_url": None,
                "is_online": True,
            },
            unread_count=await message_repo.count_unread(pool, conversation_id),
        )
    )

    asyncio.create_task(
        push_notifications.send_new_message_notification(
            user_id=recipient_id,
            influencer_name="Someone",
            message_content=content or "[Media message]",
            conversation_id=conversation_id,
            influencer_id=user_id,
        )
    )

    return {"user_message": formatted_msg, "assistant_message": None}
