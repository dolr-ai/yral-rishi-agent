# ---------------------------------------------------------------------------
# test_schema_migrations.py — Alembic upgrade/downgrade round-trip + schema
# shape verification for the influencer-and-profile-directory service.
#
# ⭐ START HERE: four end-to-end tests against the testcontainer
# Postgres provisioned by conftest.py:
#   1. test_alembic_upgrade_then_downgrade_round_trips_cleanly —
#      upgrade head → downgrade base → upgrade head, verifying the
#      `influencer_metadata` table appears + disappears + reappears
#      at each phase.
#   2. test_influencer_metadata_table_has_correct_columns —
#      column-presence assertion against the migration spec.
#   3. test_influencer_metadata_table_has_expected_indexes —
#      verifies the partial trending index + the archetype B-tree
#      both landed.
#   4. test_is_active_check_constraint_rejects_unknown_value —
#      attempt to INSERT a row with `is_active='banned'` (not in the
#      contract vocabulary); assert Postgres raises CheckViolationError.
#
# WHY THIS FILE EXISTS
# Per H11 spirit + Codex round-1 J1/J3 CONCERN on PR #142 — a migration
# that cannot be reversed is a deploy risk. A schema with the wrong
# column set or missing indexes would surface as a Chunk B endpoint
# failure or a slow trending query in production. Catching all four
# classes of regression in this single file means the data layer is
# safe to land independently of Chunk B's endpoints.
#
# A1 NOTE — WHY THE DROP IS SAFE IN THIS TEST
# The `downgrade base` command drops the `influencer_metadata` table
# this migration itself created moments earlier in an ephemeral
# testcontainers Postgres. This is NOT destroying pre-existing user
# data. See `001_initial_schema.py:downgrade()`'s A1 deletion-report
# block + the file-header A1 JUSTIFICATION block for the full reasoning
# + the typed-YES citation chain.
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
    WHY:  reusable helper — the round-trip test checks three times total
          (post-upgrade, post-downgrade, post-second-upgrade).
    """
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
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
          `POSTGRES_CONNECTION_STRING_INFLUENCER_AND_PROFILE_DIRECTORY`
          (set by conftest's `run_alembic_upgrade` fixture).
    WHEN: called from the round-trip test for `downgrade base` /
          `upgrade head`.
    WHY:  matches the prod-deploy + operator-runbook code path exactly.
          If `alembic` isn't on $PATH or a migration script is broken,
          this catches it the same way production would.
    """
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
    WHY:  H11 spirit — every migration must be reversible without manual
          SQL. If downgrade() is broken, this catches it before a bad
          deploy hits the cluster. Also exercises the
          `sa.dialects.postgresql.TIMESTAMP(...)` access pattern Codex
          round-1 flagged — if the dialect access were genuinely broken
          at runtime, the upgrade phase would fail here in CI rather
          than silently passing `py_compile`.
    """
    # ---- Phase 1: verify upgrade head created the table ----------------
    # conftest's `run_alembic_upgrade` already ran `upgrade head` before
    # any test function ran. Verify the result is correct before testing
    # the downgrade path.
    assert await _table_exists(database_pool, "influencer_metadata"), (
        "`influencer_metadata` table should exist after `alembic upgrade "
        "head`. Check that 001_initial_schema.upgrade() ran without error."
    )

    # ---- Phase 2: run downgrade base → drops the table -----------------
    downgrade_result = _run_alembic("downgrade base")
    assert downgrade_result.returncode == 0, (
        f"`alembic downgrade base` failed with exit code "
        f"{downgrade_result.returncode}.\n"
        f"STDOUT:\n{downgrade_result.stdout}\n"
        f"STDERR:\n{downgrade_result.stderr}"
    )

    # ---- Phase 3: verify downgrade dropped the table -------------------
    # Only `alembic_version` should remain — the migration's own table
    # should be gone.
    assert not await _table_exists(database_pool, "influencer_metadata"), (
        "`influencer_metadata` table should NOT exist after `alembic "
        "downgrade base`. Check that 001_initial_schema.downgrade() drops "
        "the table."
    )

    # ---- Phase 4: re-run upgrade head → restore schema for later tests -
    # Other tests in the session depend on the migrated schema.
    # Re-upgrading here leaves the DB in the correct post-migration state.
    upgrade_result = _run_alembic("upgrade head")
    assert upgrade_result.returncode == 0, (
        f"`alembic upgrade head` (re-run after downgrade) failed with "
        f"exit code {upgrade_result.returncode}.\n"
        f"STDOUT:\n{upgrade_result.stdout}\n"
        f"STDERR:\n{upgrade_result.stderr}"
    )

    # Confirm the table is back after the re-upgrade.
    assert await _table_exists(database_pool, "influencer_metadata"), (
        "`influencer_metadata` table should exist after second "
        "`alembic upgrade head`."
    )


@pytest.mark.asyncio
async def test_influencer_metadata_table_has_correct_columns(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: verify `influencer_metadata` has the exact column set the
          migration spec declares — both the 9 contract-shape columns
          AND the 3 v2-only audit/source columns.
    WHEN: after `alembic upgrade head` runs (conftest guarantees this).
    WHY:  catches column name typos or missing columns that would cause
          Chunk B endpoints to 500 on every insert/select, OR a future
          schema-bump that accidentally drops a column. Pins both the
          chat-ai-parity column names (per A8+D2) AND the v2-only
          fields (per the Q1 lock-in 2026-05-23).
    """
    # Expected columns per 001_initial_schema.upgrade(). Split into two
    # sets so a future reader sees which are parity vs v2-only.
    expected_contract_columns = {
        "id",
        "display_name",
        "bio",
        "avatar_url",
        "archetype",
        "is_nsfw",
        "follower_count",
        "creator_user_id",
        "is_active",
    }
    expected_v2_only_columns = {
        "source",
        "created_at",
        "updated_at",
    }
    expected_columns = expected_contract_columns | expected_v2_only_columns

    async with database_pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = 'influencer_metadata';"
        )
    actual_columns = {row["column_name"] for row in rows}

    assert actual_columns == expected_columns, (
        f"influencer_metadata table column mismatch.\n"
        f"Expected: {sorted(expected_columns)}\n"
        f"Actual:   {sorted(actual_columns)}"
    )


