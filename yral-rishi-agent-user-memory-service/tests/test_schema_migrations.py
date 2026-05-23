# ---------------------------------------------------------------------------
# test_schema_migrations.py — Alembic upgrade/downgrade round-trip test.
#
# ⭐ START HERE: this file runs ONE end-to-end test —
# `test_alembic_upgrade_then_downgrade_round_trips_cleanly` — which:
#   1. Confirms `upgrade head` (run by conftest's session fixture) created
#      BOTH the `conversations` and `messages` tables.
#   2. Runs `alembic downgrade base` to reverse the migration.
#   3. Confirms BOTH tables are gone (only `alembic_version` remains).
#   4. Runs `alembic upgrade head` AGAIN to restore the schema so
#      subsequent tests in the session see the correct empty-but-migrated
#      state.
#
# WHY THIS TEST EXISTS?
# Per H11 spirit + the Day-4 directive's "Schema: alembic upgrade +
# downgrade round-trip succeeds." A migration that cannot be reversed
# is a deploy risk — if a bad deploy needs rollback, `alembic downgrade`
# must work. This test catches broken downgrade() functions before they
# hit the cluster.
#
# WHY THE LAST STEP RE-RUNS UPGRADE?
# Other tests in this session (added in Deliverable 2) depend on the
# migrated schema. Leaving the DB in `downgrade base` state after this
# test would break them. The round-trip re-upgrade restores the
# post-conftest-setup state.
#
# A1 NOTE — WHY THE DROP IS SAFE IN THIS TEST:
# The `downgrade base` command drops the `conversations` and `messages`
# tables this migration itself created moments earlier in an ephemeral
# testcontainers Postgres. This is NOT destroying pre-existing user data.
# See `001_initial_schema.py`'s A1 JUSTIFICATION block for full reasoning.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import os
import subprocess
from pathlib import Path

import asyncpg
import pytest


# Path to the service folder root — Alembic shell invocations need this
# as the CWD so they find `alembic.ini` + `app/migrations/`.
SERVICE_ROOT: Path = Path(__file__).resolve().parent.parent


