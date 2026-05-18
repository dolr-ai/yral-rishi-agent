# ---------------------------------------------------------------------------
# chat_routes.py — /api/v1/chat/* + /api/v2/chat/* endpoint handlers.
#
# ⭐ START HERE: every endpoint below returns a SCHEMA-VALID stub
# `ApiResponse<T>` while the `enable_session_3_phase_1_day_2_placeholder_
# responses` flag is on. Day 4 swaps the stub bodies for orchestrator
# RPC calls without touching the handler signatures or response shapes.
#
# THE 7 ENDPOINTS THIS FILE OWNS (per interface-contracts/00-api-contract.md):
#   POST   /api/v1/chat/conversations                         → ConversationDto
#   GET    /api/v1/chat/conversations                         → list[ConversationDto]
#   POST   /api/v1/chat/conversations/{conversation_id}/messages → MessageDto
#   GET    /api/v1/chat/conversations/{conversation_id}/messages → list[MessageDto]
#   POST   /api/v1/chat/conversations/{conversation_id}/read  → {} (empty)
#   DELETE /api/v1/chat/conversations/{conversation_id}       → {} (empty)
#   GET    /api/v2/chat/conversations                         → list[ConversationDto]
#
# The WebSocket inbox endpoint (`WS /api/v1/chat/ws/inbox/{user_id}`)
# lands Days 14-18 per the agent definition.
#
# WHY TWO APIRouter INSTANCES (chat_v1 + chat_v2)?
# The contract uses different version prefixes (`/api/v1/` vs `/api/v2/`).
# Two routers means the OpenAPI tags page groups them visibly + future
# /api/v2/chat/* additions land in chat_v2_router without route-path
# conflicts.
#
# WHY MINIMAL REQUEST MODELS IN THIS FILE (not in dtos.py)?
# Request DTOs are route-internal — they describe what mobile sends to
# THIS endpoint. Response DTOs (dtos.py) are cross-cutting because
# Sessions 4 + 5 reference them. Per A2.1 — keep request shapes next to
# the handler that owns them; don't speculatively share until two
# callsites need the same shape.
#
# WHY THE STUB CONTENT MENTIONS "[v2 phase-1 day-2 placeholder ...]"?
# Per the agent definition Day 2 spec: "placeholder text is obvious +
# non-confusable with real LLM output." If a feature-flag misconfiguration
# slips a stub to mobile, the user sees "[v2 phase-1 day-2 placeholder...]"
# in the chat bubble — obviously not Tara saying hello.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from app.api.dtos import ConversationDto, MessageDto
from app.api.envelope import ApiResponse
from app.api.feature_flag import require_day_2_placeholder_flag_enabled

# Router for the v1 surface every existing mobile build talks to. The
# prefix means handlers below declare paths relative to `/api/v1/chat/`.
chat_v1_router = APIRouter(prefix="/api/v1/chat", tags=["chat-v1"])

# Router for the v2 surface mobile uses for the bot-aware inbox.
chat_v2_router = APIRouter(prefix="/api/v2/chat", tags=["chat-v2"])


# ===========================================================================
# Request models (route-internal — kept here per A2.1, not promoted to
# dtos.py until a second callsite needs the same shape)
# ===========================================================================


class CreateConversationRequest(BaseModel):
    """Body for POST /api/v1/chat/conversations.

    WHAT: the data mobile sends when opening a chat (either picking an
          AI Influencer from the catalog or starting an H2H thread).
    WHEN: each first-message-into-a-new-thread emits this; subsequent
          messages reuse the returned conversation_id.
    WHY:  mobile pattern — open the conversation FIRST, then POST the
          first message into it (per chat-ai's current flow per A8).
    """

    # AI Influencer the user wants to talk to. Null for H2H threads
    # (where `participant_b_id` is set instead). Per the contract,
    # exactly one of these two must be non-null.
    ai_influencer_id: Optional[str] = None

    # The OTHER user (per E5 — H2H + AI + Chat-as-Human in one schema).
    # Null for AI chats.
    participant_b_id: Optional[str] = None

    # Locked enum matching ConversationDto.conversation_type so the
    # server doesn't have to infer the thread kind from which ID is
    # populated. Mobile sends this explicitly.
    conversation_type: str = "ai_chat"


class SendMessageRequest(BaseModel):
    """Body for POST /api/v1/chat/conversations/{id}/messages.

    WHAT: one chat message the user is sending.
    WHEN: every chat-bubble send event on mobile.
    WHY:  mirrors the chat-ai contract per A8; Day-4 RPC integration
          forwards these fields verbatim to Session 4's orchestrator.
    """

    # The text the user typed. Empty string allowed (media-only message).
    content: str

    # Mobile-side dedup ID per the X-Client-Message-Id header convention
    # (the contract lists it as both a header AND a body field for
    # message-create — the body form is the chat-ai convention used by
    # the existing mobile parser).
    client_message_id: Optional[str] = None

    # Media attached to this message. Null for text-only. Each URL is a
    # presigned upload that mobile already POSTed to the media service
    # BEFORE sending the message body.
    media_urls: Optional[list[str]] = None


