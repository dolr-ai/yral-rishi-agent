# ---------------------------------------------------------------------------
# test_schema_migrations.py — alembic upgrade/downgrade round-trip test.
#
# ⭐ START HERE: this file runs ONE test —
# `test_alembic_upgrade_then_downgrade_round_trips_cleanly` — which:
#   1. Confirms `upgrade head` (already run by conftest's session
#      fixture) created the `soul_file_layers` table.
#   2. Runs `alembic downgrade base` from the service root.
#   3. Confirms the table is gone (only `alembic_version` remains).
#   4. Runs `alembic upgrade head` again (so subsequent tests in the
#      session see the seeded state).
#
# WHY THIS TEST IS NECESSARY
# Per the Day-4 directive verbatim: "Schema: alembic upgrade +
# downgrade round-trip succeeds." Also per CONSTRAINTS H11 spirit:
# every migration must be reversible without manual SQL.
#
# A1 PROVENANCE — why this test asserts `drop_table` succeeded
# ------------------------------------------------------------
# The round-trip's `downgrade base` phase invokes the migration's
# `downgrade()` function which DROPS the `soul_file_layers` table
# the same migration's `upgrade()` had created moments earlier in
# the testcontainers-Postgres. Per the A1 deletion justification
# block in `001_initial_schema_and_seed.py`, this is reversibility
# of the migration's own artifact + happens against a fresh
# ephemeral DB — not destruction of pre-existing production data.
# The test's `assert not await _table_exists(...)` line below is
# the intentional verification of that reversibility, NOT an A1
# deletion request from this test. Coordinator approval recorded
# in the migration file's A1 justification block + SECURITY.md's
# "A1 carve-outs granted" section.
#
# WHY THE LAST STEP RE-RUNS UPGRADE
# Other tests in this session depend on the seeded state. Leaving
# the DB in `downgrade base` state would break them. The fixture
# ordering doesn't help here because this test EXPLICITLY mutates
# the DB schema mid-session. Re-running upgrade at the end restores
# the post-conftest-setup state.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import os
import subprocess
from pathlib import Path

import asyncpg
import pytest


# Path to the service folder so Alembic shell invocations work
# regardless of pytest's launch CWD.
SERVICE_ROOT: Path = Path(__file__).resolve().parent.parent


async def _table_exists(pool: asyncpg.Pool, table_name: str) -> bool:
    """Return True if `table_name` exists in the current DB schema.

    WHAT: queries information_schema.tables.
    WHEN: called from the round-trip test before + after each Alembic
          phase.
    WHY:  reusable helper; the round-trip test checks the same shape
          three times (post-up, post-down, post-up-again).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table_name,
        )
    return bool(row["exists"])


def _run_alembic(arg: str) -> subprocess.CompletedProcess:
    """Run `alembic <arg>` from the service root with current env.

    WHAT: shells out to the alembic CLI with the env carrying the
          POSTGRES_DSN_SOUL_FILE_LIBRARY conftest exported.
    WHEN: called from the round-trip test for `downgrade base` /
          `upgrade head`.
    WHY:  matches the deploy-time + operator-runbook command path.
          If `alembic` isn't on $PATH or the migration script is
          broken, this catches it the same way prod would.
    """
    return subprocess.run(
        ["alembic", arg.split()[0], *arg.split()[1:]],
        cwd=SERVICE_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_alembic_upgrade_then_downgrade_round_trips_cleanly(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: round-trip upgrade head → downgrade base → upgrade head.
    WHEN: once per pytest session.
    WHY:  H11 spirit + Day-4 directive — every migration must be
          reversible without manual SQL.
    """
    # 1. Confirm conftest's session-scoped `upgrade head` already
    #    created the table (we're piggybacking on that work).
    assert await _table_exists(database_pool, "soul_file_layers"), (
        "soul_file_layers should exist after conftest's upgrade head"
    )

    # 2. Downgrade to base — drops the table.
    result = _run_alembic("downgrade base")
    assert result.returncode == 0, (
        f"alembic downgrade base failed:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    # 3. Confirm the table is gone. NOTE: alembic's bookkeeping table
    #    `alembic_version` survives at empty — only `soul_file_layers`
    #    should be gone.
    assert not await _table_exists(database_pool, "soul_file_layers"), (
        "soul_file_layers should NOT exist after downgrade base"
    )

    # 4. Re-run upgrade head so the rest of the session sees seeded
    #    state. Subsequent tests rely on this.
    result = _run_alembic("upgrade head")
    assert result.returncode == 0, (
        f"alembic upgrade head (re-run) failed:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )

    assert await _table_exists(database_pool, "soul_file_layers"), (
        "soul_file_layers should exist after re-applied upgrade head"
    )


# ===========================================================================
# RELATED FILES:
#   conftest.py                       — session-scoped fixture that runs
#                                       the initial alembic upgrade head
#   ../app/migrations/versions/001_initial_schema_and_seed.py
#                                    — the migration this test round-trips
#   ../app/migrations/env.py          — the Alembic env env this test
#                                       exercises via the CLI subprocess
#   ../alembic.ini                    — alembic config the CLI reads
# ===========================================================================
