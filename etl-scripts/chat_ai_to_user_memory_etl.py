#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# chat_ai_to_user_memory_etl.py — one-off data migration from chat-ai
#   (v1 Python monolith) to yral-rishi-agent-user-memory-service (v2).
#
# ⭐ START HERE: this script ports ~284K conversations + ~3.3M messages from
#   chat-ai's Postgres to the v2 user-memory-service Postgres.
#
# 🚨 A14 GATE: Do NOT run this script without explicit Rishi YES.
#   See etl-plan-day-9-draft.md §9 for the exact approval checklist.
#   Coordinator surfaces the checklist + waits for "YES" before executing.
#
# USAGE:
#   python3 chat_ai_to_user_memory_etl.py \
#     --batch-size 10000 \
#     --conversations-only      # optional: skip messages (phase 1 of 2)
#     --messages-only           # optional: skip conversations (phase 2 of 2)
#     --dry-run                 # print counts, don't write to v2
#
# ENVIRONMENT VARIABLES REQUIRED:
#   CHAT_AI_POSTGRES_URL                          — READ-ONLY chat-ai source
#   POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE — WRITE v2 destination
#
# COLUMN MAPPING:
#   See etl-plan-day-9-draft.md §2 (conversations) and §3 (messages).
#
# IDEMPOTENCY:
#   Both INSERTs use ON CONFLICT (id) DO NOTHING. Safe to re-run.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# Standard-library imports — all built into Python 3.12, no install required.
import argparse        # CLI argument parsing + mutual-exclusivity validation
import asyncio         # runs async ETL phases from the synchronous cli() entry point
import json            # serialises JSONB fields for asyncpg $N::jsonb parameters
import logging         # structured INFO/ERROR log; content is NEVER logged (PII safety)
import os              # reads connection strings from environment (not CLI args — ps-aux safety)
import sys             # sys.exit(1) on verification failure; sys.stdout for log stream
import uuid            # nil UUID as keyset-pagination starting cursor
from datetime import datetime, timezone  # UTC timestamp + epoch as keyset-pagination cursor

# Third-party — must be installed in the ETL runner's virtualenv.
import asyncpg         # async Postgres driver; no ORM per F12 directive


# ---------------------------------------------------------------------------
# Logging — structured plain-text log. Does NOT log message content (PII).
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("etl")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default rows per SELECT + INSERT batch.
# 10K balances RAM pressure against Postgres round-trips.
DEFAULT_BATCH_SIZE = 10_000

# Keyset-pagination starting cursors.
# Keyset pagination replaces LIMIT/OFFSET to avoid the O(n²) full-table scan
# that OFFSET causes on large tables (3.3M messages = ~millions of re-scanned
# rows per page). The initial cursor values are guaranteed to precede every
# real row so the first batch captures all rows.
_ETL_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)  # no chat-ai row predates 1970
_UUID_MIN = uuid.UUID("00000000-0000-0000-0000-000000000000")  # nil UUID sorts first


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


async def open_source_pool(database_connection_string: str) -> asyncpg.Pool:
    """Open a read-only asyncpg pool for the chat-ai source Postgres.

    WHAT: creates an asyncpg connection pool with statement_cache_size=0
          (required for pgBouncer transaction-mode) and server_settings
          that force READ ONLY for the session.
    WHEN: called once at script startup.
    WHY:  READ ONLY ensures we never accidentally mutate the live chat-ai
          DB during the migration window (belt-and-suspenders — A14 safety).
    """
    return await asyncpg.create_pool(
        database_connection_string,  # positional: asyncpg's first param is the connection string
        min_size=1,
        max_size=4,
        # READ ONLY server-side protection: cannot write to source.
        server_settings={"default_transaction_read_only": "true"},
        # pgBouncer transaction-mode compatibility.
        statement_cache_size=0,
    )


