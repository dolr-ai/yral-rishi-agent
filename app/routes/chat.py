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
    content_safety,
    embeddings,
    memory,
    push_notifications,
    session_memory,
    soul_file,
    websocket_manager,
    storage,
    replicate,
)
from models import SendMessageResponse, ChatMessage, AssistantError

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

    # Presign S3 keys → HTTP URLs so mobile can display images
    if media_urls:
        media_urls = [storage.generate_presigned_url(u) for u in media_urls if u]
        if not any(media_urls):
            media_urls = None

    audio_url = msg.get("audio_url")
    if audio_url and not audio_url.startswith("http"):
        audio_url = storage.generate_presigned_url(audio_url)

    created_at = msg["created_at"]
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()

    return {
        "id": msg["id"],
        "conversation_id": msg.get("conversation_id"),
        "role": msg["role"],
        # See human_chat._format_message — mobile uses sender_id for
        # bubble alignment. Symmetric across AI + H2H wire formats.
        "sender_id": msg.get("sender_id"),
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
        # Phase 2.7 follow-up: mobile reads this to decide whether to use the
        # streaming endpoint (NSFW = no, fall back to legacy non-streaming).
        "is_nsfw": bool(conv.get("inf_is_nsfw", False)),
    }

    # Phase 5.6: streak fields. The daily background job in
    # services.streak_tracker updates these; mobile reads them to render a
    # streak badge. Defaults (0/0/None) work fine for conversations on rows
    # written before migration 014.
    last_streak_date = conv.get("last_streak_date")
    if hasattr(last_streak_date, "isoformat"):
        last_streak_date = last_streak_date.isoformat()

    return {
        "id": conv["id"],
        "user_id": conv["user_id"],
        "influencer": influencer,
        "created_at": created_at,
        "updated_at": updated_at,
        "message_count": message_count,
        "last_message": last_message,
        "recent_messages": recent_messages,
        "current_streak_days": conv.get("current_streak_days") or 0,
        "longest_streak_days": conv.get("longest_streak_days") or 0,
        "last_streak_date": last_streak_date,
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


# ─── Task D / Phase 5.4: user-configurable proactive frequency ────────────

PROACTIVE_FREQUENCIES = {"default", "daily", "weekly", "off"}


@router.patch("/conversations/{conversation_id}/proactive-frequency")
async def set_proactive_frequency(conversation_id: str, body: dict, request: Request):
    """User opts each (user, bot) conversation into 'default' / 'daily' /
    'weekly' / 'off' for proactive bot-initiated messages. Default behavior
    (24h inactivity threshold) is unchanged for rows that never set this."""
    user_id = get_current_user(request)
    pool = await get_pool()

    conv = await conversation_repo.get_by_id(pool, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv["user_id"] != user_id:
        raise HTTPException(
            status_code=403, detail="Only the conversation owner can change this"
        )

    freq = (body or {}).get("frequency")
    if freq not in PROACTIVE_FREQUENCIES:
        raise HTTPException(
            status_code=422,
            detail=f"frequency must be one of {sorted(PROACTIVE_FREQUENCIES)}",
        )

    await pool.execute(
        "UPDATE conversations SET proactive_frequency = $1 WHERE id = $2",
        freq,
        conversation_id,
    )
    return {"conversation_id": conversation_id, "proactive_frequency": freq}


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

    # Chat-as-Human early exit: if creator has taken over, skip the LLM entirely.
    # The takeover state was already fetched in the conversation lookup above (no extra DB hit).
    if conv.get("human_creator_takeover_active"):
        from repositories import takeover_repo

        await takeover_repo.update_user_last_message(pool, conversation_id)
        await websocket_manager.broadcast_event(
            conv.get("inf_parent_principal_id") or "",
            "new_user_message_during_takeover",
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "message": _format_message(user_msg),
            },
        )
        return SendMessageResponse(
            user_message=ChatMessage(**_format_message(user_msg)),
            assistant_message=None,
        )

    # Content safety check — runs before LLM call
    is_nsfw = inf.get("is_nsfw", False)
    safety = content_safety.check_message(content or "", is_nsfw_influencer=is_nsfw)
    if safety.blocked:
        assistant_msg = await message_repo.create(
            pool,
            conversation_id=conversation_id,
            role="assistant",
            content=safety.override_response,
            message_type="text",
            sender_id=influencer_id,
        )
        return SendMessageResponse(
            user_message=ChatMessage(**_format_message(user_msg)),
            assistant_message=ChatMessage(**_format_message(assistant_msg)),
        )

    # Phase 4.7: piggyback session-memory update on the post-save async slot.
    # Fire-and-forget so the hot path doesn't wait on Redis.
    asyncio.create_task(
        session_memory.update_from_user_message(user_id, conversation_id, content or "")
    )

    # Fetch history + compute query embedding in parallel.
    # Phase 4.4: the embedding call (~150ms) dominates the prep budget, so we
    # fan it out alongside the history fetch (~5-10ms) to keep wall-clock close
    # to the slowest single step. Short messages skip the embedding entirely
    # since semantic search on 1-2 word inputs (e.g. "ok") isn't meaningful.
    async def _fetch_history():
        h = await message_repo.get_recent_for_context(pool, conversation_id, 11)
        h = [m for m in h if m["id"] != user_msg["id"]]
        return h[-10:]

    async def _maybe_embed():
        text = (content or "").strip()
        if len(text) < 5:
            return None
        return await embeddings.embed_text(text)

    async def _read_session():
        return await session_memory.read(user_id, conversation_id)

    history, query_embedding, session_state = await asyncio.gather(
        _fetch_history(), _maybe_embed(), _read_session()
    )

    # Compose soul file prompt with tiered memories — semantic top-K if we have
    # an embedding, else all memories (proactive / short-message fallback).
    # conversation_id enables the Phase 4 polish variety filter (recently-used
    # keys in this convo are skipped).
    memories = await memory.get_memories_for_prompt(
        pool,
        user_id,
        influencer_id,
        query_embedding=query_embedding,
        conversation_id=conversation_id,
    )

    # Phase 4.7: inject short-term session signals (mood) alongside long-term
    # memories. Treated as just-another-fact in the soul file's L4.
    if (
        session_state
        and session_state.get("mood")
        and session_state["mood"] != "neutral"
    ):
        memories["session_mood"] = session_state["mood"]

    # Phase 7.6: A/B routing. If variant B exists for this bot, pick A or B
    # 50/50 per turn. Record the choice so the compare endpoint can group
    # samples and the message history can attribute each reply correctly.
    import random as _random
    from repositories import variant_repo as _variant_repo

    _variant_b = await _variant_repo.get_variant_b(pool, influencer_id)
    chosen_instructions = inf.get("system_instructions", "") or ""
    chosen_variant_label: str | None = None
    if _variant_b:
        if _random.random() < 0.5:
            chosen_variant_label = "a"
        else:
            chosen_variant_label = "b"
            chosen_instructions = _variant_b["system_instructions"]

    system_instructions = soul_file.compose(
        system_instructions=chosen_instructions,
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
    llm_result = await ai_client.generate_response(
        system_instructions=system_instructions,
        conversation_history=history,
        user_message=content or "",
        is_nsfw=is_nsfw,
        media_urls=media_urls,
        user_id=user_id,
        conversation_id=conversation_id,
        archetype=inf.get("category"),
    )

    # Typing indicator STOP
    await websocket_manager.broadcast_typing_status(
        user_id=user_id,
        conversation_id=conversation_id,
        influencer_id=influencer_id,
        is_typing=False,
    )

    # Phase 3.8: graceful error UX. On AI failure, don't persist the fallback
    # text as a real assistant message — it would pollute LLM context on retry.
    # Return assistant_message=None with a structured error so mobile can
    # render it inline with the right icon/color/retry affordance.
    if llm_result.error_code:
        return SendMessageResponse(
            user_message=ChatMessage(**_format_message(user_msg)),
            assistant_message=None,
            error=AssistantError(
                code=llm_result.error_code,
                message=llm_result.content,
                retryable=llm_result.error_code in ai_client.RETRYABLE_CODES,
            ),
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
        variant_label=chosen_variant_label,
    )

    # Background tasks: memory extraction + push notification + WS broadcast
    asyncio.create_task(
        memory.extract_and_store(
            pool,
            user_id=user_id,
            influencer_id=influencer_id,
            user_message=content or "",
            assistant_response=llm_result.content,
            message_id=user_msg["id"],
            is_nsfw=is_nsfw,
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


# ─── Phase 2.7 — SSE streaming ────────────────────────────────────────────
# Mirror of POST /conversations/{id}/messages but returns text/event-stream
# with token-by-token output. Same pre-LLM steps (auth, dedup, content
# safety, memory injection, soul file composition). NSFW influencers fall
# back to a single bundled done event by calling the non-streaming
# generate_response — OpenRouter SDK streaming would need its own code path,
# tracked as Infra-Z if mobile asks for streaming on NSFW.


def _sse_event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


@router.post("/conversations/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: str,
    body: dict,
    request: Request,
):
    """Streaming counterpart to POST /messages.

    Wire format (one SSE event per line group, see docs/SSE-PROTOCOL.md):
      event: token  | data: {"text": "Hello"}
      event: done   | data: {"assistant_message": {...}, "provider": "gemini"}
      event: error  | data: {"code": "BLOCKED_CONTENT", "message": ..., "retryable": false}
    """
    import config as cfg
    from fastapi.responses import StreamingResponse

    if not cfg.ENABLE_SSE_STREAMING:
        raise HTTPException(status_code=404, detail="SSE streaming disabled")

    user_id = get_current_user(request)
    pool = await get_pool()

    conv = await conversation_repo.get_by_id(pool, conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not await _can_access_conversation(pool, user_id, conv):
        raise HTTPException(status_code=403, detail="Not your conversation")

    influencer_id = conv.get("influencer_id")
    if not influencer_id:
        raise HTTPException(
            status_code=400, detail="Streaming requires AI conversation"
        )
    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")

    content = body.get("content")
    message_type = body.get("message_type", "text")
    media_urls = body.get("media_urls")

    user_msg = await message_repo.create(
        pool,
        conversation_id=conversation_id,
        role="user",
        content=content,
        message_type=message_type,
        media_urls=media_urls,
        sender_id=user_id,
    )

    # Content safety pre-check — if blocked, stream the override as a single
    # token event then done. Same shape mobile expects.
    is_nsfw = inf.get("is_nsfw", False)
    safety = content_safety.check_message(content or "", is_nsfw_influencer=is_nsfw)

    async def event_stream():
        try:
            if safety.blocked:
                override = safety.override_response
                assistant_msg = await message_repo.create(
                    pool,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=override,
                    message_type="text",
                    sender_id=influencer_id,
                )
                yield _sse_event("token", {"text": override})
                yield _sse_event(
                    "done",
                    {
                        "assistant_message": _format_message(assistant_msg),
                        "provider": "content_safety",
                        "blocked": True,
                    },
                )
                return

            history = await message_repo.get_recent_for_context(
                pool, conversation_id, 11
            )
            history = [m for m in history if m["id"] != user_msg["id"]][-10:]
            memories = await memory.get_memories_for_prompt(
                pool, user_id, influencer_id, conversation_id=conversation_id
            )
            system_instructions = soul_file.compose(
                system_instructions=inf.get("system_instructions", ""),
                category=inf.get("category"),
                memories=memories,
            )

            full_text = ""
            llm_result_obj = None
            async for kind, value in ai_client.generate_response_stream(
                system_instructions=system_instructions,
                conversation_history=history,
                user_message=content or "",
                is_nsfw=is_nsfw,
                media_urls=media_urls,
                user_id=user_id,
                conversation_id=conversation_id,
                archetype=inf.get("category"),
            ):
                if kind == "text":
                    full_text += value
                    yield _sse_event("token", {"text": value})
                elif kind == "done":
                    llm_result_obj = value
                elif kind == "error":
                    err_code = value.error_code or "TRANSIENT"
                    yield _sse_event(
                        "error",
                        {
                            "code": err_code,
                            "message": value.content,
                            "retryable": err_code in ai_client.RETRYABLE_CODES,
                        },
                    )
                    return

            if not llm_result_obj or not full_text.strip():
                yield _sse_event(
                    "error",
                    {
                        "code": "TRANSIENT",
                        "message": ai_client.ERROR_MESSAGES["TRANSIENT"],
                        "retryable": True,
                    },
                )
                return

            assistant_msg = await message_repo.create(
                pool,
                conversation_id=conversation_id,
                role="assistant",
                content=full_text,
                message_type="text",
                token_count=llm_result_obj.output_tokens,
                sender_id=influencer_id,
            )

            # Background side-effects mirror the non-streaming path
            asyncio.create_task(
                memory.extract_and_store(
                    pool,
                    user_id=user_id,
                    influencer_id=influencer_id,
                    user_message=content or "",
                    assistant_response=full_text,
                    message_id=user_msg["id"],
                    is_nsfw=is_nsfw,
                )
            )

            yield _sse_event(
                "done",
                {
                    "assistant_message": _format_message(assistant_msg),
                    "provider": llm_result_obj.provider,
                    "model": llm_result_obj.model,
                    "tokens": llm_result_obj.output_tokens,
                },
            )
        except Exception as e:
            logger.exception(f"SSE stream failed: {e}")
            yield _sse_event(
                "error",
                {
                    "code": "TRANSIENT",
                    "message": ai_client.ERROR_MESSAGES["TRANSIENT"],
                    "retryable": True,
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