async def _table_exists(pool: asyncpg.Pool, table_name: str) -> bool:
    """Return True if `table_name` exists in the public schema.

    WHAT: queries `information_schema.tables` for the given table name.
    WHEN: called from the round-trip test before + after each Alembic
          phase to verify upgrade created / downgrade removed the table.
    WHY:  reusable helper — the round-trip test checks four times total
          (two tables × two states: post-upgrade, post-downgrade).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = 'public' AND table_name = $1"
            ")",
            table_name,
        )
    return bool(row["exists"])


def _run_alembic(command: str) -> subprocess.CompletedProcess:
    """Run `alembic <command>` from the service root with the current env.

    WHAT: shells out to the alembic CLI with the env containing
          POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE (set by conftest's
          `run_alembic_upgrade` fixture).
    WHEN: called from the round-trip test for `downgrade base` /
          `upgrade head`.
    WHY:  matches the prod-deploy + operator-runbook code path exactly.
          If `alembic` isn't on $PATH or a migration script is broken,
          this catches it the same way production would.
    """
    # Split the command string into a list for subprocess.
    parts = command.split()
    return subprocess.run(
        ["alembic"] + parts,
        cwd=SERVICE_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_alembic_upgrade_then_downgrade_round_trips_cleanly(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: full round-trip — upgrade head → downgrade base → upgrade head.
    WHEN: once per pytest session (conftest runs upgrade head; this test
          runs the rest of the cycle).
    WHY:  H11 spirit + Day-4 directive — every migration must be reversible
          without manual SQL. If downgrade() is broken, this catches it
          before a bad deploy hits the cluster.
    """
    # ---- Phase 1: verify upgrade head created BOTH tables ----------------
    # conftest's `run_alembic_upgrade` already ran `upgrade head` before
    # any test function ran. Verify the result is correct before testing
    # the downgrade path.
    assert await _table_exists(database_pool, "conversations"), (
        "`conversations` table should exist after `alembic upgrade head`. "
        "Check that 001_initial_schema.upgrade() ran without error."
    )
    assert await _table_exists(database_pool, "messages"), (
        "`messages` table should exist after `alembic upgrade head`. "
        "Check that 001_initial_schema.upgrade() ran without error."
    )

    # ---- Phase 2: run downgrade base → drops both tables -----------------
    downgrade_result = _run_alembic("downgrade base")
    assert downgrade_result.returncode == 0, (
        f"`alembic downgrade base` failed with exit code "
        f"{downgrade_result.returncode}.\n"
        f"STDOUT:\n{downgrade_result.stdout}\n"
        f"STDERR:\n{downgrade_result.stderr}"
    )

    # ---- Phase 3: verify downgrade dropped BOTH tables -------------------
    # Only `alembic_version` should remain — the migration's own tables
    # should be gone.
    assert not await _table_exists(database_pool, "conversations"), (
        "`conversations` table should NOT exist after `alembic downgrade base`. "
        "Check that 001_initial_schema.downgrade() drops the table."
    )
    assert not await _table_exists(database_pool, "messages"), (
        "`messages` table should NOT exist after `alembic downgrade base`. "
        "Check that 001_initial_schema.downgrade() drops the table."
    )

    # ---- Phase 4: re-run upgrade head → restore schema for later tests --
    # Other tests in the session (added in Deliverable 2) need the schema.
    # Re-upgrading here leaves the DB in the correct post-migration state.
    upgrade_result = _run_alembic("upgrade head")
    assert upgrade_result.returncode == 0, (
        f"`alembic upgrade head` (re-run after downgrade) failed with exit "
        f"code {upgrade_result.returncode}.\n"
        f"STDOUT:\n{upgrade_result.stdout}\n"
        f"STDERR:\n{upgrade_result.stderr}"
    )

    # Confirm both tables are back after the re-upgrade.
    assert await _table_exists(database_pool, "conversations"), (
        "`conversations` table should exist after second `alembic upgrade head`."
    )
    assert await _table_exists(database_pool, "messages"), (
        "`messages` table should exist after second `alembic upgrade head`."
    )


@pytest.mark.asyncio
async def test_conversations_table_has_correct_columns(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: verify `conversations` has the exact column set from the migration spec.
    WHEN: after `alembic upgrade head` runs (conftest guarantees this).
    WHY:  catches column name typos or missing columns that would cause
          the Deliverable 2 repository layer to 500 on every insert.
    """
    # Expected columns per 001_initial_schema.upgrade() — each name is
    # the exact SQL identifier used in the CREATE TABLE statement.
    expected_columns = {
        "id",
        "user_id",
        "influencer_id",
        "participant_b_id",
        "conversation_type",
        "created_at",
        "last_message_at",
        "message_count",
        "soft_deleted_at",
    }

    async with database_pool.acquire() as conn:
        # Query the information schema for the conversations table columns.
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'conversations';"
        )
    actual_columns = {row["column_name"] for row in rows}

    assert actual_columns == expected_columns, (
        f"conversations table column mismatch.\n"
        f"Expected: {sorted(expected_columns)}\n"
        f"Actual:   {sorted(actual_columns)}"
    )


@pytest.mark.asyncio
async def test_messages_table_has_correct_columns(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: verify `messages` has the exact column set from both migrations.
    WHEN: after `alembic upgrade head` runs (conftest guarantees this) — which
          applies 001 (7 base columns) then 002 (adds client_message_id +
          count_toward_paywall for a total of 9).
    WHY:  catches column name typos or missing columns that would cause
          the Deliverable 2 route handlers to 500 on every insert or select.
    """
    # Expected columns per 001_initial_schema.upgrade() (7 base columns)
    # + 002_add_message_fields.upgrade() (2 additional columns).
    # Each name is the exact SQL identifier used in the CREATE TABLE /
    # ALTER TABLE statement.
    expected_columns = {
        "id",
        "conversation_id",
        "role",
        "content",
        "media_urls",
        "created_at",
        "gemini_metadata",
        # Added by 002_add_message_fields:
        "client_message_id",
        "count_toward_paywall",
    }

    async with database_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'messages';"
        )
    actual_columns = {row["column_name"] for row in rows}

    assert actual_columns == expected_columns, (
        f"messages table column mismatch.\n"
        f"Expected: {sorted(expected_columns)}\n"
        f"Actual:   {sorted(actual_columns)}"
    )


