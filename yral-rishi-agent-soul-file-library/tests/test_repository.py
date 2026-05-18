# ---------------------------------------------------------------------------
# test_repository.py — coverage for `app/repository/soul_file_repository.py`.
#
# ⭐ START HERE: this file exercises the repository's read + write paths:
#
#   READ
#     test_get_current_returns_seeded_layer_1
#     test_get_current_returns_none_for_missing_layer_3
#     test_list_versions_returns_history_descending
#
#   WRITE
#     test_create_new_version_flips_is_current_correctly
#     test_create_new_version_initial_row_starts_at_version_1
#     test_retire_current_marks_row_history
#     test_partial_unique_index_blocks_concurrent_double_current
#
# Per J1 the soul-file-library is WARM-tier (50-60% floor); the
# composer is the chat hot-path but its inputs are cacheable. The
# read tests cover the composer's hot-path lookups; the write tests
# cover the invariant the partial-unique-index enforces.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import asyncpg
import pytest

from app.repository.soul_file_repository import (
    LAYER_ARCHETYPE,
    LAYER_GLOBAL,
    LAYER_PER_INFLUENCER,
    LAYER_PER_USER_SEGMENT,
    create_new_version,
    get_current,
    list_versions,
    retire_current,
)


# ===========================================================================
# READ tests
# ===========================================================================


@pytest.mark.asyncio
async def test_get_current_returns_seeded_layer_1(
    db_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: get_current(1, '') returns the seeded global row.
    WHEN: after conftest's truncate-and-reseed fixture runs.
    WHY:  proves the migration's L1 seed lands + the repo's basic
          SELECT path works.
    """
    row = await get_current(LAYER_GLOBAL, "")
    assert row is not None
    assert row.layer == 1
    assert row.scope_key == ""
    assert row.is_current is True
    assert row.version == 1
    assert "Layer 1 placeholder" in row.body


@pytest.mark.asyncio
async def test_get_current_returns_none_for_missing_layer_3(
    db_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: get_current(3, '<random uuid>') returns None.
    WHEN: L3 has no seeded rows on Day 4 (data port deferred).
    WHY:  composer turns this None into the 404 InfluencerSoulFileMissingError.
    """
    row = await get_current(LAYER_PER_INFLUENCER, "00000000-0000-0000-0000-000000000001")
    assert row is None


@pytest.mark.asyncio
async def test_list_versions_returns_history_descending(
    db_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: list_versions returns rows ordered version DESC.
    WHEN: after we bump a layer multiple times.
    WHY:  rollback runbook needs history with current row first.
    """
    # Start state: layer 2 'companion' is at version 1 (from seed).
    # Bump it twice. Final state should be 3 rows total: v3 current,
    # v2 + v1 non-current.
    await create_new_version(LAYER_ARCHETYPE, "companion", body="bump v2")
    await create_new_version(LAYER_ARCHETYPE, "companion", body="bump v3")

    history = await list_versions(LAYER_ARCHETYPE, "companion")
    assert len(history) == 3
    assert [r.version for r in history] == [3, 2, 1]
    assert history[0].is_current is True
    assert all(r.is_current is False for r in history[1:])


# ===========================================================================
# WRITE tests
# ===========================================================================


@pytest.mark.asyncio
async def test_create_new_version_flips_is_current_correctly(
    db_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: after a bump, exactly one row at (layer, scope_key) is current.
    WHEN: bumping an already-seeded slot.
    WHY:  the partial unique index would reject any state with >1
          current rows; this test proves the repo's
          retire-then-insert keeps the invariant.
    """
    new_row = await create_new_version(
        LAYER_ARCHETYPE, "therapist", body="new therapist body"
    )
    assert new_row.is_current is True
    assert new_row.version == 2

    history = await list_versions(LAYER_ARCHETYPE, "therapist")
    current_rows = [r for r in history if r.is_current]
    assert len(current_rows) == 1
    assert current_rows[0].version == 2


@pytest.mark.asyncio
async def test_create_new_version_initial_row_starts_at_version_1(
    db_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: the first insert into an empty slot gets version 1.
    WHEN: inserting an L3 row (no L3 seeds exist).
    WHY:  proves COALESCE(MAX(version), 0) + 1 starts the count at
          1 on an empty slot. Without the COALESCE, MAX would be NULL
          and the new version would be NULL.
    """
    new_row = await create_new_version(
        LAYER_PER_INFLUENCER,
        "11111111-1111-1111-1111-111111111111",
        body="first L3 body",
        archetype="companion",
    )
    assert new_row.version == 1
    assert new_row.is_current is True
    assert new_row.archetype == "companion"


@pytest.mark.asyncio
async def test_retire_current_marks_row_history(
    db_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: retire_current flips current → False without inserting a replacement.
    WHEN: operator decides to retire an archetype with no replacement.
    WHY:  proves the slot ends empty; composer's get_current(...) will
          return None for that slot afterwards → composer raises a
          data-integrity error.
    """
    retired = await retire_current(LAYER_ARCHETYPE, "coach")
    assert retired is True

    row = await get_current(LAYER_ARCHETYPE, "coach")
    assert row is None

    history = await list_versions(LAYER_ARCHETYPE, "coach")
    assert len(history) == 1
    assert history[0].is_current is False


@pytest.mark.asyncio
async def test_partial_unique_index_blocks_concurrent_double_current(
    db_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: directly inserting a second is_current=TRUE row at the same
          (layer, scope_key) raises asyncpg.UniqueViolationError.
    WHEN: simulating a race / direct-SQL bypass of create_new_version.
    WHY:  proves the partial unique index is the durable safety net
          even if a buggy caller skips the retire-then-insert pattern.
    """
    async with db_pool.acquire() as conn:
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO soul_file_layers "
                "(layer, scope_key, body, version, is_current) "
                "VALUES (1, '', 'duplicate global', 99, TRUE);"
            )


# ===========================================================================
# RELATED FILES:
#   conftest.py                       — db_pool + app_pool_bound fixtures
#   ../app/repository/soul_file_repository.py
#                                    — module under test
#   ../app/migrations/versions/001_initial_schema_and_seed.py
#                                    — the seed rows these tests start from
#                                      (re-seeded per-test via conftest)
# ===========================================================================
