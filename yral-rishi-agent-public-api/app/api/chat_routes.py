# ---------------------------------------------------------------------------
# chat_routes.py — /api/v1/chat/* + /api/v2/chat/* endpoint handlers.
#
# ⭐ START HERE: every endpoint below returns a SCHEMA-VALID stub
# `ApiResponse<T>` while the `enable_session_3_phase_1_day_2_placeholder_
# responses` flag is on. Day 4 swaps the stub bodies for orchestrator
# RPC calls without touching the handler signatures or response shapes.
#
# THE 7 ENDPOINTS THIS FILE OWNS (per interface-contracts/00-api-contract.md):
#   POST   /api/v1/chat/conversations                         → ConversationResponse
#   GET    /api/v1/chat/conversations                         → list[ConversationResponse]
#   POST   /api/v1/chat/conversations/{conversation_id}/messages → MessageResponse
#   GET    /api/v1/chat/conversations/{conversation_id}/messages → list[MessageResponse]
#   POST   /api/v1/chat/conversations/{conversation_id}/read  → {} (empty)
#   DELETE /api/v1/chat/conversations/{conversation_id}       → {} (empty)
#   GET    /api/v2/chat/conversations                         → list[ConversationResponse]
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
# WHY MINIMAL REQUEST MODELS IN THIS FILE (not in response_models.py)?
# Request DTOs are route-internal — they describe what mobile sends to
# THIS endpoint. Response models (response_models.py) are cross-cutting because
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

# datetime / timezone — stub factories stamp ISO8601 UTC timestamps
# onto the placeholder response bodies.
from datetime import datetime, timezone

# logging — Day-4C send_message logs idempotency adoption source +
# cache hit/miss via the structured logger (logs.py wiring carries
# request_id).
import logging

# typing.Literal — used for the conversation_type field per Codex
# PR #97 BLOCKER 3 (E5 mandates the 3 locked modes; previously str
# accepted any value). typing.Optional — nullable request fields.
from typing import Literal, Optional

# uuid4 — generates per-message + per-conversation IDs the stub
# factories return so each call produces a unique-but-syntactically-
# valid ID mobile can store.
from uuid import uuid4

# httpx — Day-4C catches httpx.TimeoutException + httpx.ConnectError
# on the orchestrator call to map them to the public-api 504 envelope.
import httpx

# sentry_sdk — Day-4C tags every orchestrator failure path with
# `orchestrator.call.failed=<timeout|503|status|bad_response_shape>`
# so the Sentry dashboard can pivot on the upstream failure mode.
import sentry_sdk

# fastapi — APIRouter groups endpoints; Depends wires per-handler
# dependencies (real auth + flag gate); Header maps the
# X-Idempotency-Key header to a handler parameter (Day-4C); Path /
# Query map URL components to parameters; Request is needed by
# Day-4C send_message for the per-request httpx context; WebSocket
# signals the inbox stub added in BLOCKER 4.
from fastapi import APIRouter, Depends, Header, Path, Query, Request, WebSocket

# fastapi.responses — Day-4C send_message returns Response (for cache
# replay byte-for-byte) + JSONResponse (for error-mapping paths).
from fastapi.responses import JSONResponse, Response

# pydantic — BaseModel for request bodies; model_validator enforces
# the per-mode participant-id rule added in Codex PR #97 BLOCKER 3.
from pydantic import BaseModel, model_validator

# Response models the handlers return (renamed from `*Dto` per Codex
# PR #97 BLOCKER 1 + Rishi 2026-05-19 Option-A).
from app.api.response_models import ConversationResponse, MessageResponse

# ApiResponse envelope every endpoint wraps its payload in (locked
# contract per A8 + A16).
from app.api.envelope import ApiResponse

# Feature-flag dependency gating the Day-2 placeholder bodies so they
# can't accidentally ship to production traffic.
from app.api.feature_flag import require_day_2_placeholder_flag_enabled

# Error helper + status map — used by the influencer-write stubs added
# in BLOCKER 4 that return `service_unavailable` envelopes instead of
# letting the locked paths 404. Also used by Day-4C's send_message
# error-mapping paths (timeout / 503 / 422 / 5xx).
from app.api.errors import HTTP_STATUS_FOR_ERROR_CODE, error_response

# Real auth dependency (Day 4B) — replaces the PR #97 round-5 placeholder
# (`require_authorization_header`). Wired per-handler so every chat
# endpoint receives an `AuthenticatedUser` argument with the validated
# user_id + raw token (the latter Day-4C forwards to the orchestrator).
from app.api.dependencies import AuthenticatedUser, require_authenticated_user