class MarkReadRequest(BaseModel):
    """Body for POST /api/v1/chat/conversations/{id}/read.

    WHAT: which message the user has read up to. Mobile sends the
          conversation's last-known message_id; server decrements the
          unread_count accordingly.
    WHEN: when the user opens a conversation thread on mobile.
    WHY:  unread_count surfacing in the inbox depends on this signal.
    """

    # The most-recent message_id the user has seen. Server marks every
    # message with created_at <= this message's created_at as read.
    last_read_message_id: str


# ===========================================================================
# Helper: build a SCHEMA-VALID stub MessageDto for Day-2 responses
# ===========================================================================


def _stub_message(
    conversation_id: str,
    role: str = "assistant",
    content: str = (
        "[v2 phase-1 day-2 placeholder — real response from day-4 "
        "once orchestrator RPC is wired]"
    ),
    client_message_id: Optional[str] = None,
) -> MessageDto:
    """Build a stub MessageDto with a fresh UUID + current timestamp.

    WHAT: factory for SCHEMA-VALID placeholder messages used by Day-2
          chat handlers.
    WHEN: called from the POST messages handler (and from the GET
          messages history handler when building a sample reply).
    WHY:  centralizes the "what does a stub message look like?" shape
          so when Day-4 RPC integration lands, only ONE function changes
          to call orchestrator.run_turn() instead of producing this stub.
    """
    return MessageDto(
        id=str(uuid4()),
        conversation_id=conversation_id,
        role=role,  # type: ignore[arg-type] — Literal narrowed by caller
        content=content,
        media_urls=None,
        client_message_id=client_message_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        # Stub messages don't count toward the paywall — they're not
        # real chat turns. Once Day-4 RPC integration lands, this flag
        # will follow the orchestrator's per-turn decision per E7.
        count_toward_paywall=False,
    )


def _stub_conversation(
    user_id: str = "stub-user-id",
    ai_influencer_id: Optional[str] = "stub-influencer-id",
    participant_b_id: Optional[str] = None,
) -> ConversationDto:
    """Build a stub ConversationDto with a fresh UUID + current timestamp.

    WHAT: factory for SCHEMA-VALID placeholder conversations used by
          Day-2 inbox + create handlers.
    WHEN: called by every inbox / create handler while the placeholder
          flag is on.
    WHY:  centralized so the Day-4 swap is a single-file edit.
    """
    new_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    return ConversationDto(
        id=new_id,
        user_id=user_id,
        participant_b_id=participant_b_id,
        ai_influencer_id=ai_influencer_id,
        conversation_type="ai_chat",
        last_message=_stub_message(new_id),
        last_message_at=now,
        unread_count=0,
    )


# ===========================================================================
# Handlers — declared in the order mobile most-commonly calls them
# (entry-point first, history second, list third, mutations last)
# ===========================================================================


