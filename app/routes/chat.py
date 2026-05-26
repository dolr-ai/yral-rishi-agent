import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Query

from database import get_pool
from auth import get_current_user
from repositories import influencer_repo, conversation_repo, message_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["Chat v1 — AI"])


def _format_message(msg: dict) -> dict:
    media_urls = msg.get("media_urls")
    if isinstance(media_urls, str):
        try:
            media_urls = json.loads(media_urls)
        except (json.JSONDecodeError, TypeError):
            media_urls = []
    if media_urls == []:
        media_urls = None

    audio_url = msg.get("audio_url")

    created_at = msg["created_at"]
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()

    return {
        "id": msg["id"],
        "conversation_id": msg.get("conversation_id"),
        "role": msg["role"],
        "content": msg.get("content"),
        "message_type": msg["message_type"],
        "media_urls": media_urls,
        "audio_url": audio_url,
        "audio_duration_seconds": msg.get("audio_duration_seconds"),
        "token_count": msg.get("token_count"),
        "created_at": created_at,
    }


def _format_conversation(conv: dict, message_count: int = 0,
                         last_message: dict | None = None,
                         recent_messages: list[dict] | None = None,
                         show_suggestions: bool = False) -> dict:
    suggested = conv.get("inf_suggested_messages")
    if isinstance(suggested, str):
        try:
            suggested = json.loads(suggested)
        except (json.JSONDecodeError, TypeError):
            suggested = None

    if not show_suggestions or message_count > 1:
        suggested = None

    created_at = conv["created_at"]
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()

    updated_at = conv["updated_at"]
    if isinstance(updated_at, datetime):
        updated_at = updated_at.isoformat()

    influencer = {
        "id": conv.get("inf_id") or conv.get("influencer_id") or "",
        "name": conv.get("inf_name") or "",
        "display_name": conv.get("inf_display_name") or "",
        "avatar_url": conv.get("inf_avatar_url") or "",
        "category": conv.get("inf_category"),
        "suggested_messages": suggested,
    }

    return {
        "id": conv["id"],
        "user_id": conv["user_id"],
        "influencer": influencer,
        "created_at": created_at,
        "updated_at": updated_at,
        "message_count": message_count,
        "last_message": last_message,
        "recent_messages": recent_messages,
    }


async def _can_access_conversation(pool, user_id: str, conv: dict) -> bool:
    if conv["user_id"] == user_id:
        return True
    if conv.get("influencer_id") == user_id:
        return True
    if conv.get("influencer_id"):
        parent = await influencer_repo.get_parent_principal(pool, conv["influencer_id"])
        if parent == user_id:
            return True
    return False


@router.post("/conversations", status_code=201)
async def create_conversation(body: dict, request: Request):
    user_id = get_current_user(request)
    pool = await get_pool()

    influencer_id = body.get("influencer_id")
    if not influencer_id:
        raise HTTPException(status_code=422, detail="influencer_id is required")

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")

    existing = await conversation_repo.get_existing(pool, user_id, influencer_id)
    if existing:
        msg_count = await message_repo.count_by_conversation(pool, existing["id"])
        recent = await message_repo.get_recent_for_context(pool, existing["id"], 10)
        formatted_recent = [_format_message(m) for m in recent] if recent else None
        return _format_conversation(
            existing, message_count=msg_count,
            recent_messages=formatted_recent,
            show_suggestions=True,
        )

    conv = await conversation_repo.create(pool, user_id, influencer_id)

    if inf.get("initial_greeting"):
        await message_repo.create(
            pool,
            conversation_id=conv["id"],
            role="assistant",
            content=inf["initial_greeting"],
            message_type="text",
            sender_id=influencer_id,
        )

    conv = await conversation_repo.get_by_id(pool, conv["id"])
    msg_count = await message_repo.count_by_conversation(pool, conv["id"])
    recent = await message_repo.get_recent_for_context(pool, conv["id"], 10)
    formatted_recent = [_format_message(m) for m in recent] if recent else None

    return _format_conversation(
        conv, message_count=msg_count,
        recent_messages=formatted_recent,
        show_suggestions=True,
    )


@router.get("/conversations")
async def list_conversations(
    request: Request,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    influencer_id: str | None = Query(default=None),
):
    user_id = get_current_user(request)
    pool = await get_pool()

    conversations = await conversation_repo.list_by_user(
        pool, user_id, influencer_id, limit, offset,
    )
    total = await conversation_repo.count_by_user(pool, user_id, influencer_id)

    if not conversations:
        return {"conversations": [], "total": total, "limit": limit, "offset": offset}

    conv_ids = [c["id"] for c in conversations]
    last_messages = await conversation_repo.get_last_messages_batch(pool, conv_ids)
    recent_messages = await message_repo.get_recent_for_conversations_batch(pool, conv_ids, 10)

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

    recent_map: dict[str, list] = {}
    for rm in recent_messages:
        cid = rm["conversation_id"]
        if cid not in recent_map:
            recent_map[cid] = []
        recent_map[cid].append(_format_message(rm))

    formatted = []
    for c in conversations:
        msg_count = c.get("message_count", 0)
        last_msg = last_msg_map.get(c["id"])
        recent = recent_map.get(c["id"])
        formatted.append(_format_conversation(
            c, message_count=msg_count,
            last_message=last_msg,
            recent_messages=recent,
            show_suggestions=True,
        ))

    return {"conversations": formatted, "total": total, "limit": limit, "offset": offset}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    request: Request,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    order: str = Query(default="desc"),
):
    user_id = get_current_user(request)
    pool = await get_pool()

    conv = await conversation_repo.get_by_id(pool, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not await _can_access_conversation(pool, user_id, conv):
        raise HTTPException(status_code=403, detail="Access denied")

    messages = await message_repo.list_by_conversation(
        pool, conversation_id, limit, offset, order,
    )
    total = await message_repo.count_by_conversation(pool, conversation_id)

    return {
        "conversation_id": conversation_id,
        "messages": [_format_message(m) for m in messages],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/conversations/{conversation_id}/read")
async def mark_as_read(conversation_id: str, request: Request):
    user_id = get_current_user(request)
    pool = await get_pool()

    conv = await conversation_repo.get_by_id(pool, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not await _can_access_conversation(pool, user_id, conv):
        raise HTTPException(status_code=403, detail="Access denied")

    await message_repo.mark_as_read(pool, conversation_id)
    unread = await message_repo.count_unread(pool, conversation_id)
    return {"unread_count": unread}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request):
    user_id = get_current_user(request)
    pool = await get_pool()

    conv = await conversation_repo.get_by_id(pool, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Only the conversation creator can delete it")

    msg_count = await message_repo.delete_by_conversation(pool, conversation_id)
    await conversation_repo.delete(pool, conversation_id)

    return {
        "success": True,
        "message": "Conversation deleted successfully",
        "deleted_conversation_id": conversation_id,
        "deleted_messages_count": msg_count,
    }
