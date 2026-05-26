# ---------------------------------------------------------------------------
# models.py — Pydantic request + response models for the internal RPC.
#
# ⭐ START HERE: this file defines the data shapes for the 4 conversation +
# message RPC endpoints. There are two groups:
#
#   REQUEST MODELS — what callers (orchestrator, public-api) send:
#     ConversationCreateRequest    POST /v1/conversations
#     MessageCreateItem            one item inside AppendMessagesRequest
#     AppendMessagesRequest        POST /v1/conversations/{id}/messages
#
#   RESPONSE MODELS — what this service returns to callers:
#     MessageResponse              MIRRORS public-api's MessageResponse exactly
#     ConversationResponse         MIRRORS public-api's ConversationResponse exactly
#
# WHY MIRROR public-api'S SHAPES?
# The orchestrator + public-api consume these responses. public-api
# sometimes forwards them (with thin wrapping) to mobile. If the shapes
# diverge, public-api needs a translation step — A8 + A16 forbid that.
# Keep the wire shapes byte-identical between this service's responses
# and public-api's contracts. If public-api's response_models.py changes,
# update this file at the same time (single-decision point: the locked
# interface-contracts/00-api-contract.md).
#
# WHY MessageCreateItem ALLOWS role="system"?
# The messages table stores system-role rows (used by the orchestrator for
# context framing). Only "user" + "assistant" cross the mobile wire per
# the contract (MessageResponse.role is Literal["user", "assistant"]).
# The storage layer accepts all three; the read path filters system rows
# out before building MessageResponse objects.
#
# WHY created_at AS str IN RESPONSE MODELS?
# public-api uses ISO8601 strings per the locked contract. asyncpg returns
# datetime objects for TIMESTAMPTZ columns. The route handlers in
# conversation_routes.py convert datetime → ISO8601 string via _format_dt()
# before building these response models. Keeping the models string-typed
# maintains byte-identical parity with the mobile-facing contract.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from typing import Any, Literal, Optional

from pydantic import BaseModel


# ===========================================================================
# Request models — accepted by POST /v1/conversations and
#                 POST /v1/conversations/{id}/messages
# ===========================================================================


class ConversationCreateRequest(BaseModel):
    """Body for POST /v1/conversations (create-or-get).

    WHAT: the fields needed to look up or create a conversation row.
          The service uses (user_id, conversation_type, ai_influencer_id,
          participant_b_id) as the natural key for the upsert.
    WHEN: public-api calls this when mobile opens a chat for the first
          time, OR after losing the conversation_id (e.g. app reinstall).
    WHY:  conversation_id is the stable reference for every subsequent
          message; this endpoint is where it is minted or recovered.
    """

    # The user starting the conversation. Forwarded from public-api after
    # JWT validation; trusted here per E6 (no re-validation on the
    # internal overlay).
    user_id: str

    # For ai_chat + chat_as_human: the AI Influencer UUID. Null for H2H.
    # Stored as `influencer_id` in the DB; exposed as `ai_influencer_id`
    # on the wire (contract name). See conversation_routes.py mapping.
    ai_influencer_id: Optional[str] = None

    # For human_chat: the other participant's user_id. Null for AI modes.
    participant_b_id: Optional[str] = None

    # Locked enum per E5. Included in the natural upsert key so
    # "ai_chat with influencer X" and "chat_as_human with X" are
    # treated as distinct conversations.
    conversation_type: Literal["ai_chat", "human_chat", "chat_as_human"] = "ai_chat"


class MessageCreateItem(BaseModel):
    """One message being appended in a POST .../messages batch.

    WHAT: a single turn — the user's input message, the assistant's reply,
          OR a system event. Multiple items are sent together so the
          orchestrator persists the full turn atomically.
    WHEN: called by the orchestrator at the end of each chat turn.
    WHY:  batching user message + assistant reply in one request avoids a
          partial-turn DB state (user message saved, reply not yet saved)
          during the window between the two inserts.
    """

    # Who authored this message. "system" is stored for orchestrator
    # context framing but filtered from mobile-facing responses.
    role: Literal["user", "assistant", "system"]

    # Message body. Empty string is valid for media-only messages.
    content: str = ""

    # Mobile-side dedup ID. Present on user messages to support F10
    # per-user idempotency. Null on assistant + system messages (AI replies
    # carry no client-side ID).
    client_message_id: Optional[str] = None

    # Presigned media URLs. Null for text-only messages.
    media_urls: Optional[list[str]] = None

    # LLM call metadata for assistant messages (prompt_tokens,
    # completion_tokens, model, latency_ms). Null for user + system messages.
    # Stored as-is in the gemini_metadata JSONB column.
    gemini_metadata: Optional[dict[str, Any]] = None

    # E7 paywall counter. Defaults TRUE — if the orchestrator omits this
    # field the message is counted conservatively (fail-safe direction).
    count_toward_paywall: bool = True


