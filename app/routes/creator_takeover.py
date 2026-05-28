"""Chat as Human: creator takes over a conversation and replies directly.

When takeover is active:
  - User messages are saved but DO NOT trigger an AI call
  - Creator can post messages that look identical to bot messages on the user side
  - Auto-releases after 2 minutes of user inactivity (background task in main.py)

The hot-path send-message check is in routes/chat.py — see the early-exit branch.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from auth import get_current_user
from database import get_pool
from repositories import conversation_repo, message_repo, takeover_repo
from services import websocket_manager
from services.takeover_helpers import (
    format_msg_for_response,
    remaining_seconds,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/creator", tags=["Creator Takeover"])


async def _load_and_authorize(
    request: Request, conversation_id: str
) -> tuple[dict, str]:
    """Returns (conversation, authenticated_creator_user_id). Raises 403 if not the owner."""
    user_id = get_current_user(request)
    pool = await get_pool()
    conv = await conversation_repo.get_by_id(pool, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    parent = conv.get("inf_parent_principal_id")
    if not parent or parent != user_id:
        raise HTTPException(
            status_code=403,
            detail="Only the AI influencer's creator can manage takeover",
        )
    return conv, user_id


@router.post("/conversations/{conversation_id}/human-creator-takeover")
async def takeover_start(conversation_id: str, request: Request):
    conv, creator_user_id = await _load_and_authorize(request, conversation_id)
    if conv.get("human_creator_takeover_active"):
        raise HTTPException(status_code=409, detail="Takeover already active")

    pool = await get_pool()
    state = await takeover_repo.activate(pool, conversation_id, creator_user_id)

    creator_display = conv.get("inf_display_name") or "Creator"
    bot_display = conv.get("inf_display_name") or "your AI"
    join_text = (
        f"📣 {creator_display}, the human creator behind {bot_display}, "
        "has joined the chat. You're now talking to them directly."
    )
    await message_repo.create(
        pool,
        conversation_id=conversation_id,
        role="system",
        content=join_text,
        message_type="text",
        sender_id=creator_user_id,
    )

    await websocket_manager.broadcast_event(
        conv["user_id"],
        "human_creator_takeover_started",
        {
            "conversation_id": conversation_id,
            "bot_id": conv.get("influencer_id"),
            "human_creator_display_name": creator_display,
        },
    )

    started_at = state.get("human_creator_takeover_started_at")
    user_last = state.get("user_last_message_at")
    return {
        "status": "active",
        "started_at": started_at.isoformat()
        if isinstance(started_at, datetime)
        else None,
        "user_last_message_at": user_last.isoformat()
        if isinstance(user_last, datetime)
        else None,
        "remaining_seconds": remaining_seconds(user_last),
    }


@router.post("/conversations/{conversation_id}/human-creator-release")
async def takeover_release(conversation_id: str, request: Request):
    conv, creator_user_id = await _load_and_authorize(request, conversation_id)
    if not conv.get("human_creator_takeover_active"):
        raise HTTPException(status_code=400, detail="Takeover is not active")

    pool = await get_pool()
    await takeover_repo.deactivate(pool, conversation_id)

    creator_display = conv.get("inf_display_name") or "Creator"
    leave_text = f"{creator_display} has left the chat."
    await message_repo.create(
        pool,
        conversation_id=conversation_id,
        role="system",
        content=leave_text,
        message_type="text",
        sender_id=creator_user_id,
    )

    await websocket_manager.broadcast_event(
        conv["user_id"],
        "human_creator_takeover_ended",
        {"conversation_id": conversation_id},
    )

    return {"status": "released"}


@router.post("/conversations/{conversation_id}/human-creator-messages")
async def takeover_send_message(conversation_id: str, body: dict, request: Request):
    conv, creator_user_id = await _load_and_authorize(request, conversation_id)
    if not conv.get("human_creator_takeover_active"):
        raise HTTPException(status_code=400, detail="Takeover is not active")

    content = (body or {}).get("content")
    if not content or not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content is required")

    pool = await get_pool()

    # Save as role='assistant' so AI history-fetch picks it up naturally on resume
    msg = await message_repo.create(
        pool,
        conversation_id=conversation_id,
        role="assistant",
        content=content,
        message_type="text",
        sender_id=conv.get("influencer_id"),
    )

    # Stamp it as a takeover message (separate UPDATE — non-blocking, doesn't affect hot path)
    await pool.execute(
        """
        UPDATE messages
        SET is_human_creator_takeover = TRUE,
            human_creator_user_id = $1
        WHERE id = $2
        """,
        creator_user_id,
        msg["id"],
    )

    # Bump conversation updated_at
    await pool.execute(
        "UPDATE conversations SET updated_at = NOW() WHERE id = $1",
        conversation_id,
    )

    # Broadcast to user — looks identical to a bot message on the user side
    formatted = format_msg_for_response(msg)
    formatted["content"] = content  # ensure content is the latest

    unread_count = await message_repo.count_unread(pool, conversation_id)
    await websocket_manager.broadcast_new_message(
        user_id=conv["user_id"],
        conversation_id=conversation_id,
        message=formatted,
        influencer={
            "id": conv.get("influencer_id"),
            "display_name": conv.get("inf_display_name", ""),
            "avatar_url": conv.get("inf_avatar_url"),
            "is_online": True,
        },
        unread_count=unread_count,
    )

    return formatted


@router.get("/conversations/{conversation_id}/human-creator-takeover-status")
async def takeover_status(conversation_id: str, request: Request):
    conv, _ = await _load_and_authorize(request, conversation_id)
    active = bool(conv.get("human_creator_takeover_active"))
    started_at = conv.get("human_creator_takeover_started_at")
    user_last = conv.get("user_last_message_at")
    return {
        "active": active,
        "started_at": started_at.isoformat()
        if isinstance(started_at, datetime)
        else None,
        "user_last_message_at": user_last.isoformat()
        if isinstance(user_last, datetime)
        else None,
        "remaining_seconds": remaining_seconds(user_last) if active else 0,
    }


@router.get("/conversations/{conversation_id}/messages")
async def creator_list_messages(
    conversation_id: str,
    request: Request,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc",
):
    """Owner-only mirror of user-side message list.

    Polled every 2-3 seconds by the creator's mobile during takeover.
    Returns same shape as GET /api/v1/chat/conversations/{id}/messages.
    Uses idx_messages_conversation_created for sub-100ms response.
    """
    await _load_and_authorize(request, conversation_id)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    pool = await get_pool()
    messages = await message_repo.list_by_conversation(
        pool, conversation_id, limit, offset, order
    )
    total = await message_repo.count_by_conversation(pool, conversation_id)

    # Reuse the same _format_message shape as user-side chat route.
    from routes.chat import _format_message

    return {
        "conversation_id": conversation_id,
        "messages": [_format_message(m) for m in messages],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
