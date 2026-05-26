import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Query

from database import get_pool
from auth import get_current_user
from repositories import influencer_repo, conversation_repo, message_repo
import httpx

from services import (
    ai_client,
    push_notifications,
    soul_file,
    websocket_manager,
    storage,
    replicate,
)
from models import SendMessageResponse, ChatMessage

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


def _format_conversation(
    conv: dict,
    message_count: int = 0,
    last_message: dict | None = None,
    recent_messages: list[dict] | None = None,
    show_suggestions: bool = False,
) -> dict:
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
            existing,
            message_count=msg_count,
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
        conv,
        message_count=msg_count,
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
        pool,
        user_id,
        influencer_id,
        limit,
        offset,
    )
    total = await conversation_repo.count_by_user(pool, user_id, influencer_id)

    if not conversations:
        return {"conversations": [], "total": total, "limit": limit, "offset": offset}

    conv_ids = [c["id"] for c in conversations]
    last_messages = await conversation_repo.get_last_messages_batch(pool, conv_ids)
    recent_messages = await message_repo.get_recent_for_conversations_batch(
        pool, conv_ids, 10
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
        formatted.append(
            _format_conversation(
                c,
                message_count=msg_count,
                last_message=last_msg,
                recent_messages=recent,
                show_suggestions=True,
            )
        )

    return {
        "conversations": formatted,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


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
        pool,
        conversation_id,
        limit,
        offset,
        order,
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
        raise HTTPException(
            status_code=403, detail="Only the conversation creator can delete it"
        )

    msg_count = await message_repo.delete_by_conversation(pool, conversation_id)
    await conversation_repo.delete(pool, conversation_id)

    return {
        "success": True,
        "message": "Conversation deleted successfully",
        "deleted_conversation_id": conversation_id,
        "deleted_messages_count": msg_count,
    }


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: dict,
    request: Request,
):
    user_id = get_current_user(request)
    pool = await get_pool()

    conv = await conversation_repo.get_by_id(pool, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not await _can_access_conversation(pool, user_id, conv):
        raise HTTPException(status_code=403, detail="Access denied")

    influencer_id = conv.get("influencer_id")
    if not influencer_id:
        raise HTTPException(status_code=400, detail="Not an AI chat conversation")

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")

    # Deduplication
    client_message_id = body.get("client_message_id")
    if client_message_id:
        existing = await message_repo.get_by_client_id(
            pool, conversation_id, client_message_id
        )
        if existing:
            reply = await message_repo.get_assistant_reply(pool, existing["id"])
            return {
                "user_message": _format_message(existing),
                "assistant_message": _format_message(reply) if reply else None,
            }

    # Audio transcription
    content = body.get("content")
    message_type = body.get("message_type", "text")
    audio_url = body.get("audio_url")
    media_urls = body.get("media_urls")

    if message_type == "audio" and audio_url:
        transcription = await ai_client.transcribe_audio(audio_url)
        if transcription:
            content = f"[Transcribed: {transcription}]"
        else:
            content = "[Audio message - transcription unavailable]"

    # Save user message
    user_msg = await message_repo.create(
        pool,
        conversation_id=conversation_id,
        role="user",
        content=content,
        message_type=message_type,
        media_urls=media_urls,
        audio_url=audio_url,
        audio_duration_seconds=body.get("audio_duration_seconds"),
        client_message_id=client_message_id,
        sender_id=user_id,
    )

    # Fetch conversation history
    history = await message_repo.get_recent_for_context(pool, conversation_id, 11)
    history = [m for m in history if m["id"] != user_msg["id"]]
    history = history[-10:]

    # Enhance system instructions with memories
    metadata = conv.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}
    elif metadata is None:
        metadata = {}

    memories = metadata.get("memories", {})
    if isinstance(memories, str):
        try:
            memories = json.loads(memories)
        except (json.JSONDecodeError, TypeError):
            memories = {}

    system_instructions = soul_file.compose(
        system_instructions=inf.get("system_instructions", ""),
        category=inf.get("category"),
        memories=memories,
    )

    # Typing indicator START
    await websocket_manager.broadcast_typing_status(
        user_id=user_id,
        conversation_id=conversation_id,
        influencer_id=influencer_id,
        is_typing=True,
    )

    # Call AI model
    is_nsfw = inf.get("is_nsfw", False)
    llm_result = await ai_client.generate_response(
        system_instructions=system_instructions,
        conversation_history=history,
        user_message=content or "",
        is_nsfw=is_nsfw,
        media_urls=media_urls,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    # Typing indicator STOP
    await websocket_manager.broadcast_typing_status(
        user_id=user_id,
        conversation_id=conversation_id,
        influencer_id=influencer_id,
        is_typing=False,
    )

    # Save AI response
    assistant_msg = await message_repo.create(
        pool,
        conversation_id=conversation_id,
        role="assistant",
        content=llm_result.content,
        message_type="text",
        token_count=llm_result.output_tokens,
        sender_id=influencer_id,
    )

    # Background tasks: memory extraction + push notification + WS broadcast
    asyncio.create_task(
        _background_memory_extraction(
            pool,
            conversation_id,
            content or "",
            llm_result.content,
            memories,
            is_nsfw,
        )
    )

    unread_count = await message_repo.count_unread(pool, conversation_id)
    asyncio.create_task(
        websocket_manager.broadcast_new_message(
            user_id=user_id,
            conversation_id=conversation_id,
            message=_format_message(assistant_msg),
            influencer={
                "id": influencer_id,
                "display_name": inf.get("display_name", ""),
                "avatar_url": inf.get("avatar_url"),
                "is_online": True,
            },
            unread_count=unread_count,
        )
    )

    asyncio.create_task(
        push_notifications.send_new_message_notification(
            user_id=user_id,
            influencer_name=inf.get("display_name", "AI"),
            message_content=llm_result.content,
            conversation_id=conversation_id,
            influencer_id=influencer_id,
        )
    )

    return SendMessageResponse(
        user_message=ChatMessage(**_format_message(user_msg)),
        assistant_message=ChatMessage(**_format_message(assistant_msg)),
    )


async def _background_memory_extraction(
    pool,
    conversation_id: str,
    user_message: str,
    assistant_response: str,
    existing_memories: dict,
    is_nsfw: bool,
):
    try:
        updated_memories = await ai_client.extract_memories(
            user_message,
            assistant_response,
            existing_memories,
            is_nsfw,
        )
        if updated_memories != existing_memories:
            await conversation_repo.update_metadata(
                pool,
                conversation_id,
                {"memories": updated_memories},
            )
    except Exception as e:
        logger.warning(f"Memory extraction failed (non-fatal): {e}")


async def _generate_image_prompt_from_context(pool, conversation_id: str) -> str:
    messages = await message_repo.list_by_conversation(
        pool, conversation_id, limit=10, offset=0, order="desc"
    )
    messages.reverse()
    context_lines = [
        f"{m['role']}: {m['content']}" for m in messages if m.get("content")
    ]
    context_str = "\n".join(context_lines)

    system = (
        "You are an AI assistant helping to visualize a scene. Based on "
        "the recent conversation, generate a detailed image generation "
        "prompt that captures the current context, action, or requested "
        "visual. Output ONLY the prompt, no other text."
    )
    user = f"Conversation Context:\n{context_str}\n\nGenerate an image prompt:"

    result = await ai_client.generate_response(
        system_instructions=system,
        conversation_history=[],
        user_message=user,
        is_nsfw=False,
        media_urls=None,
    )
    return result.content.strip()


@router.post("/conversations/{conversation_id}/images", status_code=201)
async def generate_conversation_image(
    conversation_id: str,
    body: dict,
    request: Request,
):
    import config

    user_id = get_current_user(request)
    pool = await get_pool()

    if not config.REPLICATE_API_TOKEN:
        raise HTTPException(
            status_code=503, detail="Image generation service not available"
        )

    conv = await conversation_repo.get_by_id(pool, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not await _can_access_conversation(pool, user_id, conv):
        raise HTTPException(status_code=403, detail="Not your conversation")

    influencer_id = conv.get("influencer_id")
    if not influencer_id:
        raise HTTPException(status_code=404, detail="Influencer not found")
    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if inf.get("is_active") == "discontinued":
        raise HTTPException(
            status_code=403,
            detail="This bot has been deleted and can no longer generate images.",
        )

    final_prompt = (body.get("prompt") or "").strip()
    if not final_prompt:
        final_prompt = await _generate_image_prompt_from_context(pool, conversation_id)

    avatar_raw = (inf.get("avatar_url") or "").strip()
    input_image_url: str | None = None
    if avatar_raw:
        if avatar_raw.startswith("http"):
            input_image_url = avatar_raw
        else:
            input_image_url = storage.generate_presigned_url(avatar_raw) or None

    if input_image_url:
        image_url = await replicate.generate_image_with_reference(
            final_prompt, input_image_url, aspect_ratio="9:16"
        )
    else:
        image_url = await replicate.generate_image(final_prompt, aspect_ratio="9:16")

    if not image_url:
        raise HTTPException(
            status_code=503, detail="Failed to generate image from upstream provider"
        )

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
            resp = await http.get(image_url)
            resp.raise_for_status()
    except Exception:
        raise HTTPException(status_code=503, detail="Failed to fetch generated image")

    image_bytes = resp.content
    if not image_bytes:
        raise HTTPException(status_code=503, detail="Generated image was empty")
    content_type = (
        (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
    )
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"

    s3_key, _ = await storage.upload(
        user_id=user_id,
        file_bytes=image_bytes,
        file_extension=".jpg",
        content_type=content_type,
    )

    msg = await message_repo.create(
        pool,
        conversation_id=conversation_id,
        role="assistant",
        content="",
        message_type="image",
        media_urls=[s3_key],
        sender_id=influencer_id,
        token_count=0,
    )
    return _format_message(msg)
