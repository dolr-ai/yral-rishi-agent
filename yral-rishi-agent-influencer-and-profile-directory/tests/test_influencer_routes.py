# ---------------------------------------------------------------------------
# test_influencer_routes.py — endpoint tests for Chunk B's catalog routes
# (`GET /v1/influencers` + `GET /v1/influencers/{id}`).
#
# ⭐ START HERE: integration tests against the testcontainer Postgres via
# the FastAPI app + httpx.AsyncClient (no network socket, no uvicorn).
# Each test seeds rows via a direct asyncpg connection, drives the HTTP
# route via the `test_client` fixture, asserts the wire-shape response.
#
# COVERAGE:
#   list endpoint (GET /v1/influencers):
#     - happy path: 3 active rows seeded → 200 + 3 rows in body
#     - pagination: limit + offset honored
#     - empty result when offset > row count
#     - catalog authority: discontinued rows filtered out; coming_soon
#       surfaced with is_active="active" wire mapping
#     - 422 when ANY of the 4 required internal-call headers is missing
#       (parameterised across X-User-Id / X-Internal-Caller /
#       X-Request-Id / X-Trace-Id per round-2 CONCERN closure)
#     - 422 on out-of-range limit / negative offset
#     - Cache-Control: max-age=300 header set on 200 responses per
#       00-api-contract.md (round-2 CONCERN closure)
#
#   single-fetch endpoint (GET /v1/influencers/{id}):
#     - happy path: seeded row → 200 + full InfluencerResponse shape
#     - 404 on missing id
#     - 404 on discontinued id (indistinguishable from missing per
#       privacy/soft-delete-enumeration protection)
#     - coming_soon row returned with is_active="active"
#     - NULL avatar_url coerced to "" on the wire
#     - 422 when ANY of the 4 required internal-call headers is missing
#       (parameterised same as the list endpoint)
#
# B7/B2 NOTE (per PR #154 carve-out):
# Test files are exempt from B7 (no file-header WHAT/WHEN/WHY ceremony
# required on every test function) + B2 (idiomatic abbreviations like
# `req` / `resp` permitted in test-only scope). Sentence-style J3 test
# names + adequate docstrings on the load-bearing cases are still
# required for readability.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import asyncpg
import pytest
from httpx import AsyncClient


# Headers the routes require on every internal call. Bundled into a
# constant so each test passes the canonical 4-set without retyping.
# Tests that want to verify "missing X header → 422" omit one key from
# this dict explicitly via dict-spread + pop.
_INTERNAL_HEADERS = {
    "X-User-Id": "test-user-uuid-0001",
    "X-Internal-Caller": "yral-rishi-agent-public-api",
    "X-Request-Id": "test-request-id-0001",
    "X-Trace-Id": "test-trace-id-0001",
}


# ===========================================================================
# Helper — seed one fully-populated row via direct asyncpg
# ===========================================================================


