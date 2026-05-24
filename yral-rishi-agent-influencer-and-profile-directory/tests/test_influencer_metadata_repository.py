# ---------------------------------------------------------------------------
# test_influencer_metadata_repository.py — unit tests for the 3 read
# methods in `app/repository/influencer_metadata_repository.py`.
#
# ⭐ START HERE: tests against a real testcontainers Postgres (J1-HOT
# per Session-5's precedent), NOT mocked asyncpg. Reasoning:
#   - The repository's value is in the SQL it issues; mocking asyncpg
#     would test the Python wrapper around the SQL but not the SQL
#     itself.
#   - testcontainers-Postgres adds <2s session-setup overhead but
#     catches real SQL syntax errors, column-name typos, ORDER BY
#     semantics, partial-index behaviour, etc.
#   - Same pattern Session 5 used for `user-memory-service/tests/`.
#
# COVERAGE PER METHOD:
#   get_by_id(influencer_id):
#     - Returns the row when it exists.
#     - Returns None when no row matches.
#     - All 9 contract-shape columns round-trip via the Pydantic model.
#
#   list_paginated(limit, offset):
#     - Returns ALL rows when offset=0 + limit > row count.
#     - Honors offset (skips first N).
#     - Honors limit (truncates to N).
#     - Returns empty list when offset >= row count.
#     - Ordered by `id ASC` deterministically.
#     - Returns BOTH active and discontinued rows (filtering is the
#       caller's job per the contract).
#
#   list_trending(limit):
#     - Filters to is_active='active' only.
#     - Orders by follower_count DESC.
#     - Honors limit.
#
# B7 NOTE — `pytest-asyncio` test functions need `async def` per the
# pytest-asyncio convention; the `async`/`await` keywords are Python
# language features, not abbreviations, and are not subject to B2.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import asyncpg
import pytest

from app.models.influencer_metadata import InfluencerMetadata
from app.repository import influencer_metadata_repository


# ===========================================================================
# Helper — insert one fully-populated test row
# ===========================================================================


async def _insert_test_influencer(
    pool: asyncpg.Pool,
    *,
    identifier: str,
    display_name: str = "Test Influencer",
    bio: str = "Test biography line for the test fixture.",
    avatar_url: str = "https://example.invalid/avatar.png",
    archetype: str = "companion",
    is_nsfw: bool = False,
    follower_count: int = 0,
    creator_user_id: str | None = None,
    is_active: str = "active",
) -> None:
    """Insert one `influencer_metadata` row with the given values.

    WHAT: shorthand for the verbose 9-column INSERT statement so
          individual tests stay readable.
    WHEN: called from per-test row setup.
    WHY:  every test needs to seed at least one row + the v2-only
          audit columns (`source`, `created_at`, `updated_at`) all have
          DEFAULTs so the INSERT only specifies the contract-shape
          columns + the test sets only the fields it cares about.
    """
    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO influencer_metadata (
                id, display_name, bio, avatar_url, archetype,
                is_nsfw, follower_count, creator_user_id, is_active
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9
            )
            """,
            identifier,
            display_name,
            bio,
            avatar_url,
            archetype,
            is_nsfw,
            follower_count,
            creator_user_id,
            is_active,
        )


# ===========================================================================
# get_by_id
# ===========================================================================


@pytest.mark.asyncio
async def test_get_by_id_returns_row_when_present(
    database_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: get_by_id returns a populated `InfluencerMetadata` instance
          when the row exists.
    WHEN: every chat turn that lands on a real influencer (via the
          orchestrator's run_turn → soul-file → directory chain).
    WHY:  by-id lookup is the hottest read path in production.
          Regression here breaks every chat request.
    """
    # Inject the testcontainer pool into the repository's get_pool()
    # callsite. Mirrors Session-5's test_client pre-injection pattern.
    import app.database as database_module
    database_module._pool = database_pool

    await _insert_test_influencer(
        database_pool,
        identifier="test-influencer-get-by-id-happy-path",
        display_name="Get-By-Id Test",
        bio="A bio for the happy-path test.",
        archetype="therapist",
        is_nsfw=False,
        follower_count=42,
        creator_user_id="test-creator-001",
        is_active="active",
    )

    result = await influencer_metadata_repository.get_by_id(
        "test-influencer-get-by-id-happy-path"
    )

    # Type assertion + every contract-shape field round-tripped.
    assert isinstance(result, InfluencerMetadata)
    assert result.id == "test-influencer-get-by-id-happy-path"
    assert result.display_name == "Get-By-Id Test"
    assert result.bio == "A bio for the happy-path test."
    assert result.archetype == "therapist"
    assert result.is_nsfw is False
    assert result.follower_count == 42
    assert result.creator_user_id == "test-creator-001"
    assert result.is_active == "active"

    # Cleanup — un-inject the pool to avoid leakage across tests.
    database_module._pool = None


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_missing(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: get_by_id returns None when no row matches the given id.
    WHEN: a request for an influencer that doesn't exist (404 path in
          Chunk B endpoint).
    WHY:  None is the contract between the repository + the endpoint —
          the endpoint maps None to HTTP 404 with the `not_found` error
          code per the parity contract. Returning anything else
          (raising, returning empty model) would break that mapping.
    """
    import app.database as database_module
    database_module._pool = database_pool

    # No INSERT — table is empty (TRUNCATE in conftest's database_pool
    # fixture cleared it before yielding).
    result = await influencer_metadata_repository.get_by_id(
        "this-id-does-not-exist-anywhere"
    )

    assert result is None

    database_module._pool = None


# ===========================================================================
# list_paginated
# ===========================================================================


@pytest.mark.asyncio
async def test_list_paginated_returns_all_rows_when_limit_exceeds_count(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: list_paginated returns every row when `limit` is larger
          than the table's row count + `offset=0`.
    WHEN: small catalog (early days) + mobile's default limit=20.
    WHY:  pins the "no truncation when not needed" case. A regression
          that always truncated would silently drop influencers.
    """
    import app.database as database_module
    database_module._pool = database_pool

    await _insert_test_influencer(
        database_pool, identifier="influencer-001-paginated"
    )
    await _insert_test_influencer(
        database_pool, identifier="influencer-002-paginated"
    )
    await _insert_test_influencer(
        database_pool, identifier="influencer-003-paginated"
    )

    results = await influencer_metadata_repository.list_paginated(
        limit=20, offset=0
    )

    assert len(results) == 3
    # Ordered by `id ASC` per the repository's documented contract.
    returned_identifiers = [row.id for row in results]
    assert returned_identifiers == [
        "influencer-001-paginated",
        "influencer-002-paginated",
        "influencer-003-paginated",
    ]

    database_module._pool = None


