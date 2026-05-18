# ---------------------------------------------------------------------------
# dtos.py — Pydantic models for every response payload mobile receives.
#
# ⭐ START HERE: each class below is a 1:1 Python copy of a DTO listed
# in interface-contracts/00-api-contract.md "Response DTOs" section.
# Field names + types match the contract EXACTLY — mobile parses these
# shapes verbatim, and per A8 v2 must preserve chat-ai's wire format.
#
# WHY Pydantic INSTEAD OF dataclass / plain dict?
# FastAPI uses Pydantic models to (a) validate request bodies, (b)
# serialize response objects to JSON, and (c) auto-generate OpenAPI
# schema entries. Defining the DTOs as BaseModel gives us validation +
# serialization + docs for free.
#
# WHY OPTIONAL FIELDS NOT REQUIRED?
# The contract marks several fields as nullable (e.g. `media_urls`,
# `client_message_id`, `participant_b_id`, `ai_influencer_id`,
# `last_message`, `creator_user_id`). Mobile treats `null` and missing
# fields equivalently; v2 ALWAYS emits the field (even as null) so the
# JSON shape never silently changes — the matching `Optional[X] = None`
# does that automatically.
#
# WHY conversation_type AS Literal?
# Mobile branches UI per type. A typo at a v2 callsite ("chat_as_humans"
# with a plural-s) would silently render wrong; the Literal makes the
# typo a type-check error before deploy.
#
# WHY is_active IS A Literal, NOT A bool?
# Existing chat-ai schema uses the string column "active" / "discontinued"
# per the contract. Keeping the same string preserves direct ETL row
# copies (Day-9 ETL per A4 + agent definition).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from typing import Literal, Optional

from pydantic import BaseModel


class MessageDto(BaseModel):
    """One chat message — user-written OR assistant-generated.

    WHAT: the unit returned by POST messages (the assistant's reply) +
          inside GET messages history arrays + as the `last_message`
          field inside ConversationDto.
    WHEN: every chat turn produces one of these on the assistant side;
          mobile composes one before POSTing for the user side.
    WHY:  the contract requires exact field names + types per A8 — break
          the shape, break every chat screen on mobile.
    """

    # UUID generated at create-time. Mobile uses this for read-tracking
    # and deduplication in the local message list.
    id: str

    # The conversation this message belongs to. UUID matching one of the
    # ConversationDto.id values from the inbox endpoint.
    conversation_id: str

    # "user" for human-typed messages, "assistant" for AI replies.
    # Mobile renders different bubbles per role; "system" + "tool" never
    # cross the wire per the contract (those stay inside the orchestrator).
    role: Literal["user", "assistant"]

    # The message text. Empty string is valid (e.g. an image-only
    # message). Never null — mobile expects a string here.
    content: str

    # Attached media (images / video thumbnails). Null when the message
    # is text-only; otherwise a list of presigned URLs the mobile client
    # fetches separately.
    media_urls: Optional[list[str]] = None

    # Client-assigned dedup ID. Mobile sends this on POST so server-side
    # retries don't double-insert the user message; the assistant reply
    # echoes the same ID back so mobile can match the response to the
    # outgoing request. Null on assistant-generated messages that have
    # no triggering client message (e.g. proactive nudge per Phase 5).
    client_message_id: Optional[str] = None

    # ISO8601 UTC timestamp the message was stored. Mobile orders the
    # transcript by this field and shows it in the time-stamp gutter.
    created_at: str

    # Counts toward the 50-message paywall threshold per E7? User
    # messages and most assistant replies do; system events (e.g. an
    # auto-greeting on first turn) do not. Mobile uses this to render
    # the paywall progress indicator.
    count_toward_paywall: bool


class ConversationDto(BaseModel):
    """One conversation thread between a user and one other participant.

    WHAT: a row in the inbox + the return value of POST
          /api/v1/chat/conversations (create-or-get).
    WHEN: returned from inbox endpoints + the create endpoint.
    WHY:  exact contract shape — mobile renders the inbox list directly
          from this; missing fields = blank rows.
    """

    # UUID of the conversation. Mobile uses this in every subsequent
    # call against this thread.
    id: str

    # The user side of the conversation. Always the JWT subject for the
    # authenticated user — never another user (privacy).
    user_id: str

    # For H2H chat (per E5): the OTHER human participant's user_id.
    # Null for AI-influencer chats.
    participant_b_id: Optional[str] = None

    # For AI chat: the influencer the user is talking to. Null for
    # H2H + "Chat as Human" conversations.
    ai_influencer_id: Optional[str] = None

    # Locked enum (per E5 — H2H + AI + Chat-as-Human in one schema from
    # day 1). Mobile branches the bubble + header rendering per type.
    conversation_type: Literal["ai_chat", "human_chat", "chat_as_human"]

    # Preview of the most recent message — shown in the inbox row's
    # subtitle. Null when the conversation was just created with no
    # messages yet.
    last_message: Optional[MessageDto] = None

    # ISO8601 timestamp of last_message. Mobile sorts inbox by this.
    last_message_at: str

    # Number of messages the user hasn't read. Mobile shows the
    # unread-bubble badge per row.
    unread_count: int