@chat_v1_router.post(
    "/conversations",
    response_model=ApiResponse[ConversationDto],
    summary="Create or fetch the conversation for a given user + AI Influencer",
)
async def create_or_get_conversation(
    body: CreateConversationRequest,
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[ConversationDto]:
    """Create-or-get a conversation thread (Day-2 stub).

    WHAT: returns a fresh ConversationDto with the influencer_id mobile
          asked for. Real impl (Day 4) calls Session 4's
          influencer-directory + persists a row in the conversations table.
    WHEN: mobile opens a chat for the first time (or rejoins one whose
          ID it lost).
    WHY:  the inbox + messages endpoints all need a stable conversation
          ID; this is where it comes from.
    """
    # Pull the influencer/participant from the request so the stub's
    # output reflects what mobile asked for (NOT a blanket stub-influencer).
    conv = _stub_conversation(
        ai_influencer_id=body.ai_influencer_id,
        participant_b_id=body.participant_b_id,
    )
    return ApiResponse[ConversationDto](
        success=True,
        msg="OK",
        error=None,
        data=conv,
    )


@chat_v1_router.get(
    "/conversations",
    response_model=ApiResponse[list[ConversationDto]],
    summary="v1 inbox — all conversations for the authenticated user",
)
async def list_conversations_v1(
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[list[ConversationDto]]:
    """List the authenticated user's conversations (Day-2 stub).

    WHAT: returns a 1-element list with a stub conversation. The real
          impl reads from Session 4's orchestrator (which itself reads
          conversations + last_message join from Postgres).
    WHEN: mobile loads the inbox screen.
    WHY:  inbox is the entry point for every chat flow.
    """
    return ApiResponse[list[ConversationDto]](
        success=True,
        msg="OK",
        error=None,
        data=[_stub_conversation()],
    )


@chat_v1_router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ApiResponse[MessageDto],
    summary="Send a message; receive the assistant reply",
)
async def send_message(
    body: SendMessageRequest,
    conversation_id: str = Path(..., description="Conversation UUID"),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[MessageDto]:
    """Send a user message + return the assistant's reply (Day-2 stub).

    WHAT: produces a stub assistant MessageDto echoing the conversation_id
          mobile gave us. Day-4 RPC integration calls
          orchestrator.run_turn(...) instead and streams back the real
          assistant content.
    WHEN: every chat send.
    WHY:  the hot path mobile hits dozens of times per session.
    """
    # The stub's `client_message_id` echoes the body's so mobile's local
    # dedup logic works against the returned assistant message the same
    # way it will against the Day-4 real response.
    reply = _stub_message(
        conversation_id=conversation_id,
        client_message_id=body.client_message_id,
    )
    return ApiResponse[MessageDto](
        success=True,
        msg="OK",
        error=None,
        data=reply,
    )


@chat_v1_router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ApiResponse[list[MessageDto]],
    summary="Paginated message history for the given conversation",
)
async def list_messages(
    conversation_id: str = Path(..., description="Conversation UUID"),
    limit: int = Query(20, ge=1, le=100, description="Page size; default 20, max 100"),
    before: Optional[str] = Query(None, description="Message UUID; returns older-than-this"),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[list[MessageDto]]:
    """Paginated message history (Day-2 stub).

    WHAT: returns a stub list with one assistant + one user message
          so mobile's transcript renderer has something to scroll
          through. Real impl reads from the orchestrator (or directly
          from the conversation_turn table; the architecture call lands
          Day 4).
    WHEN: mobile opens an existing conversation or scrolls up for older
          messages.
    WHY:  per the contract — paginated history is a load-bearing endpoint.
    """
    # `limit` + `before` are accepted-and-ignored at Day-2 because the
    # stub list is fixed. Day-4 wires them into the real query.
    _ = limit
    _ = before
    return ApiResponse[list[MessageDto]](
        success=True,
        msg="OK",
        error=None,
        data=[
            _stub_message(conversation_id, role="user", content="(stub user message)"),
            _stub_message(conversation_id, role="assistant"),
        ],
    )


@chat_v1_router.post(
    "/conversations/{conversation_id}/read",
    response_model=ApiResponse[dict],
    summary="Mark messages up to the given message_id as read",
)
async def mark_read(
    body: MarkReadRequest,
    conversation_id: str = Path(..., description="Conversation UUID"),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[dict]:
    """Mark-read marker (Day-2 stub — returns success on any input).

    WHAT: returns success=True with empty data. Real impl writes a
          read_state row + decrements the inbox unread_count.
    WHEN: mobile opens a conversation thread.
    WHY:  unread_count in the inbox depends on this signal.
    """
    # conversation_id + body are unused at Day-2; Day-4 wires them.
    _ = conversation_id
    _ = body
    return ApiResponse[dict](
        success=True,
        msg="OK",
        error=None,
        data={},
    )


@chat_v1_router.delete(
    "/conversations/{conversation_id}",
    response_model=ApiResponse[dict],
    summary="Delete the given conversation (soft delete in chat-ai)",
)
async def delete_conversation(
    conversation_id: str = Path(..., description="Conversation UUID"),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[dict]:
    """Soft-delete a conversation (Day-2 stub).

    WHAT: returns success=True with empty data. Real impl flips the
          conversation's `is_deleted=true` (or moves to a deleted_at
          column — Day-4 RPC integration carries the chat-ai behavior).
    WHEN: user swipes-to-delete a conversation on the inbox screen.
    WHY:  inbox UX needs this signal even before the orchestrator is
          live so the contract test can pass.
    """
    _ = conversation_id
    return ApiResponse[dict](
        success=True,
        msg="OK",
        error=None,
        data={},
    )


# ===========================================================================
# /api/v2/chat/* — the bot-aware inbox mobile actually uses
# ===========================================================================


@chat_v2_router.get(
    "/conversations",
    response_model=ApiResponse[list[ConversationDto]],
    summary="v2 bot-aware inbox — what current mobile build hits",
)
async def list_conversations_v2(
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[list[ConversationDto]]:
    """v2 inbox (Day-2 stub).

    WHAT: returns a 1-element stub list. The v2 inbox differs from v1 by
          surfacing bot metadata inline (no extra round-trip per
          conversation row). Day-4 RPC integration reads from the
          orchestrator + influencer-directory in one go.
    WHEN: mobile loads the inbox screen on the current build (per the
          contract, mobile uses v2 — v1 stays for backward compat).
    WHY:  v2 is the actual hot path mobile takes today.
    """
    return ApiResponse[list[ConversationDto]](
        success=True,
        msg="OK",
        error=None,
        data=[_stub_conversation()],
    )


# ===========================================================================
# RELATED FILES:
#   ../main.py               — wires chat_v1_router + chat_v2_router into
#                              the FastAPI app via app.include_router(...)
#   feature_flag.py          — every handler depends on
#                              require_day_2_placeholder_flag_enabled
#   envelope.py              — ApiResponse[T] wrapper EVERY response uses
#   dtos.py                  — ConversationDto + MessageDto shapes
#   ../../tests/contract/test_chat_routes.py
#                            — asserts envelope + DTO shape + feature-flag gating
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — locked endpoint paths + DTO shapes
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — Session 4's orchestrator RPC that Day 4 swaps in
# ===========================================================================
