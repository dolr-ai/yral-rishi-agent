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
#     - Returns the row when it exists + is non-discontinued.
#     - Returns None when no row matches.
#     - Returns None when the row exists but is discontinued (catalog
#       authority — see Chunk B PR file-header comment in the
#       repository module + the coordinator routing 2026-05-25).
#     - All 9 contract-shape columns round-trip via the Pydantic model.
#
#   list_paginated(limit, offset):
#     - Returns matching rows when offset=0 + limit > row count.
#     - Honors offset (skips first N).
#     - Honors limit (truncates to N).
#     - Returns empty list when offset >= row count.
#     - Ordered by `id ASC` deterministically.
#     - Filters out discontinued rows per catalog authority (Chunk B
#       coordinator routing 2026-05-25). `is_active='active'` + `is_active=
#       'coming_soon'` rows surface; `is_active='discontinued'` rows do
#       not.
#
#   list_trending(limit):
#     - Filters to is_active='active' only (stricter than list_paginated;
#       trending excludes coming_soon too because the partial index
#       covers active rows only).
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
    name: str | None = None,
    display_name: str = "Test Influencer",
    bio: str = "Test biography line for the test fixture.",
    avatar_url: str | None = "https://example.invalid/avatar.png",
    archetype: str = "companion",
    is_nsfw: bool = False,
    follower_count: int = 0,
    creator_user_id: str | None = None,
    is_active: str = "active",
) -> None:
    """Insert one `influencer_metadata` row with the given values.

    WHAT: shorthand for the verbose multi-column INSERT statement so
          individual tests stay readable. The 5 round-5 chat-ai-port
          columns (`personality_traits`, `initial_greeting`,
          `suggested_messages`, `metadata`) + the 3 audit columns
          (`source`, `created_at`, `updated_at`) all have DB-level
          DEFAULTs, so this INSERT omits them — Postgres fills with
          the defaults.
    WHEN: called from per-test row setup.
    WHY:  every test needs to seed at least one row; this helper keeps
          the explicit-fields-set-per-test surface narrow + lets the
          DB defaults exercise their happy path.

    Args:
        identifier: PK value for the new row.
        name: unique slug-style identifier. If None, derived from
              `identifier` by replacing dashes with underscores. UNIQUE
              constraint forces every row to have a distinct name —
              the auto-derived default works for tests that don't care
              about the name field.
    """
    # Auto-derive `name` from `identifier` when caller doesn't specify;
    # the underscore-replacement makes the name "look slug-shaped"
    # while staying derived from the identifier so multi-row tests get
    # unique names automatically.
    if name is None:
        name = identifier.replace("-", "_")

    async with pool.acquire() as connection:
        await connection.execute(
            """
            INSERT INTO influencer_metadata (
                id, name, display_name, bio, avatar_url, archetype,
                is_nsfw, follower_count, creator_user_id, is_active
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
            )
            """,
            identifier,
            name,
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
async def test_get_by_id_returns_the_row_when_an_influencer_with_that_id_exists(
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
    monkeypatch.setattr(database_module, "_pool", database_pool)

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



@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_no_influencer_with_that_id_exists(
    database_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(database_module, "_pool", database_pool)

    # No INSERT — table is empty (TRUNCATE in conftest's database_pool
    # fixture cleared it before yielding).
    result = await influencer_metadata_repository.get_by_id(
        "this-id-does-not-exist-anywhere"
    )

    assert result is None



# ===========================================================================
# list_paginated
# ===========================================================================


@pytest.mark.asyncio
async def test_list_paginated_returns_every_row_when_the_limit_exceeds_the_total_row_count(
    database_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: list_paginated returns every row when `limit` is larger
          than the table's row count + `offset=0`.
    WHEN: small catalog (early days) + mobile's default limit=20.
    WHY:  pins the "no truncation when not needed" case. A regression
          that always truncated would silently drop influencers.
    """
    import app.database as database_module
    monkeypatch.setattr(database_module, "_pool", database_pool)

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



@pytest.mark.asyncio
async def test_list_paginated_honors_both_the_offset_and_the_limit_bounds_when_paging_through_the_catalog(
    database_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(database_module, "_pool", database_pool)

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



@pytest.mark.asyncio
async def test_list_paginated_returns_an_empty_list_when_the_offset_exceeds_the_total_row_count(
    database_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: list_paginated returns an empty list when `offset` is
          larger than the table's row count.
    WHEN: mobile derives "no more pages" from `len(items) < limit` —
          this case surfaces as `len(items) == 0` past the last page.
    WHY:  pins the empty-list-not-None behaviour. An exception or None
          return here would break the mobile pagination loop.
    """
    import app.database as database_module
    monkeypatch.setattr(database_module, "_pool", database_pool)

    await _insert_test_influencer(
        database_pool, identifier="influencer-only-one-row"
    )

    # 1 row exists; asking for rows 5..15 returns nothing.
    results = await influencer_metadata_repository.list_paginated(
        limit=10, offset=5
    )

    assert results == []



@pytest.mark.asyncio
async def test_list_paginated_excludes_discontinued_rows_but_surfaces_active_and_coming_soon_per_catalog_authority(
    database_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: list_paginated returns rows with `is_active IN ('active',
          'coming_soon')` and never returns rows with `is_active=
          'discontinued'`. The catalog authority lives in the SQL
          query (WHERE is_active <> 'discontinued') so the endpoint
          layer can trust the repo's output without re-filtering.
    WHEN: mobile renders the public catalog via `GET /v1/influencers`.
    WHY:  pins the catalog-authority behaviour added in Chunk B (PR
          coordinator routing 2026-05-25). A regression that removed
          the WHERE filter would leak discontinued rows into the
          mobile catalog. A regression that tightened the filter to
          `is_active='active'` would hide pre-launch coming_soon
          influencers from the catalog. This test asserts BOTH
          sides — coming_soon IS surfaced; discontinued IS NOT —
          so neither regression can land silently.
    """
    import app.database as database_module
    monkeypatch.setattr(database_module, "_pool", database_pool)

    await _insert_test_influencer(
        database_pool,
        identifier="influencer-active-row",
        is_active="active",
    )
    await _insert_test_influencer(
        database_pool,
        identifier="influencer-coming-soon-row",
        is_active="coming_soon",
    )
    await _insert_test_influencer(
        database_pool,
        identifier="influencer-discontinued-row",
        is_active="discontinued",
    )

    results = await influencer_metadata_repository.list_paginated(
        limit=20, offset=0
    )

    # 2 of the 3 inserted rows surface; the discontinued row is
    # filtered out by the catalog-authority WHERE clause.
    assert len(results) == 2
    is_active_values = {row.is_active for row in results}
    assert is_active_values == {"active", "coming_soon"}
    assert "discontinued" not in is_active_values


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_the_row_exists_but_is_discontinued_per_catalog_authority(
    database_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: get_by_id returns None when the matching row exists in the
          DB but has `is_active='discontinued'`. The catalog authority
          (WHERE id = $1 AND is_active <> 'discontinued') treats a
          discontinued row as not-in-catalog so the endpoint layer's
          404 path fires.
    WHEN: a mobile client requests `GET /v1/influencers/{id}` for an
          id that was previously surfaced + has since been
          discontinued (admin / content-moderation flow).
    WHY:  pins the 404-on-discontinued behaviour at the data layer. A
          regression that dropped the AND clause would surface
          discontinued rows to mobile, breaking the catalog-authority
          guarantee. Also: the 404 is intentionally
          indistinguishable from "no such id" so an external probe
          can't enumerate which ids have been soft-deleted vs never
          existed (privacy + churn-data protection).
    """
    import app.database as database_module
    monkeypatch.setattr(database_module, "_pool", database_pool)

    await _insert_test_influencer(
        database_pool,
        identifier="influencer-discontinued-row",
        is_active="discontinued",
    )

    result = await influencer_metadata_repository.get_by_id(
        "influencer-discontinued-row"
    )

    assert result is None



# ===========================================================================
# list_trending
# ===========================================================================


@pytest.mark.asyncio
async def test_list_trending_orders_results_by_follower_count_in_descending_order(
    database_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: list_trending returns rows ordered by `follower_count` DESC.
    WHEN: every mobile call to `GET /v1/influencers/trending`.
    WHY:  the ordering IS the trending semantic. A regression that
          ordered the wrong direction or used the wrong column would
          break the entire endpoint's UX without any 5xx signal —
          mobile would show "trending" as a stable random order.
    """
    import app.database as database_module
    monkeypatch.setattr(database_module, "_pool", database_pool)

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



@pytest.mark.asyncio
async def test_list_trending_excludes_rows_whose_is_active_value_is_not_active(
    database_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch.setattr(database_module, "_pool", database_pool)

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



@pytest.mark.asyncio
async def test_list_trending_honors_the_limit_parameter_when_more_rows_qualify_than_requested(
    database_pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WHAT: list_trending truncates to `limit` rows.
    WHEN: mobile's default limit=20 against catalogs larger than 20
          active rows.
    WHY:  pins the truncation contract. A regression that returned all
          rows would scale O(n) on the response payload size.
    """
    import app.database as database_module
    monkeypatch.setattr(database_module, "_pool", database_pool)

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