async def open_destination_pool(database_connection_string: str) -> asyncpg.Pool:
    """Open a write asyncpg pool for the v2 user-memory-service destination.

    WHAT: standard asyncpg pool; statement_cache_size=0 for pgBouncer.
    WHEN: called once at script startup.
    WHY:  separate pool from source keeps connection accounting clear and
          ensures a source-side disconnect doesn't affect in-flight writes.
    """
    return await asyncpg.create_pool(
        database_connection_string,  # positional: asyncpg's first param is the connection string
        min_size=1,
        max_size=4,
        statement_cache_size=0,
    )


# ---------------------------------------------------------------------------
# Transform helpers
# ---------------------------------------------------------------------------


def transform_conversation_row(row: asyncpg.Record) -> dict:
    """Map one chat-ai conversations row to the v2 column set.

    WHAT: applies the column mapping from etl-plan-day-9-draft.md §2.
          Drops: metadata (Phase 2), adds: soft_deleted_at=NULL.
          Renames: updated_at → last_message_at.
    WHEN: called once per row during the conversations migration phase.
    WHY:  centralises all transform logic so the INSERT statement below
          stays clean. If a new v2 column needs a default, add it here.
    """
    # Drop: metadata (JSONB memories — Phase 2 pgvector rebuild)
    # We log a warning so the coordinator can see which rows had memories.
    metadata = row.get("metadata")
    if metadata is not None:
        memories = metadata.get("memories") if isinstance(metadata, dict) else None
        if memories:
            log.debug(
                "conversation %s has memories (will not be migrated in Phase 1)",
                row["id"],
            )

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "influencer_id": row.get("influencer_id"),        # nullable: ai_chat
        "participant_b_id": row.get("participant_b_id"),  # nullable: human_chat
        "conversation_type": row["conversation_type"],    # 'ai_chat' | 'human_chat'
        # Rename: updated_at (auto-updated by trigger on msg insert) → last_message_at
        "last_message_at": row["updated_at"],
        "created_at": row["created_at"],
        # message_count: filled to 0 now, updated in Phase 3 bulk UPDATE
        "message_count": 0,
        # soft_deleted_at: NULL for all migrated rows (chat-ai hard-deletes)
        "soft_deleted_at": None,
    }


def transform_message_row(row: asyncpg.Record) -> dict:
    """Map one chat-ai messages row to the v2 column set.

    WHAT: applies the column mapping from etl-plan-day-9-draft.md §3.
          Drops: sender_id, message_type, audio_url, audio_duration_seconds,
                 is_read, status, metadata.
          Transforms: token_count → gemini_metadata JSONB.
          Defaults: count_toward_paywall = True.
    WHEN: called once per row during the messages migration phase.
    WHY:  single transform function = single place to update if the v2
          schema gains or loses a column.
    """
    # Transform: token_count → gemini_metadata JSONB
    token_count = row.get("token_count")
    gemini_metadata = (
        json.dumps({"total_tokens": token_count})
        if token_count is not None
        else None
    )

    # Coerce NULL content to '' (v2 has NOT NULL constraint on messages.content)
    content = row.get("content") or ""

    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],               # 'user' | 'assistant' (no 'system' in chat-ai)
        "content": content,
        "media_urls": _serialize_jsonb(row.get("media_urls")),
        "gemini_metadata": gemini_metadata,
        "client_message_id": row.get("client_message_id"),
        "created_at": row["created_at"],
        # Default TRUE: conservative fail-safe per E7; all historical messages
        # count toward paywall (we cannot retroactively know which were auto-greets).
        "count_toward_paywall": True,
        # Dropped: sender_id, message_type, audio_url, audio_duration_seconds,
        #          is_read, status, metadata
    }


def _serialize_jsonb(value) -> str | None:
    """Serialize a JSONB value to a JSON string for asyncpg INSERT.

    WHAT: asyncpg requires explicit JSON strings when inserting JSONB with
          $N::jsonb syntax (rather than relying on the codec). Handles both
          Python objects (already decoded by asyncpg's jsonb codec on the
          source connection) and raw strings.
    WHEN: called for media_urls column during message transform.
    WHY:  prevents "type mismatch" errors when the source asyncpg codec is
          configured differently from the destination's codec registration.
    """
    if value is None:
        return None
    if isinstance(value, str):
        # Already a JSON string (some asyncpg configurations return raw text)
        return value
    # Python object (list, dict, etc.) — serialize it
    return json.dumps(value)