class AppendMessagesRequest(BaseModel):
    """Body for POST /v1/conversations/{id}/messages.

    WHAT: one or more messages to append atomically to a conversation.
    WHEN: the orchestrator calls this at the end of each chat turn to
          persist [user_message, assistant_reply] together. Typically 2
          items per turn; may include a system message for context framing.
    WHY:  single-call atomic append means the DB is never in a half-turn
          state. The orchestrator does not need two separate RPC calls per
          turn.
    """

    # At least one message is expected per turn. The list preserves insertion
    # order — messages are inserted in array order and the ordering is
    # visible in the created_at timestamps (NOW() at INSERT time).
    messages: list[MessageCreateItem]


# ===========================================================================
# Response models — returned by all 4 endpoints.
# These MIRROR public-api's response_models.py exactly.
# Any change here MUST be reflected in:
#   yral-rishi-agent-public-api/app/api/response_models.py
# The locked source of truth is:
#   interface-contracts/00-api-contract.md
# ===========================================================================


class MessageResponse(BaseModel):
    """One chat message — user-written OR assistant-generated.

    WHAT: the wire shape returned by POST .../messages (the appended
          messages list) and GET .../messages (history page).
    WHEN: every time a caller reads or appends messages.
    WHY:  byte-identical to public-api's MessageResponse per A8 + A16 —
          public-api can forward these directly to mobile without
          translation.

    MIRROR: yral-rishi-agent-public-api/app/api/response_models.py::MessageResponse
    """

    # UUID of the stored message row.
    id: str

    # UUID of the conversation this message belongs to.
    conversation_id: str

    # "user" or "assistant" only on the wire — system messages are filtered
    # before this model is built (conversation_routes.py never passes
    # role="system" to this constructor).
    role: Literal["user", "assistant"]

    # Message body. Never null per the contract — empty string for media-
    # only messages.
    content: str

    # Presigned media URLs. Null for text-only messages.
    media_urls: Optional[list[str]] = None

    # Mobile-side dedup ID. Null on assistant messages (AI replies carry
    # no client-side ID).
    client_message_id: Optional[str] = None

    # ISO8601 UTC timestamp the message was stored. Formatted from the
    # Postgres TIMESTAMPTZ in conversation_routes.py's _format_dt() helper.
    created_at: str

    # E7 paywall counter — TRUE if this message counts toward the
    # 50-message per-user limit.
    count_toward_paywall: bool


class ConversationResponse(BaseModel):
    """One conversation thread between a user and one other participant.

    WHAT: returned from POST /v1/conversations (create-or-get) and from
          GET /v1/conversations/by-user/{user_id} (inbox list).
    WHEN: mobile opens a chat or loads the inbox screen.
    WHY:  byte-identical to public-api's ConversationResponse per A8 + A16.

    MIRROR: yral-rishi-agent-public-api/app/api/response_models.py::ConversationResponse

    DB NAME MAPPING:
      DB column `influencer_id` → wire field `ai_influencer_id`.
      The rename happens in conversation_routes.py's
      _row_to_conversation_response() helper — this model always uses the
      wire name so callers see a consistent shape.
    """

    # UUID of the conversation row.
    id: str

    # The user side of the conversation. Always the requesting user's JWT
    # subject (forwarded as X-User-Id on the internal RPC).
    user_id: str

    # For H2H chat: the other participant's user_id. Null for AI chats.
    participant_b_id: Optional[str] = None

    # For AI chat: the AI Influencer UUID (renamed from DB's `influencer_id`
    # to the wire name `ai_influencer_id`). Null for H2H conversations.
    ai_influencer_id: Optional[str] = None

    # Locked enum per E5.
    conversation_type: Literal["ai_chat", "human_chat", "chat_as_human"]

    # Preview of the most recent non-system message. Null when the
    # conversation has no messages yet (just created).
    last_message: Optional[MessageResponse] = None

    # ISO8601 UTC timestamp of the last message (or creation time when no
    # messages exist yet). Mobile sorts inbox rows by this field.
    last_message_at: str

    # Number of messages the user has not read. Phase 1 always returns 0 —
    # real unread tracking requires a mark-read endpoint wired to this
    # service (later sprint).
    unread_count: int = 0


# ===========================================================================
# RELATED FILES:
#   conversation_routes.py   — route handlers that accept + return these models
#   ../../tests/test_conversation_routes.py
#                            — tests that assert these shapes
#   ../../../../yral-rishi-agent-public-api/app/api/response_models.py
#                            — MIRROR source — keep in sync with changes here
#   ../../../../yral-rishi-agent-plan-and-discussions/
#     multi-session-parallel-build-coordination/interface-contracts/
#     00-api-contract.md     — locked wire contract both this service and
#                              public-api implement
# ===========================================================================