@pytest.mark.asyncio
async def test_influencer_metadata_table_has_expected_indexes(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: verify both supporting indexes from the migration landed.
    WHEN: after `alembic upgrade head` runs.
    WHY:  the partial trending index
          (`influencer_metadata_active_follower_count`) is the load-
          bearing performance gate for `GET /v1/influencers/trending` —
          without it the query falls back to a full-table scan + sort,
          which scales O(n) on the catalog size. The archetype B-tree
          (`influencer_metadata_archetype`) supports future filter-by-
          archetype mobile UI. A migration that forgets either index
          would silently regress catalog-list performance in production
          + nothing else would catch it.
    """
    expected_indexes = {
        "influencer_metadata_active_follower_count",
        "influencer_metadata_archetype",
    }

    async with database_pool.acquire() as connection:
        rows = await connection.fetch(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' "
            "AND tablename = 'influencer_metadata' "
            "AND indexname != 'influencer_metadata_pkey';"
        )
    actual_indexes = {row["indexname"] for row in rows}

    assert actual_indexes == expected_indexes, (
        f"influencer_metadata index mismatch.\n"
        f"Expected (non-PK): {sorted(expected_indexes)}\n"
        f"Actual (non-PK):   {sorted(actual_indexes)}"
    )


@pytest.mark.asyncio
async def test_is_active_check_constraint_rejects_unknown_value(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: attempt to INSERT a row with `is_active='banned'` (not in
          the contract vocabulary {'active', 'discontinued'}); assert
          Postgres raises `CheckViolationError`.
    WHEN: after `alembic upgrade head` runs.
    WHY:  the CHECK constraint
          `influencer_metadata_is_active_in_active_or_discontinued`
          is the schema-level enforcement of the contract's `is_active`
          vocabulary. Removing or weakening it would allow a future
          repository write (e.g. from the eventual creator-studio
          flow) to insert a value the InfluencerDto contract doesn't
          declare, which mobile would then fail to deserialize. This
          test pins the constraint's behaviour with a deliberately-
          invalid value the migration's CHECK must reject.
    """
    async with database_pool.acquire() as connection:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            # All other columns supplied with valid placeholder values;
            # only `is_active` carries the invalid 'banned' value the
            # CHECK constraint should reject.
            await connection.execute(
                """
                INSERT INTO influencer_metadata (
                    id, display_name, bio, avatar_url, archetype,
                    is_nsfw, follower_count, creator_user_id, is_active
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9
                )
                """,
                "test-id-banned-value-rejection",
                "Test Display Name",
                "Test bio.",
                "https://example.invalid/avatar.png",
                "companion",
                False,
                0,
                None,
                "banned",  # NOT in the CHECK vocabulary.
            )


# ===========================================================================
# RELATED FILES:
#   conftest.py                              — testcontainers Postgres +
#                                                Alembic upgrade fixture
#   ../app/migrations/versions/001_initial_schema.py
#                                            — the migration this file tests
#   ../app/migrations/env.py                 — Alembic env that the
#                                                subprocess invocations
#                                                drive
#   ../alembic.ini                           — `script_location` config
#   ../../yral-rishi-agent-user-memory-service/tests/test_schema_migrations.py
#                                            — Session-5's cross-service
#                                                precedent this file mirrors
# ===========================================================================