# ---------------------------------------------------------------------------
# Migration phases
# ---------------------------------------------------------------------------


async def migrate_conversations(
    source: asyncpg.Pool,
    destination: asyncpg.Pool,
    batch_size: int,
    dry_run: bool,
) -> int:
    """Phase 1: migrate all conversations from chat-ai → v2.

    WHAT: reads chat-ai.conversations in batches using keyset pagination on
          (created_at, id) — O(n) vs LIMIT/OFFSET's O(n²) for large tables.
          Each batch is bulk-loaded into a temp staging table via asyncpg's
          binary COPY protocol (~50× faster than per-row INSERTs), then
          atomically upserted into v2.conversations with ON CONFLICT (id) DO NOTHING.
          If the bulk INSERT SELECT hits a CheckViolationError (bad row), the
          batch retries row-by-row so only the offending row is skipped (logged).
    WHEN: called as the first migration phase.
    WHY:  conversations must exist BEFORE messages (FK constraint). Keyset read
          prevents source DB overload on 284K rows. A single destination
          connection is held for the entire phase so the TEMP TABLE (session-
          scoped in PostgreSQL) is visible across all batches.

    Returns: total rows inserted (excluding ON CONFLICT skips).
    """
    log.info("Phase 1 — conversations: starting (batch_size=%d, dry_run=%s)",
             batch_size, dry_run)

    async with source.acquire() as source_connection:
        total = await source_connection.fetchval("SELECT count(*) FROM conversations;")
    log.info("Phase 1 — source has %d conversations total", total)

    inserted_total = 0
    batch_number = 0
    # Keyset cursor: start before all real rows (epoch timestamp + nil UUID).
    cursor_timestamp = _ETL_EPOCH
    cursor_id = _UUID_MIN

    # Hold ONE destination connection for the entire phase.
    # PostgreSQL TEMP TABLEs are session-scoped — a new acquire() would return
    # a different connection without the staging table.
    async with destination.acquire() as destination_connection:
        if not dry_run:
            # Create staging table once; reuse across all batches via TRUNCATE.
            await destination_connection.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS conversations_staging (
                    id UUID NOT NULL,
                    user_id TEXT NOT NULL,
                    influencer_id TEXT,
                    participant_b_id TEXT,
                    conversation_type TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    last_message_at TIMESTAMPTZ NOT NULL,
                    message_count INT NOT NULL,
                    soft_deleted_at TIMESTAMPTZ
                )
                """
            )

        while True:
            # Keyset read — no OFFSET, so the source DB scans only new rows.
            async with source.acquire() as source_connection:
                rows = await source_connection.fetch(
                    """
                    SELECT id, user_id, influencer_id, participant_b_id,
                           conversation_type, metadata, created_at, updated_at
                    FROM conversations
                    WHERE (created_at, id) > ($1, $2)
                    ORDER BY created_at ASC, id ASC
                    LIMIT $3
                    """,
                    cursor_timestamp,
                    cursor_id,
                    batch_size,
                )

            if not rows:
                log.info("Phase 1 — keyset cursor exhausted after batch %d", batch_number)
                break

            batch_number += 1
            rows_to_insert = [transform_conversation_row(r) for r in rows]

            if dry_run:
                log.info("Phase 1 — batch %d: DRY RUN — would insert %d rows",
                         batch_number, len(rows_to_insert))
            else:
                # TRUNCATE staging → COPY batch (binary protocol) → INSERT SELECT.
                # Each step auto-commits; ON CONFLICT makes the whole pipeline
                # idempotent — a crash + restart re-runs from cursor_timestamp = _ETL_EPOCH
                # and skips already-loaded rows.
                await destination_connection.execute("TRUNCATE conversations_staging")
                await destination_connection.copy_records_to_table(
                    "conversations_staging",
                    records=[
                        (
                            r["id"], r["user_id"], r["influencer_id"],
                            r["participant_b_id"], r["conversation_type"],
                            r["created_at"], r["last_message_at"],
                            r["message_count"], r["soft_deleted_at"],
                        )
                        for r in rows_to_insert
                    ],
                    columns=[
                        "id", "user_id", "influencer_id", "participant_b_id",
                        "conversation_type", "created_at", "last_message_at",
                        "message_count", "soft_deleted_at",
                    ],
                )

                try:
                    result = await destination_connection.execute(
                        """
                        INSERT INTO conversations (
                            id, user_id, influencer_id, participant_b_id,
                            conversation_type, created_at, last_message_at,
                            message_count, soft_deleted_at
                        )
                        SELECT id, user_id, influencer_id, participant_b_id,
                               conversation_type, created_at, last_message_at,
                               message_count, soft_deleted_at
                        FROM conversations_staging
                        ON CONFLICT (id) DO NOTHING
                        """
                    )
                    # asyncpg returns "INSERT 0 N" — N is the count of new rows.
                    inserted_batch = int(result.split()[-1])

                except asyncpg.CheckViolationError as violation:
                    # Bulk INSERT SELECT hit a CHECK constraint — one or more rows have
                    # an invalid conversation_type or other constrained value. Fall back
                    # to per-row inserts so only the offending rows are skipped. The
                    # staging table data is intact (the failed INSERT SELECT did not
                    # commit any rows), so we can re-insert from rows_to_insert directly.
                    log.warning(
                        "Phase 1 — batch %d: bulk INSERT SELECT hit CHECK constraint %r; "
                        "retrying row-by-row — bad rows will be logged and skipped",
                        batch_number, violation.constraint_name,
                    )
                    inserted_batch = 0
                    for row in rows_to_insert:
                        try:
                            row_result = await destination_connection.execute(
                                """
                                INSERT INTO conversations (
                                    id, user_id, influencer_id, participant_b_id,
                                    conversation_type, created_at, last_message_at,
                                    message_count, soft_deleted_at
                                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                                ON CONFLICT (id) DO NOTHING
                                """,
                                row["id"], row["user_id"], row["influencer_id"],
                                row["participant_b_id"], row["conversation_type"],
                                row["created_at"], row["last_message_at"],
                                row["message_count"], row["soft_deleted_at"],
                            )
                            if row_result.split()[-1] != "0":
                                inserted_batch += 1
                        except asyncpg.CheckViolationError as row_violation:
                            log.warning(
                                "Phase 1 — batch %d: SKIPPING conversation %s "
                                "(CHECK constraint %r violated — see plan §7 recovery)",
                                batch_number, row["id"], row_violation.constraint_name,
                            )

                inserted_total += inserted_batch
                log.info(
                    "Phase 1 — batch %d: inserted=%d skipped=%d "
                    "(running total inserted=%d / source=%d)",
                    batch_number, inserted_batch, len(rows_to_insert) - inserted_batch,
                    inserted_total, total,
                )

            # Advance keyset cursor to the last row of this batch.
            cursor_timestamp = rows[-1]["created_at"]
            cursor_id = rows[-1]["id"]

            # Exit when this batch was smaller than batch_size → no more rows.
            if len(rows) < batch_size:
                break

    log.info("Phase 1 — conversations DONE: inserted=%d (expected ~%d)",
             inserted_total, total)
    return inserted_total


async def migrate_messages(
    source: asyncpg.Pool,
    destination: asyncpg.Pool,
    batch_size: int,
    dry_run: bool,
) -> int:
    """Phase 2: migrate all messages from chat-ai → v2.

    WHAT: reads chat-ai.messages in batches using keyset pagination on
          (created_at, id) — O(n) vs LIMIT/OFFSET's O(n²) for large tables.
          Each batch is bulk-loaded into a temp staging table via asyncpg's
          binary COPY protocol (~50× faster than per-row INSERTs), then
          atomically upserted into v2.messages with ON CONFLICT (id) DO NOTHING.
          JSONB columns (media_urls, gemini_metadata) are stored as TEXT in the
          staging table and cast to JSONB in the INSERT SELECT — COPY's binary
          protocol does not natively encode Postgres JSONB.
          If the bulk INSERT SELECT hits a CheckViolationError (bad row), the
          batch retries row-by-row so only the offending row is skipped (logged).
    WHEN: called AFTER Phase 1 (conversations must exist before messages: FK
          constraint on messages.conversation_id).
    WHY:  3.3M messages × OFFSET re-scan = O(n²) on the source DB. Keyset
          pagination eliminates the re-scan: each page starts exactly where the
          last left off. A single destination connection is held for the entire
          phase so the TEMP TABLE (session-scoped in PostgreSQL) is visible
          across all batches.

    Returns: total rows inserted (excluding ON CONFLICT skips).
    """
    log.info("Phase 2 — messages: starting (batch_size=%d, dry_run=%s)",
             batch_size, dry_run)

    async with source.acquire() as source_connection:
        total = await source_connection.fetchval("SELECT count(*) FROM messages;")
    log.info("Phase 2 — source has %d messages total", total)

    inserted_total = 0
    batch_number = 0
    # Keyset cursor: start before all real rows (epoch timestamp + nil UUID).
    cursor_timestamp = _ETL_EPOCH
    cursor_id = _UUID_MIN

    # Hold ONE destination connection for the entire phase.
    # PostgreSQL TEMP TABLEs are session-scoped — a new acquire() would return
    # a different connection without the staging table.
    async with destination.acquire() as destination_connection:
        if not dry_run:
            # media_urls + gemini_metadata stored as TEXT in staging; cast to
            # JSONB in the INSERT SELECT. COPY's binary protocol cannot encode
            # the Postgres JSONB wire format directly — TEXT is the safe bridge.
            await destination_connection.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS messages_staging (
                    id UUID NOT NULL,
                    conversation_id UUID NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    media_urls TEXT,
                    gemini_metadata TEXT,
                    client_message_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    count_toward_paywall BOOLEAN NOT NULL
                )
                """
            )

        while True:
            # Keyset read — no OFFSET, so the source DB scans only new rows.
            async with source.acquire() as source_connection:
                rows = await source_connection.fetch(
                    """
                    SELECT id, conversation_id, role, content, media_urls,
                           client_message_id, token_count, created_at
                    FROM messages
                    WHERE (created_at, id) > ($1, $2)
                    ORDER BY created_at ASC, id ASC
                    LIMIT $3
                    """,
                    cursor_timestamp,
                    cursor_id,
                    batch_size,
                )

            if not rows:
                log.info("Phase 2 — keyset cursor exhausted after batch %d", batch_number)
                break

            batch_number += 1
            rows_to_insert = [transform_message_row(r) for r in rows]

            if dry_run:
                log.info("Phase 2 — batch %d: DRY RUN — would insert %d rows",
                         batch_number, len(rows_to_insert))
            else:
                # TRUNCATE staging → COPY batch (binary protocol) → INSERT SELECT.
                # ON CONFLICT makes the whole pipeline idempotent — a crash +
                # restart re-runs from cursor_timestamp = _ETL_EPOCH and skips
                # already-loaded rows.
                await destination_connection.execute("TRUNCATE messages_staging")
                await destination_connection.copy_records_to_table(
                    "messages_staging",
                    records=[
                        (
                            r["id"], r["conversation_id"], r["role"],
                            r["content"], r["media_urls"], r["gemini_metadata"],
                            r["client_message_id"], r["created_at"],
                            r["count_toward_paywall"],
                        )
                        for r in rows_to_insert
                    ],
                    columns=[
                        "id", "conversation_id", "role", "content",
                        "media_urls", "gemini_metadata", "client_message_id",
                        "created_at", "count_toward_paywall",
                    ],
                )

                try:
                    result = await destination_connection.execute(
                        """
                        INSERT INTO messages (
                            id, conversation_id, role, content,
                            media_urls, gemini_metadata,
                            client_message_id, created_at,
                            count_toward_paywall
                        )
                        SELECT
                            id, conversation_id, role, content,
                            media_urls::jsonb, gemini_metadata::jsonb,
                            client_message_id, created_at,
                            count_toward_paywall
                        FROM messages_staging
                        ON CONFLICT (id) DO NOTHING
                        """
                    )
                    # asyncpg returns "INSERT 0 N" — N is the count of new rows.
                    inserted_batch = int(result.split()[-1])

                except asyncpg.CheckViolationError as violation:
                    # Bulk INSERT SELECT hit a CHECK constraint — one or more rows have
                    # an invalid role value or other constrained column. Fall back to
                    # per-row inserts so only the offending rows are skipped. The
                    # staging table data is intact (failed INSERT SELECT committed nothing).
                    log.warning(
                        "Phase 2 — batch %d: bulk INSERT SELECT hit CHECK constraint %r; "
                        "retrying row-by-row — bad rows will be logged and skipped",
                        batch_number, violation.constraint_name,
                    )
                    inserted_batch = 0
                    for row in rows_to_insert:
                        try:
                            row_result = await destination_connection.execute(
                                """
                                INSERT INTO messages (
                                    id, conversation_id, role, content,
                                    media_urls, gemini_metadata,
                                    client_message_id, created_at,
                                    count_toward_paywall
                                ) VALUES (
                                    $1, $2, $3, $4,
                                    $5::jsonb, $6::jsonb,
                                    $7, $8, $9
                                )
                                ON CONFLICT (id) DO NOTHING
                                """,
                                row["id"], row["conversation_id"],
                                row["role"], row["content"],
                                row["media_urls"], row["gemini_metadata"],
                                row["client_message_id"], row["created_at"],
                                row["count_toward_paywall"],
                            )
                            if row_result.split()[-1] != "0":
                                inserted_batch += 1
                        except asyncpg.CheckViolationError as row_violation:
                            log.warning(
                                "Phase 2 — batch %d: SKIPPING message %s "
                                "(CHECK constraint %r violated — see plan §7 recovery)",
                                batch_number, row["id"], row_violation.constraint_name,
                            )

                inserted_total += inserted_batch
                # Progress log every 10 batches (every 100K rows at default size).
                if batch_number % 10 == 0:
                    log.info(
                        "Phase 2 — batch %d: inserted=%d skipped=%d "
                        "(running total inserted=%d / source=%d)",
                        batch_number, inserted_batch,
                        len(rows_to_insert) - inserted_batch,
                        inserted_total, total,
                    )

            # Advance keyset cursor to the last row of this batch.
            cursor_timestamp = rows[-1]["created_at"]
            cursor_id = rows[-1]["id"]

            # Exit when this batch was smaller than batch_size → no more rows.
            if len(rows) < batch_size:
                break

    log.info("Phase 2 — messages DONE: inserted=%d (expected ~%d)",
             inserted_total, total)
    return inserted_total


