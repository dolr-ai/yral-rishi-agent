"""Continuous incremental ETL from chat-ai (read-only) to v2.

Reads `CHAT_AI_DATABASE_URL` from env. If unset, the background loop logs
once and stays idle — operator sets the env var via swarm service update
to enable the ETL without a code deploy.

Pull strategy per table:
  1. Read last_sync_ts from etl_sync_state
  2. Connect to chat-ai (read-only)
  3. SELECT * FROM <table> WHERE created_at > $1 ORDER BY created_at LIMIT page_size
  4. Batch-INSERT into v2 with ON CONFLICT (id) DO NOTHING — idempotent
  5. Advance the cursor to the max created_at observed in the batch
  6. Repeat until empty page or until SAFETY_BATCH_LIMIT batches consumed in
     a single tick (prevents one huge tick from monopolizing the loop)

Read-only contract: the chat-ai pool is opened with `default_transaction_read_only=on`
so a typo in the SQL can never write to rishi-1. We also never query
information_schema / pg_catalog for anything beyond column lists, and
column lists are hardcoded — no dynamic discovery against chat-ai.
"""

import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)


SYNC_INTERVAL_SEC = 5 * 60  # 5 minutes
INITIAL_DELAY_SEC = 60  # 1 min after startup
PAGE_SIZE = 1000
SAFETY_BATCH_LIMIT = 50  # per table per tick — cap one tick at 50k rows


# Tables synced in dependency order. ai_influencers first (conversations FK
# to it), conversations next (messages FK), messages last. Each entry pins
# the column list we copy — narrow to the intersection of chat-ai and v2
# schemas so v2-only columns (is_proactive, variant_label, etc.) just take
# their defaults on inserted rows.
SYNCED_TABLES: list[dict] = [
    {
        "name": "ai_influencers",
        "columns": [
            "id",
            "name",
            "display_name",
            "avatar_url",
            "description",
            "category",
            "system_instructions",
            "personality_traits",
            "initial_greeting",
            "suggested_messages",
            "is_active",
            "is_nsfw",
            "parent_principal_id",
            "source",
            "metadata",
            "created_at",
            "updated_at",
        ],
        "id_column": "id",
    },
    {
        "name": "conversations",
        "columns": [
            "id",
            "user_id",
            "influencer_id",
            "conversation_type",
            "participant_b_id",
            "metadata",
            "created_at",
            "updated_at",
        ],
        "id_column": "id",
    },
    {
        "name": "messages",
        "columns": [
            "id",
            "conversation_id",
            "role",
            "sender_id",
            "content",
            "message_type",
            "media_urls",
            "audio_url",
            "audio_duration_seconds",
            "token_count",
            "client_message_id",
            "status",
            "is_read",
            "metadata",
            "created_at",
        ],
        "id_column": "id",
    },
]


def _chat_ai_dsn() -> str | None:
    """Operator-provided DSN for the read-only chat-ai connection.
    None disables the ETL gracefully."""
    return os.environ.get("CHAT_AI_DATABASE_URL") or None


_chat_ai_pool = None
_chat_ai_pool_init_lock = asyncio.Lock()


async def _get_chat_ai_pool():
    """Lazy asyncpg pool to chat-ai with read-only transaction default.

    The session-level read-only setting is the second line of defense —
    even if our SQL contains a typo'd INSERT/UPDATE/DELETE, Postgres
    rejects it. The first line of defense is just that this module only
    runs SELECTs.
    """
    global _chat_ai_pool
    if _chat_ai_pool is not None:
        return _chat_ai_pool
    dsn = _chat_ai_dsn()
    if not dsn:
        return None
    async with _chat_ai_pool_init_lock:
        if _chat_ai_pool is not None:
            return _chat_ai_pool
        try:
            import asyncpg

            _chat_ai_pool = await asyncpg.create_pool(
                dsn,
                min_size=1,
                max_size=2,
                # Hard guard: every transaction is read-only by default.
                server_settings={"default_transaction_read_only": "on"},
                timeout=30,
                command_timeout=60,
            )
            logger.info("etl_chat_ai: read-only pool to chat-ai initialized")
        except Exception as e:
            logger.warning(f"etl_chat_ai: pool init failed (non-fatal): {e}")
            _chat_ai_pool = None
    return _chat_ai_pool


async def _read_cursor(v2_pool, table_name: str):
    """Return (last_sync_ts, rows_pulled_total). Falls back to epoch if no row."""
    row = await v2_pool.fetchrow(
        "SELECT last_sync_ts, rows_pulled_total FROM etl_sync_state WHERE table_name = $1",
        table_name,
    )
    if row is None:
        return None, 0
    return row["last_sync_ts"], int(row["rows_pulled_total"])


async def _write_cursor(
    v2_pool,
    table_name: str,
    new_cursor,
    rows_pulled_this_run: int,
    runtime_ms: int,
    error: str | None,
):
    await v2_pool.execute(
        """
        UPDATE etl_sync_state
        SET last_sync_ts = COALESCE($1, last_sync_ts),
            last_run_at = NOW(),
            rows_pulled_total = rows_pulled_total + $2,
            rows_pulled_last_run = $2,
            last_error = $3,
            last_runtime_ms = $4,
            updated_at = NOW()
        WHERE table_name = $5
        """,
        new_cursor,
        rows_pulled_this_run,
        error,
        runtime_ms,
        table_name,
    )


