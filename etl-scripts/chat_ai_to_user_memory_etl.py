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

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

import asyncpg


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


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


async def open_source_pool(dsn: str) -> asyncpg.Pool:
    """Open a read-only asyncpg pool for the chat-ai source Postgres.

    WHAT: creates an asyncpg connection pool with statement_cache_size=0
          (required for pgBouncer transaction-mode) and server_settings
          that force READ ONLY for the session.
    WHEN: called once at script startup.
    WHY:  READ ONLY ensures we never accidentally mutate the live chat-ai
          DB during the migration window (belt-and-suspenders — A14 safety).
    """
    return await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=4,
        # READ ONLY server-side protection: cannot write to source.
        server_settings={"default_transaction_read_only": "true"},
        # pgBouncer transaction-mode compatibility.
        statement_cache_size=0,
    )


async def open_dest_pool(dsn: str) -> asyncpg.Pool:
    """Open a write asyncpg pool for the v2 user-memory-service destination.

    WHAT: standard asyncpg pool; statement_cache_size=0 for pgBouncer.
    WHEN: called once at script startup.
    WHY:  separate pool from source keeps connection accounting clear and
          ensures a source-side disconnect doesn't affect in-flight writes.
    """
    return await asyncpg.create_pool(
        dsn=dsn,
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
    src: asyncpg.Pool,
    dst: asyncpg.Pool,
    batch_size: int,
    dry_run: bool,
) -> int:
    """Phase 1: migrate all conversations from chat-ai → v2.

    WHAT: reads chat-ai.conversations in batches (ORDER BY created_at ASC
          for deterministic cursor-free pagination), transforms each row,
          and inserts into v2.conversations with ON CONFLICT (id) DO NOTHING.
    WHEN: called as the first migration phase.
    WHY:  conversations must exist BEFORE messages are inserted (FK constraint
          on messages.conversation_id). Batch processing bounds memory usage.

    Returns: total rows inserted (excluding ON CONFLICT skips).
    """
    log.info("Phase 1 — conversations: starting (batch_size=%d, dry_run=%s)",
             batch_size, dry_run)

    # Get total count for progress reporting.
    async with src.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM conversations;")
    log.info("Phase 1 — source has %d conversations total", total)

    inserted_total = 0
    skipped_total = 0
    batch_num = 0
    offset = 0

    while True:
        # Read one batch from source (READ ONLY connection).
        async with src.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, influencer_id, participant_b_id,
                       conversation_type, metadata, created_at, updated_at
                FROM conversations
                ORDER BY created_at ASC, id ASC
                LIMIT $1 OFFSET $2
                """,
                batch_size,
                offset,
            )

        if not rows:
            log.info("Phase 1 — all batches exhausted at offset %d", offset)
            break

        batch_num += 1
        rows_to_insert = [transform_conversation_row(r) for r in rows]

        if dry_run:
            log.info("Phase 1 — batch %d: DRY RUN — would insert %d rows",
                     batch_num, len(rows_to_insert))
        else:
            # Bulk insert via executemany-style loop with ON CONFLICT DO NOTHING.
            # We execute individually (not COPY) so ON CONFLICT is respected per-row.
            inserted_count = 0
            skipped_count = 0

            async with dst.acquire() as conn:
                async with conn.transaction():
                    for r in rows_to_insert:
                        result = await conn.execute(
                            """
                            INSERT INTO conversations (
                                id, user_id, influencer_id, participant_b_id,
                                conversation_type, created_at, last_message_at,
                                message_count, soft_deleted_at
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6, $7, $8, $9
                            )
                            ON CONFLICT (id) DO NOTHING
                            """,
                            r["id"],
                            r["user_id"],
                            r["influencer_id"],
                            r["participant_b_id"],
                            r["conversation_type"],
                            r["created_at"],
                            r["last_message_at"],
                            r["message_count"],
                            r["soft_deleted_at"],
                        )
                        # asyncpg execute() returns "INSERT 0 N" where N is rows affected.
                        # ON CONFLICT DO NOTHING returns N=0 on conflict.
                        if "INSERT 0 1" in result:
                            inserted_count += 1
                        else:
                            skipped_count += 1

            inserted_total += inserted_count
            skipped_total += skipped_count
            log.info(
                "Phase 1 — batch %d (offset %d): inserted=%d skipped=%d "
                "(running total: inserted=%d skipped=%d / %d)",
                batch_num, offset, inserted_count, skipped_count,
                inserted_total, skipped_total, total,
            )

        offset += len(rows)

        # Exit condition: this batch was smaller than batch_size → last batch.
        if len(rows) < batch_size:
            break

    log.info(
        "Phase 1 — conversations DONE: inserted=%d skipped=%d (expected ~%d)",
        inserted_total, skipped_total, total,
    )
    return inserted_total


async def migrate_messages(
    src: asyncpg.Pool,
    dst: asyncpg.Pool,
    batch_size: int,
    dry_run: bool,
) -> int:
    """Phase 2: migrate all messages from chat-ai → v2.

    WHAT: reads chat-ai.messages in batches (ORDER BY created_at ASC for
          deterministic pagination), transforms each row (see §3 of the plan),
          and inserts into v2.messages with ON CONFLICT (id) DO NOTHING.
    WHEN: called AFTER Phase 1 (conversations must exist for FK to pass).
    WHY:  3.3M rows require batch processing to bound memory. ON CONFLICT
          makes re-runs safe.

    Returns: total rows inserted.
    """
    log.info("Phase 2 — messages: starting (batch_size=%d, dry_run=%s)",
             batch_size, dry_run)

    async with src.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM messages;")
    log.info("Phase 2 — source has %d messages total", total)

    inserted_total = 0
    skipped_total = 0
    batch_num = 0
    offset = 0

    while True:
        async with src.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, conversation_id, role, content, media_urls,
                       client_message_id, token_count, created_at
                FROM messages
                ORDER BY created_at ASC, id ASC
                LIMIT $1 OFFSET $2
                """,
                batch_size,
                offset,
            )

        if not rows:
            log.info("Phase 2 — all batches exhausted at offset %d", offset)
            break

        batch_num += 1
        rows_to_insert = [transform_message_row(r) for r in rows]

        if dry_run:
            log.info("Phase 2 — batch %d: DRY RUN — would insert %d rows",
                     batch_num, len(rows_to_insert))
        else:
            inserted_count = 0
            skipped_count = 0

            async with dst.acquire() as conn:
                async with conn.transaction():
                    for r in rows_to_insert:
                        result = await conn.execute(
                            """
                            INSERT INTO messages (
                                id, conversation_id, role, content,
                                media_urls, gemini_metadata,
                                client_message_id, created_at,
                                count_toward_paywall
                            ) VALUES (
                                $1, $2, $3, $4,
                                $5::jsonb, $6::jsonb,
                                $7, $8,
                                $9
                            )
                            ON CONFLICT (id) DO NOTHING
                            """,
                            r["id"],
                            r["conversation_id"],
                            r["role"],
                            r["content"],
                            r["media_urls"],
                            r["gemini_metadata"],
                            r["client_message_id"],
                            r["created_at"],
                            r["count_toward_paywall"],
                        )
                        if "INSERT 0 1" in result:
                            inserted_count += 1
                        else:
                            skipped_count += 1

            inserted_total += inserted_count
            skipped_total += skipped_count

            # Progress log every 10 batches (every 100K rows at default batch size)
            if batch_num % 10 == 0:
                log.info(
                    "Phase 2 — batch %d (offset %d): inserted=%d skipped=%d "
                    "(running total: inserted=%d / %d)",
                    batch_num, offset, inserted_count, skipped_count,
                    inserted_total, total,
                )

        offset += len(rows)

        if len(rows) < batch_size:
            break

    log.info(
        "Phase 2 — messages DONE: inserted=%d skipped=%d (expected ~%d)",
        inserted_total, skipped_total, total,
    )
    return inserted_total


