# ---------------------------------------------------------------------------
# conversation_routes.py — 5 internal RPC route handlers for conversations
#   + messages.
#
# ⭐ START HERE: this file is the core of Deliverable 2. It exposes 5
# endpoints that the orchestrator + public-api call to persist and read
# conversation history:
#
#   POST /v1/conversations                    create-or-get a conversation
#   POST /v1/conversations/{id}/messages      append a turn (user + reply)
#   GET  /v1/conversations/by-user/{user_id}  inbox list (with last_message)
#   GET  /v1/conversations/{id}               single conversation by ID
#   GET  /v1/conversations/{id}/messages      paginated message history
#
# CALLER MAP:
#   public-api   → POST /v1/conversations            (mobile opens chat)
#   public-api   → GET  /v1/conversations/by-user    (inbox screen load)
#   public-api   → GET  /v1/conversations/{id}       (per-request influencer_id
#                                                     derivation for PR-B2)
#   orchestrator → POST /v1/conversations/{id}/msgs  (end of each turn)
#   orchestrator → GET  /v1/conversations/{id}/msgs  (context fetch before LLM)
#
# WHY RAW asyncpg SQL (NO SQLAlchemy ORM)?
# Per F12 + the explicit directive: asyncpg + raw SQL + Pydantic. No ORM.
# The SQL is annotated with its performance profile so the reader knows
# which index it relies on and how it scales.
#
# WHY NO AUTH CHECK ON MOST ROUTES?
# POST / GET .../messages + GET .../by-user are INTERNAL RPC endpoints —
# only reachable on the Swarm overlay `yral-v2-internal` (per C3). No
# Docker port-publish → not internet-accessible. Callers are trusted per
# internal-rpc-contracts.md §E6. External auth (JWT validation) lives in
# public-api — it validates before any call reaches here.
#
# GET /v1/conversations/{id} requires X-User-Id header for tenant
# isolation — public-api forwards it after JWT validation. The route
# returns 404 (not 403) when the conversation exists but belongs to a
# different user — never leaking the existence of other users' data.
#
# UPSERT LOGIC (POST /v1/conversations):
# Natural key = (user_id, conversation_type, influencer_id, participant_b_id)
# WHERE soft_deleted_at IS NULL. The INSERT uses ON CONFLICT with the
# partial unique expression index (003_add_dedup_indexes.py) so the
# operation is atomic — two concurrent calls with the same key resolve
# to the same row. COALESCE in the index and ON CONFLICT target handles
# NULL equality: two NULLs match each other.
#
# IDEMPOTENCY (POST /v1/conversations/{id}/messages):
# If a message has a non-NULL client_message_id and a row with that
# (conversation_id, client_message_id) already exists, the INSERT uses
# ON CONFLICT DO NOTHING and we return the existing row. Retries from
# mobile (after a network blip) never duplicate messages.
#
# PAGINATION SEMANTICS (GET /v1/conversations/{id}/messages):
# Returns the N most-recent non-system messages in chronological order
# (oldest first within the page). With the `before` cursor, returns the
# N messages just before the cursor message (for loading older history
# when the user scrolls up). The subquery DESC-then-ASC pattern ensures
# "most recent N" semantics while preserving chronological page order.
# ORDER BY includes `id` as a tiebreaker for same-timestamp batch inserts
# so the ordering is always deterministic.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import json
import uuid
from datetime import timezone
from typing import Optional

import asyncpg
from fastapi import APIRouter, Header, HTTPException, Path, Query

from app.api.models import (
    AppendMessagesRequest,
    ConversationCreateRequest,
    ConversationResponse,
    MessageResponse,
)
from app.database import get_pool