# Day-4C orchestrator RPC client + F10 idempotency dedup. send_message
# is the first handler in v2 public-api to actually call the
# orchestrator; the rest stay on Day-2 stubs until later sprints.
from app import orchestrator_client
from app.api.idempotency import (
    cache_lookup,
    cache_store,
    resolve_idempotency_key,
)
from app.request_id_middleware import get_request_id

# Structured logger for Day-4C send_message diagnostics. Module-level so
# every log line carries the structured-logging request_id (per the
# logging.py / request_id_middleware.py wiring).
_logger = logging.getLogger(__name__)

# Router for the v1 surface every existing mobile build talks to. The
# prefix means handlers below declare paths relative to `/api/v1/chat/`.
# Day-4B replaced PR #97 round-5's router-level placeholder
# `dependencies=` with per-handler `Depends(require_authenticated_user)`
# so each handler receives the AuthenticatedUser as a parameter (the
# router-level form only ran the dep + discarded the value, which the
# real auth dep would have made unreachable for forwarding the user_id
# / raw_token to Day-4C's orchestrator RPC). WebSocket routes — the
# BLOCKER-4 ws_inbox_stub at the bottom of this file — still check
# auth inline inside the handler body since FastAPI's Request-typed
# Depends doesn't apply to WebSocket routes.
#
# F10 IDEMPOTENCY STATUS — Day 4C:
# `send_message` (POST /conversations/{id}/messages) now enforces F10
# Redis-backed dedup (24h TTL, scoped by user_id) — it's the first
# state-mutating handler in v2 public-api (Day-4C wires the orchestrator
# RPC + Redis cache here). The other 3 non-GET handlers (POST
# conversations, POST read, DELETE conv) still return Day-2 stubs
# without state mutation; they pick up F10 + the real orchestrator
# call in Day 5+ when their bodies stop being stubs.
chat_v1_router = APIRouter(prefix="/api/v1/chat", tags=["chat-v1"])

# Router for the v2 surface mobile uses for the bot-aware inbox.
chat_v2_router = APIRouter(prefix="/api/v2/chat", tags=["chat-v2"])

# Router for the WebSocket inbox stub (BLOCKER 4). SEPARATE from
# `chat_v1_router` because FastAPI's Request-typed `Depends` (which
# Day-4B's real auth uses) does not apply to WebSocket routes. The WS
# stub does its own auth check on the upgrade-request headers
# (lowercase-keyed mapping) inline below.
chat_v1_ws_router = APIRouter(prefix="/api/v1/chat", tags=["chat-v1-ws"])


# ===========================================================================
# Request models (route-internal — kept here per A2.1, not promoted to
# response_models.py until a second callsite needs the same shape)
# ===========================================================================


