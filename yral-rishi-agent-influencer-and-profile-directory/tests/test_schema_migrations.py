# ---------------------------------------------------------------------------
# test_schema_migrations.py — Alembic upgrade + forward-only-downgrade
# state-consistency verification for the influencer-and-profile-directory
# service.
#
# ⭐ START HERE: four end-to-end tests against the testcontainer
# Postgres provisioned by conftest.py:
#   1. test_alembic_upgrade_succeeds_and_downgrade_raises_irreversible_
#      migration_error —
#      upgrade head succeeds → `downgrade base` raises
#      `IrreversibleMigrationError` (exits non-zero); table remains;
#      `alembic_version` row stays at `001_initial_schema`. Pins the
#      state-consistency invariant Codex round-5 flagged on PR #148.
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
# that cannot be reversed is a deploy risk if reversing also corrupts
# alembic state. A schema with the wrong column set or missing indexes
# would surface as a Chunk B endpoint failure or a slow trending query
# in production. Catching all four classes of regression in this single
# file means the data layer is safe to land independently of Chunk B's
# endpoints.
#
# A1 NOTE — FORWARD-ONLY DOWNGRADE + STATE CONSISTENCY (round-6 update)
# Per A1, dropping `influencer_metadata` is a hard-stop deletion category
# requiring a separate typed-YES PR. This migration's `downgrade()`
# RAISES `IrreversibleMigrationError` (round-6) rather than silently
# returning (round-4) because the silent-no-op shape pinned an
# inconsistent alembic-state where `alembic_version` could read `base`
# while the table remained. Test #1 pins the new raise-loudly shape +
# the state-consistency invariant. Full rationale in
# `001_initial_schema.py:downgrade()`'s docstring + the file-header
# A1 HARD-STOP + FORWARD-ONLY DOWNGRADE block.
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
async def test_alembic_upgrade_succeeds_and_downgrade_raises_irreversible_migration_error(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: verify `alembic upgrade head` created the table + verify
          `alembic downgrade base` RAISES `IrreversibleMigrationError`
          (exits non-zero) AND leaves `alembic_version` + the
          `influencer_metadata` table in a consistent state per the
          forward-only A1 hard-stop discipline.
    WHEN: once per pytest session (conftest runs upgrade head; this test
          runs the downgrade phase + asserts the raise-loudly shape).
    WHY:  per A1, dropping a database table is a hard-stop deletion
          category requiring an explicit typed YES + the formal
          deletion-report format. This PR's scope does NOT carry that
          typed YES, so `001_initial_schema.downgrade()` intentionally
          raises `IrreversibleMigrationError` (see the migration file's
          downgrade() docstring for the full rationale).

          THIS TEST IS THE STATE-CONSISTENCY GATE (Codex round-5 CONCERN
          closure, PR #148 round-6 per coordinator routing 2026-05-24,
          recommendation (b)). The round-4 no-op shape pinned an
          inconsistent alembic-state: `alembic downgrade base` exited 0,
          alembic wrote `version_num = base` to `alembic_version`, but
          the `influencer_metadata` table remained — so a later
          `alembic upgrade head` would attempt to `CREATE TABLE
          influencer_metadata` against an existing table + fail with a
          DuplicateTable error. Round-6 fixes this by raising INSIDE
          the migration function (before alembic updates the version
          row), so the version + schema stay in agreement.

          The three load-bearing assertions:
            (1) downgrade exit code is non-zero (alembic propagated
                the raise — CI fails loudly if a regression silently
                swallows the raise or re-adds `op.drop_table(...)`).
            (2) `influencer_metadata` table still exists (schema
                unchanged — the raise aborted the transaction).
            (3) `alembic_version.version_num = '001_initial_schema'`
                (version unchanged — alembic never wrote `base`).

          Migrations in this codebase are FORWARD-ONLY by default.
          Rollback is a separate intentional act (a new
          `002_drop_influencer_metadata.py` migration with its own A1
          deletion-report + typed YES PR), NOT the flip-side of a
          casual `alembic downgrade` invocation.

          Renamed from `test_alembic_upgrade_succeeds_and_downgrade_is_
          no_op` (round-4) → this name (round-6) because the
          downgrade contract changed from silent-no-op → raise-loudly.
          The test continues to exercise the
          `sa.dialects.postgresql.TIMESTAMP(...)` access pattern via
          the upgrade phase (Codex's round-1 dialect-import CONCERN is
          still pinned: if the dialect access were broken at runtime,
          upgrade would fail here in CI).
    """
    # ---- Phase 1: verify upgrade head created the table ----------------
    # conftest's `run_alembic_upgrade` already ran `upgrade head` before
    # any test function ran. Verify the result is correct.
    assert await _table_exists(database_pool, "influencer_metadata"), (
        "`influencer_metadata` table should exist after `alembic upgrade "
        "head`. Check that 001_initial_schema.upgrade() ran without error."
    )

    # ---- Phase 2: run downgrade base → should RAISE + exit non-zero ----
    downgrade_result = _run_alembic("downgrade base")
    assert downgrade_result.returncode != 0, (
        f"`alembic downgrade base` UNEXPECTEDLY exited 0 (expected "
        f"non-zero — `IrreversibleMigrationError` should propagate to "
        f"the CLI). A regression likely turned downgrade() back into "
        f"a silent no-op, OR replaced the raise with `return`, which "
        f"would re-open the round-5 inconsistent-alembic-state gap.\n"
        f"STDOUT:\n{downgrade_result.stdout}\n"
        f"STDERR:\n{downgrade_result.stderr}"
    )
    assert "IrreversibleMigrationError" in downgrade_result.stderr, (
        f"`alembic downgrade base` stderr did not mention "
        f"`IrreversibleMigrationError`. Expected the migration's raised "
        f"exception class name to appear in the traceback alembic prints "
        f"on non-zero exit.\n"
        f"STDOUT:\n{downgrade_result.stdout}\n"
        f"STDERR:\n{downgrade_result.stderr}"
    )

    # ---- Phase 3a: verify table still exists (schema unchanged) --------
    # The raise aborted the downgrade transaction before any schema
    # mutation could land. A regression that re-added `op.drop_table(...)`
    # BEFORE the raise would fail this assertion loudly.
    assert await _table_exists(database_pool, "influencer_metadata"), (
        "`influencer_metadata` table should STILL exist after `alembic "
        "downgrade base` raised `IrreversibleMigrationError` — the raise "
        "aborts the downgrade transaction. If this assertion fails, "
        "someone added a `op.drop_table(...)` call BEFORE the raise in "
        "downgrade(), which is the destructive shape A1 forbids without "
        "typed YES."
    )

    # ---- Phase 3b: verify alembic_version still at 001_initial_schema --
    # The state-consistency gate Codex round-5 flagged: with the round-4
    # no-op shape, alembic wrote `version_num = base` to alembic_version
    # while the table remained → inconsistent state. With the round-6
    # raise shape, alembic never reaches the version-update step, so
    # `version_num` stays at `001_initial_schema`. This assertion is
    # the load-bearing pin against a regression that silently swallows
    # the raise (e.g. wraps it in `try: ... except: pass`).
    async with database_pool.acquire() as conn:
        current_version = await conn.fetchval(
            "SELECT version_num FROM alembic_version;"
        )
    assert current_version == "001_initial_schema", (
        f"`alembic_version.version_num` should still be "
        f"'001_initial_schema' after `alembic downgrade base` raised — "
        f"the raise aborts the version-update transaction. Actual: "
        f"{current_version!r}. If this fails with `version_num = 'base'`"
        f", the round-5 inconsistent-alembic-state bug is back."
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
    # Expected columns per 001_initial_schema.upgrade(). Split into
    # three sets so a future reader sees which are InfluencerDto-
    # contract fields vs round-5 chat-ai-port additions vs v2-only
    # audit fields.
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
    expected_chat_ai_port_columns = {
        # Round-5 additions per A4/A8 chat-ai schema port closure.
        "name",
        "personality_traits",
        "initial_greeting",
        "suggested_messages",
        "metadata",
    }
    expected_v2_only_columns = {
        "source",
        "created_at",
        "updated_at",
    }
    expected_columns = (
        expected_contract_columns
        | expected_chat_ai_port_columns
        | expected_v2_only_columns
    )

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
        # Round-1 indexes (v2-specific + matches chat-ai's
        # idx_influencers_category):
        "influencer_metadata_active_follower_count",
        "influencer_metadata_archetype",
        # Round-5 additions per A4/A8 chat-ai schema port — mirror
        # chat-ai's idx_influencers_active + idx_influencers_active_nsfw
        # + idx_influencers_parent_principal. Also a UNIQUE index on
        # the new `name` column (chat-ai's ai_influencers_name_key).
        "influencer_metadata_is_active",
        "influencer_metadata_is_active_is_nsfw",
        "influencer_metadata_creator_user_id",
        "influencer_metadata_name_key",
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
            # All NOT-NULL contract columns supplied with valid
            # placeholder values; the 5 round-5 chat-ai-port columns
            # + 3 audit columns have DB-level DEFAULTs so they're
            # omitted from the INSERT. Only `is_active` carries the
            # invalid 'banned' value — the round-5 CHECK vocabulary
            # is 'active' | 'coming_soon' | 'discontinued', and
            # 'banned' is not in that set so the constraint must
            # reject.
            await connection.execute(
                """
                INSERT INTO influencer_metadata (
                    id, name, display_name, bio, avatar_url, archetype,
                    is_nsfw, follower_count, creator_user_id, is_active
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
                )
                """,
                "test-id-banned-value-rejection",
                "test_id_banned_value_rejection",  # unique-name slug
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