# All conversation + message endpoints share this router prefix.
# GET /v1/conversations/{id} uses X-User-Id header for tenant isolation;
# other routes rely on Swarm overlay trust (C3, §E6).
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
# GET /by-user/{user_id} is declared BEFORE the parameterised-segment routes
# GET /{conversation_id} and GET /{conversation_id}/messages so `by-user`
# is never consumed as a conversation_id parameter. FastAPI resolves routes
# in the order they are added, so more-specific (literal) paths win.
# ===========================================================================


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    summary="Create or fetch the active conversation for a user + participant",
)
async def create_or_get_conversation(
    body: ConversationCreateRequest,
) -> ConversationResponse:
    """Create-or-get a conversation (atomic upsert by natural key).

    WHAT: atomically inserts a new active conversation matching
          (user_id, conversation_type, influencer_id, participant_b_id)
          WHERE soft_deleted_at IS NULL, or returns the existing row if
          a conflict is detected on the natural-key unique index added by
          003_add_dedup_indexes.py.
    WHEN: public-api calls this when mobile opens a chat for the first
          time, or after losing the conversation_id (e.g. app reinstall).
    WHY:  mirrors chat-ai's create-or-get behaviour (per A8). The atomic
          INSERT ... ON CONFLICT DO UPDATE is race-condition-free: two
          concurrent calls with the same key resolve to the same row
          without duplicate inserts. COALESCE in the conflict target
          handles NULL equality (NULL == NULL for this comparison).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        # --- Atomic upsert using the partial expression unique index --------
        # ON CONFLICT target must match 003_add_dedup_indexes.py's
        # conversations_natural_key_active_unique_idx exactly (same
        # columns + expressions + WHERE predicate).
        #
        # DO UPDATE SET last_message_at = conversations.last_message_at is
        # a no-op update — it does not change any value. Its purpose is to
        # ensure RETURNING always yields the row (DO NOTHING returns nothing;
        # DO UPDATE returns the row whether it was inserted or conflicted).
        # Index used: conversations_natural_key_active_unique_idx
        conv_row = await conn.fetchrow(
            """
            INSERT INTO conversations
                (user_id, influencer_id, participant_b_id, conversation_type)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (
                user_id,
                conversation_type,
                COALESCE(influencer_id, ''),
                COALESCE(participant_b_id, '')
            )
            WHERE soft_deleted_at IS NULL
            DO UPDATE SET
                last_message_at = conversations.last_message_at
            RETURNING id, user_id, influencer_id, participant_b_id,
                      conversation_type, last_message_at, message_count
            """,
            body.user_id,
            body.ai_influencer_id,
            body.participant_b_id,
            body.conversation_type,
        )

        # --- Fetch the last non-system message for the response -------------
        # Index: messages_by_conversation_time_idx (conversation_id, created_at ASC)
        # ORDER BY DESC reverses the ASC index efficiently for the most-recent row.
        last_msg_row = await conn.fetchrow(
            """
            SELECT id, conversation_id, role, content, client_message_id,
                   media_urls, created_at, count_toward_paywall
            FROM messages
            WHERE conversation_id = $1
              AND role != 'system'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            conv_row["id"],
        )
        last_msg = (
            _row_to_message_response(last_msg_row) if last_msg_row else None
        )
        return _row_to_conversation_response(conv_row, last_msg)


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
          Handles client_message_id idempotency: if a message with the same
          (conversation_id, client_message_id) already exists, the INSERT
          uses ON CONFLICT DO NOTHING and the existing row is returned —
          no duplicate inserted even if mobile retries after a network blip.
    WHEN: the orchestrator calls this at the end of each chat turn to
          persist [user_message, assistant_reply] together.
    WHY:  atomic batch insert ensures the DB is never in a half-turn state
          (user message saved, reply not yet). Filtering system messages
          from the response preserves the mobile wire contract — role
          "system" never crosses the mobile boundary.
          Idempotency via ON CONFLICT DO NOTHING protects against mobile
          retry doubling the paywall count or showing duplicate bubbles.

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
            result_rows: list[asyncpg.Record] = []
            new_row_count: int = 0

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

                # --- Idempotency via ON CONFLICT DO NOTHING ---------------
                # The partial unique index messages_client_message_id_dedup_idx
                # covers (conversation_id, client_message_id) WHERE
                # client_message_id IS NOT NULL.
                #
                # When client_message_id IS NULL (assistant / system messages):
                #   The partial index predicate excludes these rows; the
                #   INSERT always succeeds (no conflict possible).
                #
                # When client_message_id IS NOT NULL (user messages with dedup ID):
                #   If a row with the same (conv_id, client_message_id) exists,
                #   DO NOTHING fires: RETURNING yields nothing (None).
                #   We then SELECT the existing row and return it so the caller
                #   gets the same message_id as the original write — safe retry.
                row = await conn.fetchrow(
                    """
                    INSERT INTO messages
                        (conversation_id, role, content, client_message_id,
                         media_urls, gemini_metadata, count_toward_paywall)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
                    ON CONFLICT (conversation_id, client_message_id)
                    WHERE client_message_id IS NOT NULL
                    DO NOTHING
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

                is_new = row is not None

                if row is None and item.client_message_id is not None:
                    # Conflict hit — fetch the existing row so the caller
                    # gets the correct (original) message_id back.
                    row = await conn.fetchrow(
                        """
                        SELECT id, conversation_id, role, content,
                               client_message_id, media_urls, created_at,
                               count_toward_paywall
                        FROM messages
                        WHERE conversation_id = $1
                          AND client_message_id = $2
                        """,
                        conv_uuid,
                        item.client_message_id,
                    )

                if row is not None:
                    result_rows.append(row)
                if is_new:
                    # Count only genuinely new rows for the stats update.
                    # Retry-matched rows must not double-increment.
                    new_row_count += 1

            # --- 3. Update conversation stats (new rows only) -----------
            # Increment message_count only for rows that were freshly inserted.
            # Retry-matched rows are not counted again. Update last_message_at
            # to the current time.
            if new_row_count > 0:
                await conn.execute(
                    """
                    UPDATE conversations
                    SET last_message_at = NOW(),
                        message_count = message_count + $1
                    WHERE id = $2
                    """,
                    new_row_count,
                    conv_uuid,
                )

        # --- 4. Build response — filter system messages -----------------
        # system-role rows are stored but must not appear in the response.
        # Mobile's MessageResponse.role is Literal["user", "assistant"].
        return [
            _row_to_message_response(row)
            for row in result_rows
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
                ORDER BY created_at DESC, id DESC
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
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Fetch a single conversation by ID (with tenant isolation)",
)
async def get_conversation_by_id(
    conversation_id: str = Path(..., description="Conversation UUID"),
    x_user_id: str = Header(
        ...,
        description=(
            "The requesting user's ID (forwarded by public-api after JWT "
            "validation). Must match the conversation's owner — the route "
            "returns 404 for a wrong-user request to prevent leaking the "
            "existence of other users' conversations."
        ),
    ),
) -> ConversationResponse:
    """Fetch a single conversation by ID with tenant isolation.

    WHAT: fetches the active conversation for `conversation_id` from the DB,
          verifies it belongs to the user identified by the X-User-Id header,
          and returns a ConversationResponse with the most-recent non-system
          message inline.
    WHEN: public-api calls this before forwarding a chat request to the
          orchestrator, to derive the ai_influencer_id from the stored
          conversation record (Session 3 PR-B2 trust-boundary fix).
    WHY:  the orchestrator needs ai_influencer_id per-request but only the
          conversation_id arrives from mobile. The user-memory-service is the
          single source of truth for which AI the conversation belongs to.

    TENANT ISOLATION:
    Returns 404 (not 403) when the conversation exists but belongs to a
    different user. This prevents information leakage: the caller cannot
    distinguish "conversation does not exist" from "conversation belongs to
    someone else". Soft-deleted conversations also return 404.

    AUTH:
    X-User-Id header is required. public-api validates the JWT + extracts
    the user_id, then forwards it as X-User-Id (per §E6 internal-rpc-
    contracts). FastAPI maps `x_user_id` parameter to the `x-user-id`
    header (underscore → hyphen, case-insensitive HTTP matching).

    Returns 404 if not found, soft-deleted, or owned by a different user.
    Returns 422 if conversation_id is not a valid UUID.
    Returns 200 + ConversationResponse on success.
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
        # --- 1. Fetch the conversation (active rows only) ----------------
        # WHERE soft_deleted_at IS NULL: soft-deleted conversations behave
        # as if they don't exist — they return 404, same as truly missing rows.
        row = await conn.fetchrow(
            """
            SELECT id, user_id, influencer_id, participant_b_id,
                   conversation_type, last_message_at, message_count
            FROM conversations
            WHERE id = $1
              AND soft_deleted_at IS NULL
            """,
            conv_uuid,
        )

        # --- 2. Tenant isolation check -----------------------------------
        # Return 404 for ALL non-visible cases:
        #   a) row is None: conversation not found or soft-deleted
        #   b) row["user_id"] != x_user_id: conversation exists but belongs
        #      to a different user — must not leak existence (return 404,
        #      never 403).
        if row is None or row["user_id"] != x_user_id:
            raise HTTPException(
                status_code=404,
                detail=f"Conversation {conversation_id} not found",
            )

        # --- 3. Fetch the most-recent non-system message inline ----------
        # Mirrors the LATERAL JOIN pattern in list_conversations_by_user but
        # for a single conversation. ORDER BY created_at DESC, id DESC uses
        # the same tiebreaker as list_messages for same-timestamp batches.
        last_msg_row = await conn.fetchrow(
            """
            SELECT id, conversation_id, role, content, client_message_id,
                   media_urls, created_at, count_toward_paywall
            FROM messages
            WHERE conversation_id = $1
              AND role != 'system'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            conv_uuid,
        )
        last_msg = _row_to_message_response(last_msg_row) if last_msg_row else None

    return _row_to_conversation_response(row, last_msg)


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
            1. inner query: ORDER BY created_at DESC, id DESC LIMIT N —
               selects the N most-recent (or N before the cursor) rows;
               id DESC is a tiebreaker for batch inserts with same timestamp.
            2. outer query: ORDER BY created_at ASC, id ASC — re-orders
               the page chronologically; id ASC matches the ASC direction.
          The messages_by_conversation_time_idx index
          (conversation_id, created_at ASC) supports both scans efficiently.
          The id tiebreaker closes the non-determinism gap: within a
          same-timestamp group (same-transaction batch), rows are always
          returned in the same order across repeated calls.

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
            # id DESC / id ASC tiebreaker ensures deterministic ordering for
            # messages sharing the same created_at (same-transaction batch inserts).
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
                    ORDER BY created_at DESC, id DESC
                    LIMIT $2
                ) sub
                ORDER BY sub.created_at ASC, sub.id ASC
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

            # Subquery resolves the cursor UUID to a (created_at, id) pair;
            # outer WHERE uses a compound row comparison so messages within
            # the SAME timestamp batch are correctly partitioned by id.
            # A created_at-only cursor would silently drop same-timestamp
            # peers that precede the cursor message — compound comparison
            # fixes this. DESC-then-ASC ensures the page is chronological.
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
                      AND (created_at, id) < (
                          SELECT created_at, id
                          FROM messages
                          WHERE id = $2
                      )
                    ORDER BY created_at DESC, id DESC
                    LIMIT $3
                ) sub
                ORDER BY sub.created_at ASC, sub.id ASC
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
#   ../migrations/versions/003_add_dedup_indexes.py
#                                    — unique indexes required by ON CONFLICT
#                                      in create_or_get_conversation +
#                                      append_messages
#   ../../tests/test_conversation_routes.py
#                                    — route-level tests (httpx + testcontainers)
#   ../../../../yral-rishi-agent-conversation-turn-orchestrator/app/run_turn.py
#                                    — primary caller of POST .../messages +
#                                      GET .../messages (context fetch)
#   ../../../../yral-rishi-agent-public-api/app/api/chat_routes.py
#                                    — primary caller of POST /conversations +
#                                      GET /conversations/by-user/{user_id} +
#                                      GET /conversations/{id} (PR-B2)
# ===========================================================================