class CreateConversationRequest(BaseModel):
    """Body for POST /api/v1/chat/conversations.

    WHAT: the data mobile sends when opening a chat (either picking an
          AI Influencer from the catalog or starting an H2H thread).
    WHEN: each first-message-into-a-new-thread emits this; subsequent
          messages reuse the returned conversation_id.
    WHY:  mobile pattern — open the conversation FIRST, then POST the
          first message into it (per chat-ai's current flow per A8).

    PER-MODE PARTICIPANT VALIDATION (Codex PR #97 BLOCKER 3):
      - ai_chat        → requires `ai_influencer_id`; `participant_b_id` MUST be null
      - human_chat     → requires `participant_b_id`; `ai_influencer_id` MUST be null
      - chat_as_human  → requires `ai_influencer_id`; `participant_b_id` MUST be null
        (per the contract field comment "ai_influencer_id: for AI chat";
        chat_as_human is the AI-Influencer-adopts-human-persona mode,
        still anchored to an AI Influencer entity. If coordinator's
        interpretation differs, the model_validator below is the single
        place to flip.)

    Validation failures raise ValueError → FastAPI's
    RequestValidationError → main.py's envelope-shaped validation
    handler (Codex BLOCKER 2) → HTTP 400 with `error="validation_failed"`.
    """

    # AI Influencer the user wants to talk to. Required for ai_chat +
    # chat_as_human modes; MUST be null for human_chat.
    ai_influencer_id: Optional[str] = None

    # The OTHER user. Required for human_chat; MUST be null for the
    # two AI-anchored modes.
    participant_b_id: Optional[str] = None

    # Locked enum per E5 — H2H + AI + Chat-as-Human in one schema.
    # Codex PR #97 BLOCKER 3 tightened this from `str` to a Literal so
    # unknown modes fail validation instead of silently routing as
    # ai_chat.
    conversation_type: Literal["ai_chat", "human_chat", "chat_as_human"] = "ai_chat"

    @model_validator(mode="after")
    def _validate_participant_for_mode(self) -> "CreateConversationRequest":
        """Enforce the per-mode participant-id rule (Codex BLOCKER 3).

        WHAT: rejects the request when the participant fields don't
              match the conversation_type.
        WHEN: runs automatically after Pydantic populates every field.
        WHY:  prevents mobile from accidentally opening an "ai_chat"
              with a participant_b_id (which would silently misroute
              the turn at the orchestrator).
        """
        if self.conversation_type == "ai_chat":
            if not self.ai_influencer_id:
                raise ValueError("ai_chat requires ai_influencer_id")
            if self.participant_b_id is not None:
                raise ValueError("ai_chat must not set participant_b_id")
        elif self.conversation_type == "human_chat":
            if not self.participant_b_id:
                raise ValueError("human_chat requires participant_b_id")
            if self.ai_influencer_id is not None:
                raise ValueError("human_chat must not set ai_influencer_id")
        elif self.conversation_type == "chat_as_human":
            if not self.ai_influencer_id:
                raise ValueError("chat_as_human requires ai_influencer_id")
            if self.participant_b_id is not None:
                raise ValueError("chat_as_human must not set participant_b_id")
        return self


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
# Helper: build a SCHEMA-VALID stub MessageResponse for Day-2 responses
# ===========================================================================