async def update_message_counts(
    destination: asyncpg.Pool,
    dry_run: bool,
) -> None:
    """Phase 3: set message_count on each conversation.

    WHAT: bulk UPDATE sets conversations.message_count = actual count of
          messages in the messages table WHERE message_count = 0 (to avoid
          overwriting counts set by live traffic during the migration window).
    WHEN: called after Phase 2 completes (all messages loaded).
    WHY:  conversations were inserted with message_count=0 in Phase 1. The
          correct count is now computable from the messages table. Restricting
          to WHERE message_count = 0 is safe: live v2 traffic that has
          already set a non-zero count must not be reset.
    """
    log.info("Phase 3 — message_count update: starting (dry_run=%s)", dry_run)

    if dry_run:
        async with destination.acquire() as connection:
            affected = await connection.fetchval(
                "SELECT count(*) FROM conversations WHERE message_count = 0;"
            )
        log.info("Phase 3 — DRY RUN: would update message_count on %d conversations",
                 affected)
        return

    async with destination.acquire() as connection:
        result = await connection.execute(
            """
            UPDATE conversations c
            SET message_count = (
                SELECT count(*)
                FROM messages
                WHERE conversation_id = c.id
            )
            WHERE c.message_count = 0
            """
        )
    log.info("Phase 3 — message_count update DONE: %s", result)