class InfluencerDto(BaseModel):
    """One AI Influencer's public profile (per B4 — "AI Influencer",
    not "bot").

    WHAT: returned from GET /api/v1/influencers + /trending + /{id}.
    WHEN: cached client-side (per the contract Cache-Control 300s
          recommendation on the list endpoint) but re-fetched on demand.
    WHY:  exact contract shape; the chat-ai schema's UUID is preserved
          per A4 + the Day-9 ETL plan, so existing user history continues
          to resolve influencer_id → influencer correctly post-cutover.
    """

    # UUID. Per A4 + the Day-9 ETL, the chat-ai influencer_id ports
    # forward unchanged so existing user chat history still works.
    id: str

    # The name mobile renders on the chat header + inbox row. Per B4 —
    # this is the AI Influencer's stage name, set by the creator at
    # creation time.
    display_name: str

    # A 1-2 sentence persona description shown on the influencer's
    # detail screen. NOT the Soul File — that's the orchestrator's
    # internal-only system prompt per E8 and never crosses this wire.
    bio: str

    # Presigned URL pointing at the avatar image. Mobile loads + caches
    # locally per the Cache-Control 300s on /influencers.
    avatar_url: str

    # Free-form persona category per the contract examples ("companion",
    # "nutritionist", ...). Not constrained to a Literal here because
    # the set evolves with the creator-studio rollout (Phase 7) and we
    # don't want to block new archetypes at the schema layer.
    archetype: str

    # True if the influencer is gated behind the NSFW flag; per A10 + the
    # llm-routing-matrix, is_nsfw=True forces OpenRouter regardless of
    # the per-influencer-id rule.
    is_nsfw: bool

    # Follower count shown on the detail screen. Mobile renders verbatim;
    # number formatting (1.2K, 3.4M) is client-side per the existing
    # chat-ai behavior.
    follower_count: int

    # The user_id that created this influencer. Null for system /
    # platform-owned influencers (the original yral-curated set).
    creator_user_id: Optional[str] = None

    # Locked enum matching the chat-ai column (per the contract). "active"
    # = listable + chattable; "discontinued" = creator-soft-deleted, still
    # readable for users mid-conversation but absent from list endpoints.
    is_active: Literal["active", "discontinued"]


class ChatAccessDataDto(BaseModel):
    """The paywall access check payload mobile receives BEFORE sending a
    chat message — per E7 + CURRENT-TRUTH's paywall contract section.

    WHAT: mirrored from yral-billing's /google/chat-access/check by
          public-api's Redis cache (60s TTL per E7).
    WHEN: mobile calls this endpoint immediately before each POST to
          messages. On hasAccess=false, mobile triggers the Google Play
          IAP sheet client-side. NEVER returns HTTP 402 — paywall is an
          ApiResponse with `data.hasAccess=false`, NOT a status code.
    WHY:  exact contract shape so the cache layer is transparent to
          mobile and chat-ai → v2 swap is invisible at the wire.
    """

    # Mobile's gate: True = open the chat send button, False = open the
    # IAP sheet. Per CURRENT-TRUTH this is the ONLY paywall signal —
    # there is no HTTP 402 anywhere in this codebase.
    hasAccess: bool  # noqa: N815 — camelCase matches the chat-ai contract verbatim

    # ISO8601 when current access lapses. Null when access is False
    # (nothing to expire). camelCase per the contract verbatim.
    expiresAt: Optional[str] = None  # noqa: N815 — camelCase matches the chat-ai contract verbatim


# ===========================================================================
# RELATED FILES:
#   envelope.py              — ApiResponse[MessageDto], ApiResponse[list[InfluencerDto]], ...
#   chat_routes.py           — assembles + returns these DTOs (stub data Day 2)
#   influencer_routes.py     — same, for the influencer read set
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — locked source of every field name + type
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — Session 4's orchestrator + influencer-directory
#                              RPC return these same DTOs Day-4 onward
# ===========================================================================