def _stub_message(
    conversation_id: str,
    role: str = "assistant",
    content: str = (
        "[v2 phase-1 day-2 placeholder — real response from day-4 "
        "once orchestrator RPC is wired]"
    ),
    client_message_id: Optional[str] = None,
) -> MessageResponse:
    """Build a stub MessageResponse with a fresh UUID + current timestamp.

    WHAT: factory for SCHEMA-VALID placeholder messages used by Day-2
          chat handlers.
    WHEN: called from the POST messages handler (and from the GET
          messages history handler when building a sample reply).
    WHY:  centralizes the "what does a stub message look like?" shape
          so when Day-4 RPC integration lands, only ONE function changes
          to call orchestrator.run_turn() instead of producing this stub.
    """
    return MessageResponse(
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
    conversation_type: Literal["ai_chat", "human_chat", "chat_as_human"] = "ai_chat",
) -> ConversationResponse:
    """Build a stub ConversationResponse with a fresh UUID + current timestamp.

    WHAT: factory for SCHEMA-VALID placeholder conversations used by
          Day-2 inbox + create handlers.
    WHEN: called by every inbox / create handler while the placeholder
          flag is on.
    WHY:  centralized so the Day-4 swap is a single-file edit.
    """
    new_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    return ConversationResponse(
        id=new_id,
        user_id=user_id,
        participant_b_id=participant_b_id,
        ai_influencer_id=ai_influencer_id,
        conversation_type=conversation_type,
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
    response_model=ApiResponse[ConversationResponse],
    summary="Create or fetch the conversation for a given user + AI Influencer",
)
async def create_or_get_conversation(
    body: CreateConversationRequest,
    user: AuthenticatedUser = Depends(require_authenticated_user),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[ConversationResponse]:
    """Create-or-get a conversation thread (Day-2 stub).

    WHAT: returns a fresh ConversationResponse with the influencer_id mobile
          asked for. Real impl (Day 4) calls Session 4's
          influencer-directory + persists a row in the conversations table.
    WHEN: mobile opens a chat for the first time (or rejoins one whose
          ID it lost).
    WHY:  the inbox + messages endpoints all need a stable conversation
          ID; this is where it comes from.
    """
    # Pull the influencer/participant + conversation_type from the request
    # so the stub's output reflects what mobile asked for (NOT a blanket
    # stub-influencer with hardcoded "ai_chat" — Codex PR #97 BLOCKER 3).
    conv = _stub_conversation(
        ai_influencer_id=body.ai_influencer_id,
        participant_b_id=body.participant_b_id,
        conversation_type=body.conversation_type,
    )
    return ApiResponse[ConversationResponse](
        success=True,
        msg="OK",
        error=None,
        data=conv,
    )


@chat_v1_router.get(
    "/conversations",
    response_model=ApiResponse[list[ConversationResponse]],
    summary="v1 inbox — all conversations for the authenticated user",
)
async def list_conversations_v1(
    user: AuthenticatedUser = Depends(require_authenticated_user),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[list[ConversationResponse]]:
    """List the authenticated user's conversations (Day-2 stub).

    WHAT: returns a 1-element list with a stub conversation. The real
          impl reads from Session 4's orchestrator (which itself reads
          conversations + last_message join from Postgres).
    WHEN: mobile loads the inbox screen.
    WHY:  inbox is the entry point for every chat flow.
    """
    return ApiResponse[list[ConversationResponse]](
        success=True,
        msg="OK",
        error=None,
        data=[_stub_conversation()],
    )


@chat_v1_router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ApiResponse[MessageResponse],
    summary="Send a message; receive the assistant reply",
)
async def send_message(
    body: SendMessageRequest,
    request: Request,
    conversation_id: str = Path(..., description="Conversation UUID"),
    x_idempotency_key: Optional[str] = Header(
        default=None,
        alias="X-Idempotency-Key",
        description="F10 idempotency key (mobile-generated UUID; empty → server mints one)",
    ),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> Response:
    """Send a user message + return the assistant's reply (Day-4C orchestrator wire).

    WHAT: forwards the turn to Session-4's orchestrator at /v1/turn,
          wraps the JSON MessageResponse-shaped body in the ApiResponse
          envelope, dedupes via F10 idempotency (Redis 24h cache scoped
          by user_id). Day-4C replaces the Day-2 stub body — the
          placeholder feature-flag gate is no longer applied to THIS
          handler.
    WHEN: every chat send. Hot path.
    WHY:  first real-data handler in v2 public-api; the rest of the
          chat / influencer surface still returns Day-2 stubs until
          Day 5+ wires the orchestrator + influencer-directory RPCs.

    Error mapping (per Day-4C directive; ERROR-CODE I6 push-back in PR
    body — directive specified `orchestrator_unavailable` +
    `orchestrator_timeout` but the contract's locked error-codes
    table forbids new codes; using `service_unavailable` here +
    distinguishing via msg field + Sentry tag for backend signal):
      - orchestrator 503 → public-api 503, error="service_unavailable",
        Sentry tag orchestrator.call.failed=503
      - orchestrator 422 → public-api 422, error="validation_failed"
      - orchestrator timeout / connect error → public-api 504, error=
        "service_unavailable", Sentry tag orchestrator.call.failed=timeout
      - orchestrator 5xx other → public-api 503, error="service_unavailable",
        Sentry tag orchestrator.call.failed=<status>
    """
    # ---- 1. Resolve idempotency key + log adoption source -----------
    idempotency_key, key_source = resolve_idempotency_key(x_idempotency_key)
    _logger.info(
        "send_message idempotency.key resolved",
        extra={
            "user_id": user.user_id,
            "conversation_id": conversation_id,
            "idempotency.key_source": key_source,
        },
    )

    # ---- 2. Cache lookup — replay if hit ---------------------------
    cached = cache_lookup(user.user_id, idempotency_key)
    if cached is not None:
        _logger.info(
            "send_message idempotency.hit",
            extra={
                "user_id": user.user_id,
                "conversation_id": conversation_id,
                "idempotency.hit": True,
            },
        )
        return Response(
            content=cached.body_bytes,
            status_code=cached.status,
            media_type="application/json",
        )

    # ---- 3. Forward to orchestrator ---------------------------------
    request_id = get_request_id()
    try:
        orchestrator_response = await orchestrator_client.run_turn(
            user_id=user.user_id,
            conversation_id=conversation_id,
            message_content=body.content,
            client_message_id=body.client_message_id,
            media_urls=body.media_urls,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        # Network / timeout failure → 504. Sentry-tagged per Day-4C
        # directive ("orchestrator.call.failed") so the dashboard can
        # pivot on the failure mode.
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("orchestrator.call.failed", "timeout")
            scope.set_context(
                "orchestrator_call",
                {
                    "user_id": user.user_id,
                    "conversation_id": conversation_id,
                    "idempotency_key": idempotency_key,
                    "exc_type": type(exc).__name__,
                },
            )
            sentry_sdk.capture_message(
                f"orchestrator call timeout / connect error: {exc}",
                level="error",
            )
        body_dict = error_response(
            "service_unavailable",
            "Orchestrator unreachable (timeout / connect error)",
        ).model_dump()
        return JSONResponse(status_code=504, content=body_dict)

    # ---- 4. Map orchestrator HTTP status to public-api response -----
    if orchestrator_response.status_code == 422:
        # Bad request shape — mirror the validation failure to the
        # caller so mobile can surface it.
        body_dict = error_response(
            "validation_failed",
            "Orchestrator rejected the request as malformed",
        ).model_dump()
        return JSONResponse(status_code=422, content=body_dict)

    if orchestrator_response.status_code == 503:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("orchestrator.call.failed", "503")
            scope.set_context(
                "orchestrator_call",
                {"user_id": user.user_id, "idempotency_key": idempotency_key},
            )
            sentry_sdk.capture_message(
                "orchestrator returned 503",
                level="warning",
            )
        body_dict = error_response(
            "service_unavailable",
            "Orchestrator is currently unavailable",
        ).model_dump()
        return JSONResponse(status_code=503, content=body_dict)

    if not 200 <= orchestrator_response.status_code < 300:
        # Other 4xx / 5xx — map to public 503 with Sentry tag carrying
        # the actual upstream status so on-call can debug.
        with sentry_sdk.new_scope() as scope:
            scope.set_tag(
                "orchestrator.call.failed",
                str(orchestrator_response.status_code),
            )
            scope.set_context(
                "orchestrator_call",
                {"user_id": user.user_id, "idempotency_key": idempotency_key},
            )
            sentry_sdk.capture_message(
                f"orchestrator returned unexpected status {orchestrator_response.status_code}",
                level="error",
            )
        body_dict = error_response(
            "service_unavailable",
            "Orchestrator returned an unexpected error",
        ).model_dump()
        return JSONResponse(status_code=503, content=body_dict)

    # ---- 5. Happy path: parse + wrap in ApiResponse envelope -------
    # Orchestrator returns JSON MessageResponse-shaped per the PR #96 +
    # PR #98 contract update (NOT SSE — the SSE shape in the on-main
    # contract was stale before PR #98). Wrap in ApiResponse[MessageResponse]
    # for the caller.
    try:
        message_json = orchestrator_response.json()
        message_response = MessageResponse(**message_json)
    except (ValueError, TypeError) as exc:
        # Orchestrator returned 2xx but body doesn't match MessageResponse.
        # Treat as a service-layer error — 503 + Sentry tag for follow-up.
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("orchestrator.call.failed", "bad_response_shape")
            scope.set_context(
                "orchestrator_call",
                {
                    "user_id": user.user_id,
                    "idempotency_key": idempotency_key,
                    "exc": str(exc),
                },
            )
            sentry_sdk.capture_message(
                f"orchestrator 2xx body did not parse as MessageResponse: {exc}",
                level="error",
            )
        body_dict = error_response(
            "service_unavailable",
            "Orchestrator returned malformed response",
        ).model_dump()
        return JSONResponse(status_code=503, content=body_dict)

    envelope = ApiResponse[MessageResponse](
        success=True,
        msg="OK",
        error=None,
        data=message_response,
    )

    # ---- 6. Cache the successful response for F10 dedup ------------
    # JSON-serialize via the model_dump_json (the wire-canonical form)
    # so a cache replay returns byte-for-byte equivalent content.
    envelope_bytes = envelope.model_dump_json().encode("utf-8")
    cache_store(
        user.user_id,
        idempotency_key,
        status=200,
        body_bytes=envelope_bytes,
    )

    return Response(
        content=envelope_bytes,
        status_code=200,
        media_type="application/json",
    )


@chat_v1_router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ApiResponse[list[MessageResponse]],
    summary="Paginated message history for the given conversation",
)
async def list_messages(
    conversation_id: str = Path(..., description="Conversation UUID"),
    limit: int = Query(20, ge=1, le=100, description="Page size; default 20, max 100"),
    before: Optional[str] = Query(None, description="Message UUID; returns older-than-this"),
    user: AuthenticatedUser = Depends(require_authenticated_user),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[list[MessageResponse]]:
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
    return ApiResponse[list[MessageResponse]](
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
    user: AuthenticatedUser = Depends(require_authenticated_user),
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
    user: AuthenticatedUser = Depends(require_authenticated_user),
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
    response_model=ApiResponse[list[ConversationResponse]],
    summary="v2 bot-aware inbox — what current mobile build hits",
)
async def list_conversations_v2(
    user: AuthenticatedUser = Depends(require_authenticated_user),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[list[ConversationResponse]]:
    """v2 inbox (Day-2 stub).

    WHAT: returns a 1-element stub list. The v2 inbox differs from v1 by
          surfacing bot metadata inline (no extra round-trip per
          conversation row). Day-4 RPC integration reads from the
          orchestrator + influencer-directory in one go.
    WHEN: mobile loads the inbox screen on the current build (per the
          contract, mobile uses v2 — v1 stays for backward compat).
    WHY:  v2 is the actual hot path mobile takes today.
    """
    return ApiResponse[list[ConversationResponse]](
        success=True,
        msg="OK",
        error=None,
        data=[_stub_conversation()],
    )


# ===========================================================================
# Codex PR #97 BLOCKER 4 — WebSocket inbox stub
# ===========================================================================
#
# `WS /api/v1/chat/ws/inbox/{user_id}` is in the locked contract; real
# implementation lands Days 14-18 per the agent definition. Until then
# the route exists so mobile (or contract tests) don't see a 404 on
# upgrade. The stub accepts the WebSocket handshake, sends a single
# envelope-shaped error frame, then closes with code 1011 (server
# error). Mobile reads the close-reason + payload to surface "feature
# not yet available."


@chat_v1_ws_router.websocket("/ws/inbox/{user_id}")
async def ws_inbox_stub(websocket: WebSocket, user_id: str) -> None:
    """WebSocket inbox stream — BLOCKER 4 stub with inline auth check.

    WHAT: validates `Authorization: Bearer <...>` from the upgrade
          request's headers. FastAPI's Request-typed `Depends` (which
          Day-4B's real auth uses on the HTTP routes) does NOT apply
          to WebSocket routes — so the WS upgrade does its own inline
          Bearer-present check here. On missing/malformed auth, closes
          the connection with code 1008 ("policy violation") and
          reason `unauthorized` BEFORE accepting. On valid auth,
          accepts the upgrade, sends one envelope-shaped
          service_unavailable frame, then closes with code 1011
          ("server error") and reason
          `service_unavailable_stub_days_14_18`. Real WS impl lands
          Days 14-18; this stub holds the wire contract per BLOCKER 4.
    WHEN: any client (mobile or contract test) connecting to
          /api/v1/chat/ws/inbox/{user_id} before Days 14-18 lands.
    WHY:  locked contract path; without registration the route 404s
          on upgrade which mobile would surface as a routing bug
          rather than "feature not implemented yet."
          The inline Bearer-present check matches the HTTP routes'
          unauthorized envelope (close code 1008 close-reason
          "unauthorized") so mobile pattern-matches the same way on
          WS as on HTTP. The real WS impl (Days 14-18) wires the
          full JWT dual-validate shadow rig once a WebSocket-shaped
          auth dependency lands; until then Bearer-present is the
          minimum-viable contract.
    """
    _ = user_id  # accepted-and-ignored until the real impl lands
    auth_header = websocket.headers.get("authorization")  # WS headers lowercase
    if not auth_header or not auth_header.startswith("Bearer "):
        # Close with policy-violation code 1008 BEFORE accepting so an
        # unauthenticated client never sees the stub payload. Matches
        # the HTTP routes' 401 envelope semantics.
        await websocket.close(code=1008, reason="unauthorized")
        return
    raw_token = auth_header[len("Bearer ") :].strip()
    if not raw_token:
        await websocket.close(code=1008, reason="unauthorized")
        return

    await websocket.accept()
    await websocket.send_json(
        {
            "success": False,
            "msg": (
                "WebSocket inbox not yet implemented (Days 14-18 per agent "
                "definition). Falling back to the v2 polling inbox."
            ),
            "error": "service_unavailable",
            "data": None,
        },
    )
    # Close code 1011 = server error per RFC 6455; reason carries the
    # machine-readable signal so mobile can pattern-match without
    # parsing the JSON frame.
    await websocket.close(code=1011, reason="service_unavailable_stub_days_14_18")


# ===========================================================================
# RELATED FILES:
#   ../main.py               — wires chat_v1_router + chat_v2_router into
#                              the FastAPI app via app.include_router(...)
#   feature_flag.py          — every handler depends on
#                              require_day_2_placeholder_flag_enabled
#   envelope.py              — ApiResponse[T] wrapper EVERY response uses
#   response_models.py                  — ConversationResponse + MessageResponse shapes
#   ../../tests/contract/test_chat_routes.py
#                            — asserts envelope + DTO shape + feature-flag gating
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — locked endpoint paths + DTO shapes
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — Session 4's orchestrator RPC that Day 4 swaps in
# ===========================================================================