async def run_verification(source: asyncpg.Pool, destination: asyncpg.Pool) -> None:
    """Print post-ETL verification counts side by side.

    WHAT: queries count(*) on both source + destination for conversations
          and messages; prints a comparison table.
    WHEN: called after all 3 phases complete.
    WHY:  the coordinator uses this output to confirm the migration was
          complete before updating the RUNBOOK + logging the pull in
          live-data-pulls-log.md (per A4 verification requirement).
    """
    log.info("Verification — running count comparison")

    async with source.acquire() as connection:
        source_conversations = await connection.fetchval("SELECT count(*) FROM conversations;")
        source_messages = await connection.fetchval("SELECT count(*) FROM messages;")

    async with destination.acquire() as connection:
        destination_conversations = await connection.fetchval("SELECT count(*) FROM conversations;")
        destination_messages = await connection.fetchval("SELECT count(*) FROM messages;")

    conversations_delta = destination_conversations - source_conversations
    messages_delta = destination_messages - source_messages

    # Positive delta = v2 has MORE rows (live traffic created new ones during ETL)
    # Negative delta = v2 has FEWER rows (some failed to import — investigate)
    conversations_status = (
        "OK" if abs(conversations_delta) <= 500
        else "⚠️  LARGE DELTA — INVESTIGATE"
    )
    messages_status = (
        "OK" if abs(messages_delta) <= 5000
        else "⚠️  LARGE DELTA — INVESTIGATE"
    )

    print("\n" + "=" * 60)
    print("ETL VERIFICATION REPORT")
    print(f"  Run at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    print(f"  conversations:")
    print(f"    chat-ai (source) : {source_conversations:>10,}")
    print(f"    v2 user-memory   : {destination_conversations:>10,}")
    print(f"    delta            : {conversations_delta:>+10,}  [{conversations_status}]")
    print(f"  messages:")
    print(f"    chat-ai (source) : {source_messages:>10,}")
    print(f"    v2 user-memory   : {destination_messages:>10,}")
    print(f"    delta            : {messages_delta:>+10,}  [{messages_status}]")
    print("=" * 60 + "\n")

    if "INVESTIGATE" in conversations_status or "INVESTIGATE" in messages_status:
        log.error(
            "Verification FAILED: delta exceeds tolerance threshold. "
            "See etl-plan-day-9-draft.md §6 for recovery steps."
        )
        sys.exit(1)
    else:
        log.info("Verification PASSED: counts within tolerance. ETL complete.")


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------


async def main(
    batch_size: int,
    conversations_only: bool,
    messages_only: bool,
    dry_run: bool,
    skip_verification: bool,
) -> None:
    """Run the full ETL migration.

    WHAT: orchestrates Phase 1 (conversations), Phase 2 (messages), Phase 3
          (message_count UPDATE), and the verification count comparison.
    WHEN: invoked by the coordinator under Rishi YES per A14.
    WHY:  single entry point keeps the ETL auditable — coordinator can see
          exactly what ran in what order from the log output.
    """
    # --- 0. Read connection strings from environment ----------------------
    # Connection strings are env vars (NOT CLI args) so they don't appear
    # in `ps aux` output on shared machines. See etl-plan-day-9-draft.md §8.
    source_database_connection_string = os.environ.get("CHAT_AI_POSTGRES_URL")
    destination_database_connection_string = os.environ.get(
        "POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE"
    )

    if not source_database_connection_string:
        log.error("CHAT_AI_POSTGRES_URL not set. Set it and re-run.")
        sys.exit(1)
    if not destination_database_connection_string:
        log.error("POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE not set. Set it and re-run.")
        sys.exit(1)

    log.info("A14 reminder: this ETL requires explicit Rishi YES. "
             "Coordinator: confirm YES was received before proceeding.")
    if dry_run:
        log.info("DRY RUN MODE — no writes will be made to the destination DB")

    # --- 1. Open pools ----------------------------------------------------
    log.info("Opening source connection pool (READ ONLY)...")
    source_pool = await open_source_pool(source_database_connection_string)

    log.info("Opening destination connection pool...")
    destination_pool = await open_destination_pool(destination_database_connection_string)

    try:
        # --- 2. Run migration phases ------------------------------------
        if not messages_only:
            await migrate_conversations(source_pool, destination_pool, batch_size, dry_run)

        if not conversations_only:
            await migrate_messages(source_pool, destination_pool, batch_size, dry_run)

        if not conversations_only and not dry_run:
            await update_message_counts(destination_pool, dry_run)

        # --- 3. Verify --------------------------------------------------
        if not skip_verification and not dry_run:
            await run_verification(source_pool, destination_pool)
        elif dry_run:
            log.info("Skipping verification (dry-run mode)")

    finally:
        await source_pool.close()
        await destination_pool.close()


def cli() -> None:
    """WHAT: parse CLI arguments via argparse, validate mutual exclusivity of
             phase-skip flags, and delegate to `main()` via asyncio.run().
    WHEN: invoked when the script is run directly:
             python3 chat_ai_to_user_memory_etl.py [flags]
          Also the setuptools console_scripts entry point if the script is
          ever installed as a package (not current, but supported by design).
    WHY:  synchronous argparse must run before the asyncio event loop starts;
          asyncio.run() then creates a fresh loop, runs `main()` to completion,
          and closes the loop. Separating CLI parsing from async orchestration
          keeps each function testable in isolation — tests can call
          `transform_conversation_row()` or `run_verification()` directly
          without spawning a subprocess or invoking the full argument parser.
          argparse `parser.error()` exits with code 2 on validation failure
          (including the --conversations-only / --messages-only mutual-exclusivity
          check) — standard POSIX CLI contract for argument errors.
    """
    parser = argparse.ArgumentParser(
        description=(
            "chat-ai → user-memory-service ETL migration. "
            "Requires CHAT_AI_POSTGRES_URL + "
            "POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE env vars."
        )
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows per SELECT+INSERT batch (default {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--conversations-only",
        action="store_true",
        help="Run Phase 1 only (skip messages + message_count update)",
    )
    parser.add_argument(
        "--messages-only",
        action="store_true",
        help="Run Phase 2 + 3 only (skip conversations)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row counts + transforms but do NOT write to the destination DB",
    )
    parser.add_argument(
        "--skip-verification",
        action="store_true",
        help="Skip the post-ETL count comparison (useful during testing)",
    )

    parsed = parser.parse_args()

    if parsed.conversations_only and parsed.messages_only:
        parser.error("--conversations-only and --messages-only are mutually exclusive")

    asyncio.run(
        main(
            batch_size=parsed.batch_size,
            conversations_only=parsed.conversations_only,
            messages_only=parsed.messages_only,
            dry_run=parsed.dry_run,
            skip_verification=parsed.skip_verification,
        )
    )


if __name__ == "__main__":
    cli()


# ===========================================================================
# RELATED FILES:
#   etl-plan-day-9-draft.md            — column mapping doc + approval checklist
#   ../yral-rishi-agent-user-memory-service/app/migrations/versions/
#                                        — schema the destination DB must have
#   ../yral-rishi-agent-user-memory-service/RUNBOOK.md
#                                        — ETL runbook section (§ ETL Day-9)
#   ../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/
#     cross-session-dependencies.md     — DEP-015: coordinator runs this under YES
#   ../yral-rishi-agent-plan-and-discussions/
#     running-coordination-asks-plus-mobile-team-memo-and-change-log/
#     live-data-pulls-log.md             — log the pull here after execution
# ===========================================================================