async def update_message_counts(
    dst: asyncpg.Pool,
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
        async with dst.acquire() as conn:
            affected = await conn.fetchval(
                "SELECT count(*) FROM conversations WHERE message_count = 0;"
            )
        log.info("Phase 3 — DRY RUN: would update message_count on %d conversations",
                 affected)
        return

    async with dst.acquire() as conn:
        result = await conn.execute(
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


async def run_verification(src: asyncpg.Pool, dst: asyncpg.Pool) -> None:
    """Print post-ETL verification counts side by side.

    WHAT: queries count(*) on both source + destination for conversations
          and messages; prints a comparison table.
    WHEN: called after all 3 phases complete.
    WHY:  the coordinator uses this output to confirm the migration was
          complete before updating the RUNBOOK + logging the pull in
          live-data-pulls-log.md (per A4 verification requirement).
    """
    log.info("Verification — running count comparison")

    async with src.acquire() as conn:
        src_convs = await conn.fetchval("SELECT count(*) FROM conversations;")
        src_msgs = await conn.fetchval("SELECT count(*) FROM messages;")

    async with dst.acquire() as conn:
        dst_convs = await conn.fetchval("SELECT count(*) FROM conversations;")
        dst_msgs = await conn.fetchval("SELECT count(*) FROM messages;")

    conv_delta = dst_convs - src_convs
    msg_delta = dst_msgs - src_msgs

    # Positive delta = v2 has MORE rows (live traffic created new ones during ETL)
    # Negative delta = v2 has FEWER rows (some failed to import — investigate)
    conv_status = "OK" if abs(conv_delta) <= 500 else "⚠️  LARGE DELTA — INVESTIGATE"
    msg_status = "OK" if abs(msg_delta) <= 5000 else "⚠️  LARGE DELTA — INVESTIGATE"

    print("\n" + "=" * 60)
    print("ETL VERIFICATION REPORT")
    print(f"  Run at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    print(f"  conversations:")
    print(f"    chat-ai (source) : {src_convs:>10,}")
    print(f"    v2 user-memory   : {dst_convs:>10,}")
    print(f"    delta            : {conv_delta:>+10,}  [{conv_status}]")
    print(f"  messages:")
    print(f"    chat-ai (source) : {src_msgs:>10,}")
    print(f"    v2 user-memory   : {dst_msgs:>10,}")
    print(f"    delta            : {msg_delta:>+10,}  [{msg_status}]")
    print("=" * 60 + "\n")

    if "INVESTIGATE" in conv_status or "INVESTIGATE" in msg_status:
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
    src_dsn = os.environ.get("CHAT_AI_POSTGRES_URL")
    dst_dsn = os.environ.get("POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE")

    if not src_dsn:
        log.error("CHAT_AI_POSTGRES_URL not set. Set it and re-run.")
        sys.exit(1)
    if not dst_dsn:
        log.error("POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE not set. Set it and re-run.")
        sys.exit(1)

    log.info("A14 reminder: this ETL requires explicit Rishi YES. "
             "Coordinator: confirm YES was received before proceeding.")
    if dry_run:
        log.info("DRY RUN MODE — no writes will be made to the destination DB")

    # --- 1. Open pools ----------------------------------------------------
    log.info("Opening source connection pool (READ ONLY)...")
    src_pool = await open_source_pool(src_dsn)

    log.info("Opening destination connection pool...")
    dst_pool = await open_dest_pool(dst_dsn)

    try:
        # --- 2. Run migration phases ------------------------------------
        if not messages_only:
            await migrate_conversations(src_pool, dst_pool, batch_size, dry_run)

        if not conversations_only:
            await migrate_messages(src_pool, dst_pool, batch_size, dry_run)

        if not conversations_only and not dry_run:
            await update_message_counts(dst_pool, dry_run)

        # --- 3. Verify --------------------------------------------------
        if not skip_verification and not dry_run:
            await run_verification(src_pool, dst_pool)
        elif dry_run:
            log.info("Skipping verification (dry-run mode)")

    finally:
        await src_pool.close()
        await dst_pool.close()


def cli() -> None:
    """Parse command-line arguments and run the ETL."""
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

    args = parser.parse_args()

    if args.conversations_only and args.messages_only:
        parser.error("--conversations-only and --messages-only are mutually exclusive")

    asyncio.run(
        main(
            batch_size=args.batch_size,
            conversations_only=args.conversations_only,
            messages_only=args.messages_only,
            dry_run=args.dry_run,
            skip_verification=args.skip_verification,
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
#     cross-session-dependencies.md     — DEP-014: coordinator runs this under YES
#   ../yral-rishi-agent-plan-and-discussions/
#     running-coordination-asks-plus-mobile-team-memo-and-change-log/
#     live-data-pulls-log.md             — log the pull here after execution
# ===========================================================================
