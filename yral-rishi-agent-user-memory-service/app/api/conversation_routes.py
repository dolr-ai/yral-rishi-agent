# ---------------------------------------------------------------------------
# conversation_routes.py — 4 internal RPC route handlers for conversations
#   + messages.
#
# ⭐ START HERE: this file is the core of Deliverable 2. It exposes 4
# endpoints that the orchestrator + public-api call to persist and read
# conversation history:
#
#   POST /v1/conversations                    create-or-get a conversation
#   POST /v1/conversations/{id}/messages      append a turn (user + reply)
#   GET  /v1/conversations/by-user/{user_id}  inbox list (with last_message)
#   GET  /v1/conversations/{id}/messages      paginated message history
#
# CALLER MAP:
#   public-api   → POST /v1/conversations            (mobile opens chat)
#   public-api   → GET  /v1/conversations/by-user    (inbox screen load)
#   orchestrator → POST /v1/conversations/{id}/msgs  (end of each turn)
#   orchestrator → GET  /v1/conversations/{id}/msgs  (context fetch before LLM)
#
# WHY RAW asyncpg SQL (NO SQLAlchemy ORM)?
# Per F12 + the explicit directive: asyncpg + raw SQL + Pydantic. No ORM.
# The SQL is annotated with its performance profile so the reader knows
# which index it relies on and how it scales.
#
# WHY NO AUTH CHECK ON THESE ROUTES?
# These are INTERNAL RPC endpoints — only reachable on the Swarm overlay
# `yral-v2-internal` (per C3). No Docker port-publish → not internet-
# accessible. Callers are trusted per internal-rpc-contracts.md §E6.
# External auth (JWT validation) lives in public-api — it validates
# before any call reaches here. If inter-service mTLS lands in a future
# phase, it mounts here via a middleware without changing handlers.
#
# UPSERT LOGIC (POST /v1/conversations):
# Natural key = (user_id, conversation_type, influencer_id, participant_b_id)
# WHERE soft_deleted_at IS NULL. If an active conversation with this key
# exists, we return it. Otherwise we INSERT and return the new row. This
# mirrors chat-ai's create-or-get behaviour (per A8) so mobile can recover
# a conversation_id after an app reinstall without creating duplicates.
# IS NOT DISTINCT FROM handles NULL equality correctly — two NULLs match.
#
# PAGINATION SEMANTICS (GET /v1/conversations/{id}/messages):
# Returns the N most-recent non-system messages in chronological order
# (oldest first within the page). With the `before` cursor, returns the
# N messages just before the cursor message (for loading older history
# when the user scrolls up). The subquery DESC-then-ASC pattern ensures
# "most recent N" semantics while preserving chronological page order.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import json
import uuid
from datetime import timezone
from typing import Optional

import asyncpg
from fastapi import APIRouter, HTTPException, Path, Query

from app.api.models import (
    AppendMessagesRequest,
    ConversationCreateRequest,
    ConversationResponse,
    MessageResponse,
)
from app.database import get_pool


# All conversation + message endpoints share this router prefix.
# No auth dependency — internal-overlay-only routes (see file header / C3).
router = APIRouter(prefix="/v1", tags=["conversations"])


# ===========================================================================
# Helper functions — datetime formatting + row → model converters
# ===========================================================================


def _format_dt(dt) -> str:
    """Convert an asyncpg TIMESTAMPTZ value to an ISO8601 UTC string.

    WHAT: formats a Python datetime (returned by asyncpg for TIMESTAMPTZ
          columns) into "YYYY-MM-DDTHH:MM:SS.ffffff+00:00" that the
          locked public-api contract expects for timestamp fields.
    WHEN: called by every response-model builder that holds a datetime.
    WHY:  MessageResponse + ConversationResponse use str for timestamps per
          the locked contract (response_models.py). asyncpg gives datetimes;
          we convert here so the Pydantic models stay string-typed.
    """
    # Timezone-aware datetimes: convert to explicit UTC then format.
    # Naive datetimes (should not occur with TIMESTAMPTZ): assume UTC.
    if dt.tzinfo is not None:
        utc_dt = dt.astimezone(timezone.utc)
    else:
        # Defensive: TIMESTAMPTZ should always be tz-aware from asyncpg,
        # but if a naive datetime ever appears, treat it as UTC.
        utc_dt = dt
    return utc_dt.isoformat()