async def _insert_test_influencer(
    connection: asyncpg.Connection,
    *,
    identifier: str,
    name: str | None = None,
    display_name: str = "Test Influencer",
    bio: str = "A test influencer for endpoint tests.",
    avatar_url: str | None = "https://example.test/avatar.png",
    archetype: str = "companion",
    is_nsfw: bool = False,
    follower_count: int = 100,
    creator_user_id: str | None = None,
    is_active: str = "active",
) -> None:
    """Insert one row directly via asyncpg.

    Endpoint tests share a single seed shape; the per-call kwargs let
    each test override the relevant fields. The `name` parameter
    defaults to a slug derived from `identifier` so the UNIQUE
    constraint on `name` is satisfied without per-test gymnastics.
    """
    if name is None:
        name = identifier.replace("-", "_")
    await connection.execute(
        """
        INSERT INTO influencer_metadata (
            id, name, display_name, bio, avatar_url, archetype,
            is_nsfw, follower_count, creator_user_id, is_active
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
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


async def _direct_connect(dsn: str) -> asyncpg.Connection:
    """Open a one-off asyncpg connection for seeding rows during a test."""
    return await asyncpg.connect(dsn=dsn)


# ===========================================================================
# GET /v1/influencers — list endpoint
# ===========================================================================


@pytest.mark.asyncio
async def test_get_list_influencers_returns_all_catalog_visible_rows_when_no_pagination_filter_applies(
    test_client: AsyncClient,
    postgres_connection_string: str,
) -> None:
    """Happy path: 3 active rows seeded → 200 + 3 rows in flat list body."""
    connection = await _direct_connect(postgres_connection_string)
    try:
        for index in range(3):
            await _insert_test_influencer(
                connection,
                identifier=f"happy-path-row-{index}",
            )
    finally:
        await connection.close()

    response = await test_client.get(
        "/v1/influencers", headers=_INTERNAL_HEADERS
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 3
    # InfluencerResponse wire shape: 9 fields exactly; extra keys would
    # mean the response model failed to filter the persistence shape.
    expected_keys = {
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
    assert set(body[0].keys()) == expected_keys


@pytest.mark.asyncio
async def test_get_list_influencers_honors_limit_and_offset_query_parameters(
    test_client: AsyncClient,
    postgres_connection_string: str,
) -> None:
    """Seed 5 rows; verify limit=2 + offset=2 returns the 3rd and 4th rows."""
    connection = await _direct_connect(postgres_connection_string)
    try:
        for index in range(5):
            await _insert_test_influencer(
                connection,
                # zero-padded id so ORDER BY id ASC is deterministic
                identifier=f"pagination-row-{index:02d}",
            )
    finally:
        await connection.close()

    response = await test_client.get(
        "/v1/influencers?limit=2&offset=2", headers=_INTERNAL_HEADERS
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 2
    assert body[0]["id"] == "pagination-row-02"
    assert body[1]["id"] == "pagination-row-03"


@pytest.mark.asyncio
async def test_get_list_influencers_returns_empty_list_when_offset_exceeds_total_row_count(
    test_client: AsyncClient,
    postgres_connection_string: str,
) -> None:
    """Empty result when offset past end (mobile derives no-more-pages)."""
    connection = await _direct_connect(postgres_connection_string)
    try:
        for index in range(3):
            await _insert_test_influencer(
                connection, identifier=f"row-{index}"
            )
    finally:
        await connection.close()

    response = await test_client.get(
        "/v1/influencers?limit=20&offset=100", headers=_INTERNAL_HEADERS
    )

    assert response.status_code == 200, response.text
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_list_influencers_excludes_discontinued_rows_but_surfaces_coming_soon_per_catalog_authority(
    test_client: AsyncClient,
    postgres_connection_string: str,
) -> None:
    """Seed one row of each tri-state; verify discontinued is filtered + coming_soon maps to is_active=active."""
    connection = await _direct_connect(postgres_connection_string)
    try:
        await _insert_test_influencer(
            connection,
            identifier="row-active",
            is_active="active",
        )
        await _insert_test_influencer(
            connection,
            identifier="row-coming-soon",
            is_active="coming_soon",
        )
        await _insert_test_influencer(
            connection,
            identifier="row-discontinued",
            is_active="discontinued",
        )
    finally:
        await connection.close()

    response = await test_client.get(
        "/v1/influencers", headers=_INTERNAL_HEADERS
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # 2 of the 3 rows surface: discontinued is filtered out at the repo
    # layer (catalog authority). The 2 visible rows BOTH have is_active=
    # "active" on the wire because the response model maps
    # coming_soon → active per the round-9 wire-vocabulary policy.
    assert len(body) == 2
    returned_ids = {row["id"] for row in body}
    assert returned_ids == {"row-active", "row-coming-soon"}
    assert "row-discontinued" not in returned_ids
    wire_is_active_values = {row["is_active"] for row in body}
    assert wire_is_active_values == {"active"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_header_name",
    ["X-User-Id", "X-Internal-Caller", "X-Request-Id", "X-Trace-Id"],
)
async def test_get_list_influencers_returns_422_when_any_required_internal_call_header_is_missing(
    test_client: AsyncClient,
    missing_header_name: str,
) -> None:
    """FastAPI 422 when ANY of the 4 required internal-call headers is absent.

    Parameterised over all 4 headers (X-User-Id, X-Internal-Caller,
    X-Request-Id, X-Trace-Id) per the round-1 CONCERN closure on PR
    #157: the file-header claims coverage of the full 4-header set
    + this parameterisation makes the claim true (round-1 had only
    2 of the 4 covered on the list endpoint).
    """
    headers_without_one = {
        key: value
        for key, value in _INTERNAL_HEADERS.items()
        if key != missing_header_name
    }

    response = await test_client.get(
        "/v1/influencers", headers=headers_without_one
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_list_influencers_sets_cache_control_max_age_300_header_per_contract(
    test_client: AsyncClient,
    postgres_connection_string: str,
) -> None:
    """List endpoint sets Cache-Control: max-age=300 per 00-api-contract.md.

    Per `00-api-contract.md` the public catalog endpoint is annotated
    `Cache-Control 300s`. Round-1 CONCERN closure on PR #157: the
    round-1 implementation returned the list without setting the
    header. Round-2 sets it on every 200 response so mobile + edge
    caches keep the catalog response for 5 minutes (lowers
    directory-RPC traffic; catalog data changes slowly).
    """
    connection = await _direct_connect(postgres_connection_string)
    try:
        await _insert_test_influencer(
            connection, identifier="cache-control-row"
        )
    finally:
        await connection.close()

    response = await test_client.get(
        "/v1/influencers", headers=_INTERNAL_HEADERS
    )

    assert response.status_code == 200, response.text
    assert response.headers.get("cache-control") == "max-age=300"


@pytest.mark.asyncio
async def test_get_list_influencers_returns_422_when_limit_exceeds_the_upper_bound(
    test_client: AsyncClient,
) -> None:
    """limit=101 violates the ge=1, le=100 bound; FastAPI 422s."""
    response = await test_client.get(
        "/v1/influencers?limit=101", headers=_INTERNAL_HEADERS
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_list_influencers_returns_422_when_offset_is_negative(
    test_client: AsyncClient,
) -> None:
    """offset=-1 violates the ge=0 bound; FastAPI 422s."""
    response = await test_client.get(
        "/v1/influencers?offset=-1", headers=_INTERNAL_HEADERS
    )

    assert response.status_code == 422


# ===========================================================================
# GET /v1/influencers/{id} — single-fetch endpoint
# ===========================================================================


@pytest.mark.asyncio
async def test_get_influencer_by_id_returns_the_row_when_an_active_influencer_with_that_id_exists(
    test_client: AsyncClient,
    postgres_connection_string: str,
) -> None:
    """Happy path: 200 + full 9-field InfluencerResponse."""
    connection = await _direct_connect(postgres_connection_string)
    try:
        await _insert_test_influencer(
            connection,
            identifier="single-fetch-active",
            display_name="Tara",
            bio="An AI influencer.",
            archetype="companion",
            is_nsfw=False,
            follower_count=42,
            is_active="active",
        )
    finally:
        await connection.close()

    response = await test_client.get(
        "/v1/influencers/single-fetch-active", headers=_INTERNAL_HEADERS
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == "single-fetch-active"
    assert body["display_name"] == "Tara"
    assert body["bio"] == "An AI influencer."
    assert body["archetype"] == "companion"
    assert body["is_nsfw"] is False
    assert body["follower_count"] == 42
    assert body["is_active"] == "active"


@pytest.mark.asyncio
async def test_get_influencer_by_id_returns_404_when_no_influencer_with_that_id_exists(
    test_client: AsyncClient,
) -> None:
    """Missing id → 404 with the documented `not_found` error_code."""
    response = await test_client.get(
        "/v1/influencers/no-such-id", headers=_INTERNAL_HEADERS
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_get_influencer_by_id_returns_404_when_the_row_exists_but_is_discontinued_per_catalog_authority(
    test_client: AsyncClient,
    postgres_connection_string: str,
) -> None:
    """Discontinued row → 404 indistinguishable from missing id (privacy)."""
    connection = await _direct_connect(postgres_connection_string)
    try:
        await _insert_test_influencer(
            connection,
            identifier="single-fetch-discontinued",
            is_active="discontinued",
        )
    finally:
        await connection.close()

    response = await test_client.get(
        "/v1/influencers/single-fetch-discontinued",
        headers=_INTERNAL_HEADERS,
    )

    assert response.status_code == 404
    # The error_code is the same as the no-such-id case so an external
    # probe can't distinguish "soft-deleted" from "never existed".
    assert response.json()["detail"]["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_get_influencer_by_id_maps_coming_soon_to_wire_is_active_active(
    test_client: AsyncClient,
    postgres_connection_string: str,
) -> None:
    """coming_soon persistence value → "active" wire value per round-9 policy."""
    connection = await _direct_connect(postgres_connection_string)
    try:
        await _insert_test_influencer(
            connection,
            identifier="single-fetch-coming-soon",
            is_active="coming_soon",
        )
    finally:
        await connection.close()

    response = await test_client.get(
        "/v1/influencers/single-fetch-coming-soon",
        headers=_INTERNAL_HEADERS,
    )

    assert response.status_code == 200, response.text
    assert response.json()["is_active"] == "active"


@pytest.mark.asyncio
async def test_get_influencer_by_id_coerces_null_avatar_url_to_empty_string_on_the_wire(
    test_client: AsyncClient,
    postgres_connection_string: str,
) -> None:
    """NULL avatar_url (allowed in persistence) → "" on the wire (contract non-null)."""
    connection = await _direct_connect(postgres_connection_string)
    try:
        await _insert_test_influencer(
            connection,
            identifier="single-fetch-no-avatar",
            avatar_url=None,
        )
    finally:
        await connection.close()

    response = await test_client.get(
        "/v1/influencers/single-fetch-no-avatar",
        headers=_INTERNAL_HEADERS,
    )

    assert response.status_code == 200, response.text
    assert response.json()["avatar_url"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_header_name",
    ["X-User-Id", "X-Internal-Caller", "X-Request-Id", "X-Trace-Id"],
)
async def test_get_influencer_by_id_returns_422_when_any_required_internal_call_header_is_missing(
    test_client: AsyncClient,
    missing_header_name: str,
) -> None:
    """FastAPI 422 when ANY of the 4 required internal-call headers is absent on the by-id route.

    Parameterised over all 4 headers per the round-1 CONCERN closure
    on PR #157: round-1 covered only 1 of the 4 on this endpoint.
    """
    headers_without_one = {
        key: value
        for key, value in _INTERNAL_HEADERS.items()
        if key != missing_header_name
    }

    response = await test_client.get(
        "/v1/influencers/any-id", headers=headers_without_one
    )

    assert response.status_code == 422


# ===========================================================================
# RELATED FILES:
#   ../app/api/influencer_routes.py
#                                  — the routes under test
#   ../app/models/influencer_response.py
#                                  — wire-shape model + from_persistence
#                                    projection these tests assert
#   ../app/repository/influencer_metadata_repository.py
#                                  — catalog-authority filter the
#                                    `excludes_discontinued` tests pin
#   conftest.py                    — provides the `test_client` fixture
# ===========================================================================