def _build_upsert_sql(table_name: str, columns: list[str], id_column: str) -> str:
    """INSERT ... ON CONFLICT DO NOTHING using the column list. Idempotent."""
    placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
    col_list = ", ".join(columns)
    return (
        f"INSERT INTO {table_name} ({col_list}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT ({id_column}) DO NOTHING"
    )


async def _sync_one_table(src_pool, v2_pool, table_spec: dict) -> dict:
    """Pull-and-insert loop for a single table. Returns stats dict."""
    table_name = table_spec["name"]
    columns = table_spec["columns"]
    id_column = table_spec["id_column"]
    col_list = ", ".join(columns)

    cursor, _ = await _read_cursor(v2_pool, table_name)
    if cursor is None:
        # The migration seeds these rows; if missing, log and skip.
        logger.warning(f"etl_chat_ai: no etl_sync_state row for {table_name}")
        return {"rows_pulled": 0, "batches": 0, "new_cursor": None}

    upsert_sql = _build_upsert_sql(table_name, columns, id_column)

    total_pulled = 0
    new_cursor = cursor
    batches = 0
    while batches < SAFETY_BATCH_LIMIT:
        rows = await src_pool.fetch(
            f"""
            SELECT {col_list}
            FROM {table_name}
            WHERE created_at > $1
            ORDER BY created_at
            LIMIT {PAGE_SIZE}
            """,
            new_cursor,
        )
        if not rows:
            break
        batches += 1

        # Batch-insert. asyncpg's executemany is fastest for large pages.
        values = [tuple(r[c] for c in columns) for r in rows]
        await v2_pool.executemany(upsert_sql, values)
        total_pulled += len(rows)

        # Advance cursor to the max created_at in this batch
        new_cursor = rows[-1]["created_at"]
        if len(rows) < PAGE_SIZE:
            break

    return {
        "rows_pulled": total_pulled,
        "batches": batches,
        "new_cursor": new_cursor,
    }


async def run_once(v2_pool) -> dict:
    """One full ETL pass — used by both the loop and tests."""
    src_pool = await _get_chat_ai_pool()
    if src_pool is None:
        return {"status": "disabled", "reason": "CHAT_AI_DATABASE_URL not set"}

    overall = {"status": "ok", "tables": {}}
    for spec in SYNCED_TABLES:
        table_name = spec["name"]
        t0 = time.monotonic()
        try:
            stats = await _sync_one_table(src_pool, v2_pool, spec)
            runtime_ms = int((time.monotonic() - t0) * 1000)
            await _write_cursor(
                v2_pool,
                table_name,
                stats["new_cursor"],
                stats["rows_pulled"],
                runtime_ms,
                error=None,
            )
            overall["tables"][table_name] = {
                "rows_pulled": stats["rows_pulled"],
                "batches": stats["batches"],
                "runtime_ms": runtime_ms,
            }
            logger.info(
                f"etl_chat_ai[{table_name}]: pulled {stats['rows_pulled']} rows "
                f"({stats['batches']} batches) in {runtime_ms}ms"
            )
        except Exception as e:
            runtime_ms = int((time.monotonic() - t0) * 1000)
            err = str(e)[:500]
            await _write_cursor(v2_pool, table_name, None, 0, runtime_ms, error=err)
            overall["tables"][table_name] = {"error": err, "runtime_ms": runtime_ms}
            logger.warning(f"etl_chat_ai[{table_name}] failed: {err}")
    return overall


async def etl_loop():
    """Background loop: run_once every SYNC_INTERVAL_SEC."""
    from database import get_pool

    await asyncio.sleep(INITIAL_DELAY_SEC)
    # If CHAT_AI_DATABASE_URL is unset, log once and keep the loop alive in
    # case the operator sets it later via `docker service update --env-add`.
    if not _chat_ai_dsn():
        logger.info(
            "etl_chat_ai: CHAT_AI_DATABASE_URL not set; ETL idle. "
            "Set it via `docker service update --env-add CHAT_AI_DATABASE_URL=...` to enable."
        )

    while True:
        try:
            v2_pool = await get_pool()
            await run_once(v2_pool)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"etl_chat_ai loop tick failed (non-fatal): {e}")
        await asyncio.sleep(SYNC_INTERVAL_SEC)


# ─── /status endpoint helper ──────────────────────────────────────────────


async def get_status(v2_pool) -> dict:
    """Snapshot for operators: latest cursor + last error per table."""
    rows = await v2_pool.fetch(
        """
        SELECT table_name, last_sync_ts, last_run_at,
               rows_pulled_total, rows_pulled_last_run,
               last_error, last_runtime_ms
        FROM etl_sync_state
        ORDER BY table_name
        """
    )
    return {
        "chat_ai_database_url_set": bool(_chat_ai_dsn()),
        "tables": [dict(r) for r in rows],
    }