@pytest.mark.asyncio
async def test_list_paginated_honors_offset_and_limit_bounds(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: list_paginated skips `offset` rows + truncates to `limit`
          rows, returning a deterministic slice via the `id ASC`
          ordering.
    WHEN: mobile paginates page-by-page.
    WHY:  the pagination contract per Q4 — without deterministic
          ordering, two requests with the same (limit, offset) could
          return different pages (Postgres makes no ordering guarantee
          on a bare SELECT). Regression would surface as duplicate or
          missing rows on page boundaries.
    """
    import app.database as database_module
    database_module._pool = database_pool

    # Seed 5 rows with predictable ids so we can assert exact slice.
    for sequence_number in range(5):
        await _insert_test_influencer(
            database_pool,
            identifier=f"influencer-pagination-{sequence_number:02d}",
        )

    # limit=2, offset=1 → should return rows [01, 02].
    results = await influencer_metadata_repository.list_paginated(
        limit=2, offset=1
    )

    assert len(results) == 2
    assert [row.id for row in results] == [
        "influencer-pagination-01",
        "influencer-pagination-02",
    ]

    database_module._pool = None


@pytest.mark.asyncio
async def test_list_paginated_returns_empty_when_offset_exceeds_row_count(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: list_paginated returns an empty list when `offset` is
          larger than the table's row count.
    WHEN: mobile derives "no more pages" from `len(items) < limit` —
          this case surfaces as `len(items) == 0` past the last page.
    WHY:  pins the empty-list-not-None behaviour. An exception or None
          return here would break the mobile pagination loop.
    """
    import app.database as database_module
    database_module._pool = database_pool

    await _insert_test_influencer(
        database_pool, identifier="influencer-only-one-row"
    )

    # 1 row exists; asking for rows 5..15 returns nothing.
    results = await influencer_metadata_repository.list_paginated(
        limit=10, offset=5
    )

    assert results == []

    database_module._pool = None


@pytest.mark.asyncio
async def test_list_paginated_returns_both_active_and_discontinued_rows(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: list_paginated returns rows regardless of `is_active`
          value — filtering of discontinued influencers happens
          mobile-side per the parity contract.
    WHEN: mobile renders the full catalog; the `is_active` field tells
          mobile which rows to grey-out / hide.
    WHY:  a repository regression that filtered to is_active='active'
          would drop the 263 discontinued chat-ai rows from the
          catalog without warning. Pins the no-filter behaviour.
    """
    import app.database as database_module
    database_module._pool = database_pool

    await _insert_test_influencer(
        database_pool,
        identifier="influencer-active-row",
        is_active="active",
    )
    await _insert_test_influencer(
        database_pool,
        identifier="influencer-discontinued-row",
        is_active="discontinued",
    )

    results = await influencer_metadata_repository.list_paginated(
        limit=20, offset=0
    )

    assert len(results) == 2
    is_active_values = {row.is_active for row in results}
    assert is_active_values == {"active", "discontinued"}

    database_module._pool = None


# ===========================================================================
# list_trending
# ===========================================================================


@pytest.mark.asyncio
async def test_list_trending_orders_by_follower_count_descending(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: list_trending returns rows ordered by `follower_count` DESC.
    WHEN: every mobile call to `GET /v1/influencers/trending`.
    WHY:  the ordering IS the trending semantic. A regression that
          ordered the wrong direction or used the wrong column would
          break the entire endpoint's UX without any 5xx signal —
          mobile would show "trending" as a stable random order.
    """
    import app.database as database_module
    database_module._pool = database_pool

    await _insert_test_influencer(
        database_pool,
        identifier="influencer-medium-followers",
        follower_count=500,
    )
    await _insert_test_influencer(
        database_pool,
        identifier="influencer-most-followers",
        follower_count=10000,
    )
    await _insert_test_influencer(
        database_pool,
        identifier="influencer-fewest-followers",
        follower_count=10,
    )

    results = await influencer_metadata_repository.list_trending(limit=20)

    assert len(results) == 3
    # Most → least followers.
    assert [row.id for row in results] == [
        "influencer-most-followers",
        "influencer-medium-followers",
        "influencer-fewest-followers",
    ]
    # And the follower_count values are strictly descending.
    follower_counts = [row.follower_count for row in results]
    assert follower_counts == sorted(follower_counts, reverse=True)

    database_module._pool = None


@pytest.mark.asyncio
async def test_list_trending_excludes_discontinued_rows(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: list_trending filters out rows with `is_active='discontinued'`
          regardless of their `follower_count`.
    WHEN: every mobile call to `GET /v1/influencers/trending`.
    WHY:  pins the partial-index behaviour. A discontinued influencer
          with a huge `follower_count` value (e.g. a banned-from-active-
          surface celebrity influencer with millions of historical
          followers) must NOT appear in the trending UI. Without this
          filter the partial trending index would still be technically
          correct but the query semantics would be wrong.
    """
    import app.database as database_module
    database_module._pool = database_pool

    await _insert_test_influencer(
        database_pool,
        identifier="influencer-active-low-count",
        follower_count=100,
        is_active="active",
    )
    await _insert_test_influencer(
        database_pool,
        identifier="influencer-discontinued-huge-count",
        follower_count=1_000_000,
        is_active="discontinued",
    )

    results = await influencer_metadata_repository.list_trending(limit=20)

    # Only the active row appears, despite the discontinued row having
    # the higher follower_count.
    assert len(results) == 1
    assert results[0].id == "influencer-active-low-count"
    assert results[0].is_active == "active"

    database_module._pool = None


@pytest.mark.asyncio
async def test_list_trending_honors_limit(
    database_pool: asyncpg.Pool,
) -> None:
    """WHAT: list_trending truncates to `limit` rows.
    WHEN: mobile's default limit=20 against catalogs larger than 20
          active rows.
    WHY:  pins the truncation contract. A regression that returned all
          rows would scale O(n) on the response payload size.
    """
    import app.database as database_module
    database_module._pool = database_pool

    # Seed 5 active rows.
    for sequence_number in range(5):
        await _insert_test_influencer(
            database_pool,
            identifier=f"influencer-trending-limit-{sequence_number:02d}",
            follower_count=sequence_number * 100,
            is_active="active",
        )

    # Request limit=2 → should return the top 2 by follower_count DESC.
    results = await influencer_metadata_repository.list_trending(limit=2)

    assert len(results) == 2
    # The top 2 are the ones with follower_count = 400 + 300 (rows 04 + 03).
    assert [row.id for row in results] == [
        "influencer-trending-limit-04",
        "influencer-trending-limit-03",
    ]

    database_module._pool = None


# ===========================================================================
# RELATED FILES:
#   conftest.py                              — database_pool fixture (the
#                                               testcontainers-backed pool
#                                               every test above injects)
#   ../app/repository/influencer_metadata_repository.py
#                                            — the module under test
#   ../app/models/influencer_metadata.py     — Pydantic model returned by
#                                               every repository method
#   ../app/database.py                       — `_pool` singleton each test
#                                               injects into for the
#                                               repository's `get_pool()` to
#                                               find
#   ../app/migrations/versions/001_initial_schema.py
#                                            — schema + indexes the queries
#                                               ride on (partial trending
#                                               index in particular)
# ===========================================================================