@pytest.mark.asyncio
async def test_conversations_can_insert_and_query(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: verify the conversations table accepts a valid insert + returns it.
    WHEN: after schema is created (conftest + run_alembic_upgrade).
    WHY:  smoke test for the full happy path — check constraint, UUID PK
          generation, and timestamp defaults all working together. Catches
          any subtle DDL error that `table_exists` wouldn't reveal.
    """
    async with database_pool.acquire() as conn:
        # Insert a minimal ai_chat conversation row.
        row = await conn.fetchrow(
            """
            INSERT INTO conversations
                (user_id, influencer_id, conversation_type)
            VALUES ($1, $2, $3)
            RETURNING id, user_id, influencer_id, conversation_type,
                      created_at, last_message_at, message_count, soft_deleted_at;
            """,
            "test-user-abc",
            "test-influencer-xyz",
            "ai_chat",
        )

    # UUID was generated by gen_random_uuid().
    assert row["id"] is not None, "id should be auto-generated by gen_random_uuid()"

    # Columns round-trip correctly.
    assert row["user_id"] == "test-user-abc"
    assert row["influencer_id"] == "test-influencer-xyz"
    assert row["conversation_type"] == "ai_chat"

    # Defaults applied correctly.
    assert row["message_count"] == 0, "message_count should default to 0"
    assert row["soft_deleted_at"] is None, "soft_deleted_at should default to NULL"
    assert row["created_at"] is not None, "created_at should be set by NOW()"
    assert row["last_message_at"] is not None, "last_message_at should be set by NOW()"


@pytest.mark.asyncio
async def test_messages_can_insert_and_query_with_fk(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: verify a message insert with a valid conversation FK works.
    WHEN: after schema is created.
    WHY:  confirms the FK relationship between messages and conversations
          is wired correctly; JSONB columns accept dict payloads; check
          constraint on role accepts 'user' + 'assistant'.
    """
    async with database_pool.acquire() as conn:
        # Create a conversation to hold the messages.
        conv_row = await conn.fetchrow(
            "INSERT INTO conversations (user_id, influencer_id, conversation_type) "
            "VALUES ('u1', 'inf1', 'ai_chat') RETURNING id;",
        )
        conversation_id = conv_row["id"]

        # Insert a user message.
        user_msg = await conn.fetchrow(
            """
            INSERT INTO messages
                (conversation_id, role, content, media_urls, gemini_metadata)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, conversation_id, role, content, media_urls,
                      gemini_metadata, created_at;
            """,
            conversation_id,
            "user",
            "Hello from the test",
            None,  # no media for this message
            None,  # no gemini metadata for user messages
        )

        # Insert an assistant message with gemini_metadata JSONB.
        assistant_msg = await conn.fetchrow(
            """
            INSERT INTO messages
                (conversation_id, role, content, gemini_metadata)
            VALUES ($1, $2, $3, $4)
            RETURNING id, role, gemini_metadata;
            """,
            conversation_id,
            "assistant",
            "Hello from the assistant",
            '{"prompt_tokens": 120, "completion_tokens": 45, '
            '"model": "gemini-2.5-flash", "latency_ms": 843}',
        )

    # User message checks.
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "Hello from the test"
    assert user_msg["conversation_id"] == conversation_id
    assert user_msg["media_urls"] is None

    # Assistant message checks.
    assert assistant_msg["role"] == "assistant"
    # Postgres returns JSONB as a Python dict when using asyncpg.
    gemini_meta = assistant_msg["gemini_metadata"]
    assert isinstance(gemini_meta, dict), "gemini_metadata should deserialise as dict"
    assert gemini_meta["prompt_tokens"] == 120
    assert gemini_meta["model"] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_check_constraint_rejects_invalid_conversation_type(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: verify the check constraint on conversation_type rejects bad values.
    WHEN: after schema is created.
    WHY:  ensures the constraint is active — a missing CHECK would silently
          accept 'ai_chats' (extra 's') or any string, causing mobile to
          receive invalid data from the RPC.
    """
    import asyncpg as _asyncpg

    async with database_pool.acquire() as conn:
        with pytest.raises(_asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO conversations "
                "(user_id, conversation_type) VALUES ('u1', 'invalid_type');"
            )


@pytest.mark.asyncio
async def test_check_constraint_rejects_invalid_message_role(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: verify the check constraint on messages.role rejects bad values.
    WHEN: after schema is created.
    WHY:  role drives mobile bubble rendering ('user' vs 'assistant'). An
          invalid role like 'bot' would silently render wrong on mobile.
    """
    import asyncpg as _asyncpg

    async with database_pool.acquire() as conn:
        # First insert a conversation to FK against.
        conv_row = await conn.fetchrow(
            "INSERT INTO conversations (user_id, influencer_id, conversation_type) "
            "VALUES ('u2', 'inf2', 'ai_chat') RETURNING id;",
        )
        with pytest.raises(_asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO messages (conversation_id, role, content) "
                "VALUES ($1, 'bot', 'bad role test');",
                conv_row["id"],
            )


@pytest.mark.asyncio
async def test_migration_003_unique_indexes_exist(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: verify the two unique partial indexes from migration 003 were
             created by `alembic upgrade head`.
    WHEN: after conftest's session fixture runs `upgrade head` (which applies
          001 → 002 → 003 in sequence).
    WHY:  the ON CONFLICT clauses in conversation_routes.py's atomic upsert
          (create_or_get_conversation) and message idempotency
          (append_messages) rely on these indexes. If either index is missing,
          the ON CONFLICT inference will fail at runtime with a Postgres error
          — traffic-breaking, not just a test failure. Catching the missing
          index here at test time is far cheaper.
    """
    # --- conversations_natural_key_active_unique_idx ----------------------
    async with database_pool.acquire() as conn:
        conv_indexes = await conn.fetch(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = 'conversations';",
        )
    conv_index_names = {row["indexname"] for row in conv_indexes}

    assert "conversations_natural_key_active_unique_idx" in conv_index_names, (
        "conversations_natural_key_active_unique_idx not found in pg_indexes. "
        "Check that 003_add_dedup_indexes.upgrade() ran without error and that "
        "alembic upgrade head reached migration 003."
    )

    # --- messages_client_message_id_dedup_idx ----------------------------
    async with database_pool.acquire() as conn:
        msg_indexes = await conn.fetch(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = 'messages';",
        )
    msg_index_names = {row["indexname"] for row in msg_indexes}

    assert "messages_client_message_id_dedup_idx" in msg_index_names, (
        "messages_client_message_id_dedup_idx not found in pg_indexes. "
        "Check that 003_add_dedup_indexes.upgrade() ran without error and that "
        "alembic upgrade head reached migration 003."
    )


# ===========================================================================
# RELATED FILES:
#   conftest.py                          — spins testcontainers-postgres,
#                                          runs alembic upgrade head, provides
#                                          database_pool fixture
#   ../app/migrations/versions/001_initial_schema.py
#                                        — base schema migration under test
#   ../app/migrations/versions/002_add_message_fields.py
#                                        — adds client_message_id column
#   ../app/migrations/versions/003_add_dedup_indexes.py
#                                        — adds unique indexes verified by
#                                          test_migration_003_unique_indexes_exist
#   ../alembic.ini                       — Alembic config used by _run_alembic
#   ../app/migrations/env.py             — Alembic env.py dispatched by
#                                          `alembic downgrade/upgrade`
# ===========================================================================