def _parse_media_urls(raw) -> Optional[list[str]]:
    """Deserialise a JSONB media_urls value from an asyncpg Record.

    WHAT: asyncpg may return JSONB columns as a raw JSON string (if no
          codec is registered) or as a Python list (if the jsonb codec is
          registered). This function normalises either form to list[str].
    WHEN: called when building MessageResponse from any DB row that has
          a media_urls column.
    WHY:  asyncpg's JSONB handling differs between configurations. A
          single normalisation function prevents "got str, expected list"
          errors from silently propagating into the response.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        # asyncpg returned raw JSON text — parse it
        return json.loads(raw)
    # asyncpg JSON codec returned a Python object already
    return raw


def _row_to_message_response(row: asyncpg.Record) -> MessageResponse:
    """Convert an asyncpg messages SELECT row to a MessageResponse.

    WHAT: maps asyncpg Record column values to the wire-canonical
          MessageResponse shape. Handles the JSONB media_urls parsing and
          the datetime-to-string conversion for created_at.
    WHEN: called after every INSERT RETURNING or SELECT on messages — once
          per row, never in a loop without the caller iterating.
    WHY:  centralises the column-name → response-field mapping. If a
          column renames or a type changes, this is the single update point.
    """
    return MessageResponse(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        # role is a CHECK-constrained TEXT column that only holds "user",
        # "assistant", or "system". Callers must filter to "user"/"assistant"
        # before passing a row here — see each route's filter comment.
        role=row["role"],
        content=row["content"],
        media_urls=_parse_media_urls(row["media_urls"]),
        client_message_id=row["client_message_id"],
        created_at=_format_dt(row["created_at"]),
        count_toward_paywall=row["count_toward_paywall"],
    )


def _row_to_conversation_response(
    row: asyncpg.Record,
    last_message: Optional[MessageResponse],
) -> ConversationResponse:
    """Convert an asyncpg conversations row + optional last_message to a
    ConversationResponse.

    WHAT: maps asyncpg Record column values to the wire-canonical
          ConversationResponse shape, including the DB→wire column rename:
            DB `influencer_id` → wire `ai_influencer_id`.
    WHEN: called by every endpoint that returns a ConversationResponse.
    WHY:  the DB column is `influencer_id` (short, unambiguous in the DB
          context). The public-api contract uses `ai_influencer_id` to
          make the AI nature explicit on the wire. This is the single
          mapping point — changing it here propagates everywhere.
    """
    return ConversationResponse(
        id=str(row["id"]),
        user_id=row["user_id"],
        participant_b_id=row["participant_b_id"],
        # DB column `influencer_id` → wire field `ai_influencer_id` (contract name)
        ai_influencer_id=row["influencer_id"],
        conversation_type=row["conversation_type"],
        last_message=last_message,
        last_message_at=_format_dt(row["last_message_at"]),
        # Phase 1: unread_count is always 0 — real tracking in later sprint
        unread_count=0,
    )


# ===========================================================================
# Route handlers
# Declaration order matters for FastAPI's router: the literal-segment route
# GET /by-user/{user_id} is declared BEFORE the parameterised-segment route
# GET /{conversation_id}/messages to ensure `by-user` is never consumed as a
# conversation_id parameter. FastAPI resolves routes left-to-right in the
# order they are added, so the more-specific (literal) path wins.
# ===========================================================================


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    summary="Create or fetch the active conversation for a user + participant",
)
async def create_or_get_conversation(
    body: ConversationCreateRequest,
) -> ConversationResponse:
    """Create-or-get a conversation (upsert by natural key).

    WHAT: looks for an existing active conversation matching
          (user_id, conversation_type, influencer_id, participant_b_id)
          WHERE soft_deleted_at IS NULL. Returns it if found. Creates and
          returns a new row if not found.
    WHEN: public-api calls this when mobile opens a chat for the first
          time, or after losing the conversation_id (e.g. app reinstall).
    WHY:  mirrors chat-ai's create-or-get behaviour (per A8) — no
          duplicate conversation rows for the same thread when mobile
          retries the create call.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        # --- 1. Look for an existing active conversation -----------------
        # IS NOT DISTINCT FROM treats NULL as equal to NULL — so "no
        # influencer" matches "no influencer" and "no participant_b" matches
        # "no participant_b" without needing IS NULL checks.
        # The partial index conversations_by_user_active_idx covers this
        # query (user_id filter + soft_deleted_at IS NULL predicate).
        existing = await conn.fetchrow(
            """
            SELECT id, user_id, influencer_id, participant_b_id,
                   conversation_type, last_message_at, message_count
            FROM conversations
            WHERE user_id = $1
              AND conversation_type = $2
              AND soft_deleted_at IS NULL
              AND influencer_id IS NOT DISTINCT FROM $3
              AND participant_b_id IS NOT DISTINCT FROM $4
            ORDER BY last_message_at DESC
            LIMIT 1
            """,
            body.user_id,
            body.conversation_type,
            body.ai_influencer_id,
            body.participant_b_id,
        )

        if existing is not None:
            # Found an active conversation — fetch its last non-system message
            # (for the `last_message` field in ConversationResponse).
            # Messages index messages_by_conversation_time_idx covers this.
            last_msg_row = await conn.fetchrow(
                """
                SELECT id, conversation_id, role, content, client_message_id,
                       media_urls, created_at, count_toward_paywall
                FROM messages
                WHERE conversation_id = $1
                  AND role != 'system'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                existing["id"],
            )
            last_msg = (
                _row_to_message_response(last_msg_row) if last_msg_row else None
            )
            return _row_to_conversation_response(existing, last_msg)

        # --- 2. No existing conversation — create a new row -------------
        # gen_random_uuid() mints the primary key in Postgres (not in
        # Python) so UUID generation is handled by the DB layer.
        new_row = await conn.fetchrow(
            """
            INSERT INTO conversations
                (user_id, influencer_id, participant_b_id, conversation_type)
            VALUES ($1, $2, $3, $4)
            RETURNING id, user_id, influencer_id, participant_b_id,
                      conversation_type, last_message_at, message_count
            """,
            body.user_id,
            body.ai_influencer_id,
            body.participant_b_id,
            body.conversation_type,
        )

        # Newly created conversation has no messages — last_message is null.
        return _row_to_conversation_response(new_row, last_message=None)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
    summary="Append a batch of messages to a conversation (orchestrator endpoint)",
)
async def append_messages(
    body: AppendMessagesRequest,
    conversation_id: str = Path(..., description="Conversation UUID"),
) -> list[MessageResponse]:
    """Append one or more messages to a conversation atomically.

    WHAT: inserts all items in body.messages into the messages table within
          a single transaction, then updates conversations.last_message_at
          + message_count. Returns only the non-system messages as
          MessageResponse objects (system messages are stored but filtered).
    WHEN: the orchestrator calls this at the end of each chat turn to
          persist [user_message, assistant_reply] together.
    WHY:  atomic batch insert ensures the DB is never in a half-turn state
          (user message saved, reply not yet). Filtering system messages
          from the response preserves the mobile wire contract — role
          "system" never crosses the mobile boundary.

    Returns 404 if the conversation does not exist.
    Returns 422 if conversation_id is not a valid UUID.
    """
    # Validate + coerce early — gives a clean 422 before any DB call if
    # the caller passes a malformed UUID (e.g. a plain string slug).
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"conversation_id is not a valid UUID: {exc}",
        ) from exc

    pool = get_pool()
    async with pool.acquire() as conn:
        # --- 1. Verify the conversation exists --------------------------
        # 404 before the FK constraint fires gives the caller a deterministic
        # error code rather than a Postgres-level IntegrityError that maps
        # ambiguously to 500.
        exists = await conn.fetchval(
            "SELECT id FROM conversations WHERE id = $1",
            conv_uuid,
        )
        if exists is None:
            raise HTTPException(
                status_code=404,
                detail=f"Conversation {conversation_id} not found",
            )

        # --- 2. Insert all messages in a single transaction -------------
        # If any INSERT fails (e.g. CHECK constraint violation on role),
        # the whole batch rolls back — no partial turn is persisted.
        async with conn.transaction():
            inserted_rows: list[asyncpg.Record] = []
            for item in body.messages:
                # Serialise JSONB columns to JSON strings for asyncpg.
                # Explicit json.dumps ensures correct serialisation
                # regardless of whether the asyncpg JSON codec is registered.
                media_urls_json = (
                    json.dumps(item.media_urls)
                    if item.media_urls is not None
                    else None
                )
                gemini_json = (
                    json.dumps(item.gemini_metadata)
                    if item.gemini_metadata is not None
                    else None
                )

                row = await conn.fetchrow(
                    """
                    INSERT INTO messages
                        (conversation_id, role, content, client_message_id,
                         media_urls, gemini_metadata, count_toward_paywall)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
                    RETURNING id, conversation_id, role, content,
                              client_message_id, media_urls, created_at,
                              count_toward_paywall
                    """,
                    conv_uuid,
                    item.role,
                    item.content,
                    item.client_message_id,
                    media_urls_json,
                    gemini_json,
                    item.count_toward_paywall,
                )
                inserted_rows.append(row)

            # --- 3. Update conversation stats ---------------------------
            # Increment message_count by ALL inserted items (including
            # system messages — the counter reflects DB row count, not
            # paywall count). Update last_message_at to the current time.
            await conn.execute(
                """
                UPDATE conversations
                SET last_message_at = NOW(),
                    message_count = message_count + $1
                WHERE id = $2
                """,
                len(body.messages),
                conv_uuid,
            )

        # --- 4. Build response — filter system messages -----------------
        # system-role rows are stored but must not appear in the response.
        # Mobile's MessageResponse.role is Literal["user", "assistant"].
        return [
            _row_to_message_response(row)
            for row in inserted_rows
            if row["role"] != "system"
        ]


@router.get(
    "/conversations/by-user/{user_id}",
    response_model=list[ConversationResponse],
    summary="List active conversations for a user (inbox endpoint)",
)
async def list_conversations_by_user(
    user_id: str = Path(..., description="User UUID (from JWT subject)"),
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="Max conversations to return; default 20, max 100",
    ),
) -> list[ConversationResponse]:
    """Return a user's active conversations, most recently active first.

    WHAT: fetches up to `limit` non-soft-deleted conversations for
          `user_id`, ordered by last_message_at DESC. Each ConversationResponse
          includes the most-recent non-system message inline via a
          LATERAL JOIN — one round-trip for the full inbox payload.
    WHEN: public-api calls this to render the inbox screen for the
          authenticated user.
    WHY:  the LATERAL JOIN avoids N+1 reads — one DB round-trip regardless
          of how many conversations the user has. The partial index
          conversations_by_user_active_idx covers the outer query filter
          (user_id + soft_deleted_at IS NULL).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        # LATERAL JOIN explanation:
        # For each conversation row `c`, Postgres evaluates the subquery
        # against `messages WHERE conversation_id = c.id` and returns the
        # single most-recent non-system message. The `LEFT JOIN ... ON TRUE`
        # means a conversation with no messages returns NULL for all `m.*`
        # columns (last_message will be None in the response).
        # Index used: messages_by_conversation_time_idx (conversation_id, created_at ASC)
        # — ORDER BY DESC reverses the ASC index efficiently.
        rows = await conn.fetch(
            """
            SELECT
                c.id,
                c.user_id,
                c.influencer_id,
                c.participant_b_id,
                c.conversation_type,
                c.last_message_at,
                c.message_count,
                m.id                   AS msg_id,
                m.conversation_id      AS msg_conversation_id,
                m.role                 AS msg_role,
                m.content              AS msg_content,
                m.client_message_id    AS msg_client_message_id,
                m.media_urls           AS msg_media_urls,
                m.created_at           AS msg_created_at,
                m.count_toward_paywall AS msg_count_toward_paywall
            FROM conversations c
            LEFT JOIN LATERAL (
                SELECT id, conversation_id, role, content, client_message_id,
                       media_urls, created_at, count_toward_paywall
                FROM messages
                WHERE conversation_id = c.id
                  AND role != 'system'
                ORDER BY created_at DESC
                LIMIT 1
            ) m ON TRUE
            WHERE c.user_id = $1
              AND c.soft_deleted_at IS NULL
            ORDER BY c.last_message_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )

        result: list[ConversationResponse] = []
        for row in rows:
            # Build the last_message from the `msg_*` columns if present.
            # msg_id is NULL when the conversation has no non-system messages.
            last_msg: Optional[MessageResponse] = None
            if row["msg_id"] is not None:
                last_msg = MessageResponse(
                    id=str(row["msg_id"]),
                    conversation_id=str(row["msg_conversation_id"]),
                    role=row["msg_role"],
                    content=row["msg_content"],
                    media_urls=_parse_media_urls(row["msg_media_urls"]),
                    client_message_id=row["msg_client_message_id"],
                    created_at=_format_dt(row["msg_created_at"]),
                    count_toward_paywall=row["msg_count_toward_paywall"],
                )

            result.append(_row_to_conversation_response(row, last_msg))

        return result


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse],
    summary="Paginated message history for a conversation",
)
async def list_messages(
    conversation_id: str = Path(..., description="Conversation UUID"),
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="Page size — number of messages per page; default 20, max 100",
    ),
    before: Optional[str] = Query(
        None,
        description=(
            "Message UUID cursor. When set, returns `limit` messages "
            "created BEFORE this message (for loading older history). "
            "When omitted, returns the most recent `limit` messages."
        ),
    ),
) -> list[MessageResponse]:
    """Paginated message history for a conversation.

    WHAT: returns up to `limit` non-system messages in chronological order
          (oldest first within the returned page). When `before` is set,
          returns the `limit` messages just before the cursor message —
          the standard mobile "scroll up to load older" pattern.
          When omitted, returns the most-recent `limit` messages (the
          orchestrator's LLM context fetch use case).
    WHEN: the orchestrator calls this (without cursor) to fetch the last
          N turns as context before the LLM call. public-api calls this
          when mobile opens a conversation thread or scrolls up for older
          history (with cursor).
    WHY:  the DESC-then-ASC subquery gives "most recent N" semantics with
          chronological ordering within the page:
            1. inner query: ORDER BY created_at DESC LIMIT N — selects
               the N most-recent (or N before the cursor) rows.
            2. outer query: ORDER BY created_at ASC — re-orders the page
               chronologically (oldest-first) for the caller.
          The messages_by_conversation_time_idx index
          (conversation_id, created_at ASC) supports both the DESC and ASC
          scans efficiently.

    Returns 404 if the conversation does not exist.
    Returns 422 if conversation_id or `before` is not a valid UUID.
    """
    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"conversation_id is not a valid UUID: {exc}",
        ) from exc

    pool = get_pool()
    async with pool.acquire() as conn:
        # Verify the conversation exists before querying messages.
        # 404 is more actionable for the orchestrator than an empty list
        # when the conversation_id is wrong.
        exists = await conn.fetchval(
            "SELECT id FROM conversations WHERE id = $1",
            conv_uuid,
        )
        if exists is None:
            raise HTTPException(
                status_code=404,
                detail=f"Conversation {conversation_id} not found",
            )

        if before is None:
            # No cursor — return the most-recent `limit` non-system messages
            # in chronological order (DESC to select, then ASC to order page).
            # Index: messages_by_conversation_time_idx (conversation_id, created_at ASC)
            rows = await conn.fetch(
                """
                SELECT *
                FROM (
                    SELECT id, conversation_id, role, content,
                           client_message_id, media_urls, created_at,
                           count_toward_paywall
                    FROM messages
                    WHERE conversation_id = $1
                      AND role != 'system'
                    ORDER BY created_at DESC
                    LIMIT $2
                ) sub
                ORDER BY sub.created_at ASC
                """,
                conv_uuid,
                limit,
            )
        else:
            # Cursor provided — validate it, then return `limit` messages
            # before the cursor in chronological order.
            try:
                before_uuid = uuid.UUID(before)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"before is not a valid UUID: {exc}",
                ) from exc

            # Subquery resolves the cursor UUID to a created_at timestamp;
            # outer WHERE filters messages strictly older than the cursor.
            # DESC-then-ASC ensures the page is chronological.
            rows = await conn.fetch(
                """
                SELECT *
                FROM (
                    SELECT id, conversation_id, role, content,
                           client_message_id, media_urls, created_at,
                           count_toward_paywall
                    FROM messages
                    WHERE conversation_id = $1
                      AND role != 'system'
                      AND created_at < (
                          SELECT created_at
                          FROM messages
                          WHERE id = $2
                      )
                    ORDER BY created_at DESC
                    LIMIT $3
                ) sub
                ORDER BY sub.created_at ASC
                """,
                conv_uuid,
                before_uuid,
                limit,
            )

        return [_row_to_message_response(row) for row in rows]


# ===========================================================================
# RELATED FILES:
#   models.py                        — Pydantic request + response shapes
#   ../main.py                       — app.include_router(router) wires this
#   ../database.py                   — get_pool() called in every handler
#   ../../tests/test_conversation_routes.py
#                                    — route-level tests (httpx + testcontainers)
#   ../../../../yral-rishi-agent-conversation-turn-orchestrator/app/run_turn.py
#                                    — primary caller of POST .../messages +
#                                      GET .../messages (context fetch)
#   ../../../../yral-rishi-agent-public-api/app/api/chat_routes.py
#                                    — primary caller of POST /conversations +
#                                      GET /conversations/by-user/{user_id}
# ===========================================================================
