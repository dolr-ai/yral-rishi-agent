# ---------------------------------------------------------------------------
# test_api_composed_prompt.py — HTTP coverage for `GET /composed-prompt`.
#
# ⭐ START HERE: this file exercises the FastAPI route surface:
#
#   HAPPY PATH
#     test_get_composed_prompt_returns_200_with_documented_shape
#
#   ERROR PATHS
#     test_get_composed_prompt_returns_404_for_unknown_influencer_id
#     test_get_composed_prompt_returns_422_for_invalid_user_segment
#     test_get_composed_prompt_returns_422_when_required_query_param_missing
#
# Per the Day-4 directive: shape must match
# `interface-contracts/01-internal-rpc-contracts.md` verbatim. The
# orchestrator (Day-5+) integrates against this surface; Session 5's
# contract-tests (Day 10+) lock the shape.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import asyncpg
import httpx
import pytest

from app.repository.soul_file_repository import create_new_version


FIXTURE_INFLUENCER_ID: str = "33333333-3333-3333-3333-333333333333"


async def _seed_l3_for_http_test(pool: asyncpg.Pool) -> None:
    """Seed a Layer 3 row so the HTTP happy-path test gets a 200.

    WHAT: same shape as the composer-test fixture L3 row.
    WHEN: called from the happy-path HTTP test.
    WHY:  HTTP tests share the no-Day-4-L3-seed constraint with the
          composer tests; seeding inside the test means the conftest
          truncate-and-reseed doesn't have to know about it.
    """
    await create_new_version(
        layer=3,
        scope_key=FIXTURE_INFLUENCER_ID,
        body=(
            "[v2 phase-1 day-4 Layer 3 body for test fixture influencer "
            f"{FIXTURE_INFLUENCER_ID} — archetype=companion]"
        ),
        archetype="companion",
    )


# ===========================================================================
# HAPPY PATH
# ===========================================================================


@pytest.mark.asyncio
async def test_get_composed_prompt_returns_200_with_documented_shape(
    database_pool: asyncpg.Pool, client: httpx.AsyncClient
) -> None:
    """WHAT: GET with valid args returns 200 + 3-field response shape.
    WHEN: L3 fixture row seeded + both query params valid.
    WHY:  proves the route + composer + repo end-to-end + shape matches
          `interface-contracts/01-internal-rpc-contracts.md` verbatim.
    """
    await _seed_l3_for_http_test(database_pool)

    response = await client.get(
        "/composed-prompt",
        params={"influencer_id": FIXTURE_INFLUENCER_ID, "user_segment": "new"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # Three documented fields, exact keys, exact types.
    assert set(body.keys()) == {"layered_prompt", "version_pin", "cache_hit"}
    assert isinstance(body["layered_prompt"], str) and len(body["layered_prompt"]) > 0
    assert isinstance(body["version_pin"], str) and len(body["version_pin"]) == 16
    assert body["cache_hit"] is False


# ===========================================================================
# ERROR PATHS
# ===========================================================================


@pytest.mark.asyncio
async def test_get_composed_prompt_returns_404_for_unknown_influencer_id(
    database_pool: asyncpg.Pool, client: httpx.AsyncClient
) -> None:
    """WHAT: GET with influencer_id that has no L3 row → 404.
    WHEN: random UUID + valid user_segment.
    WHY:  proves the InfluencerSoulFileMissingError → 404 mapping.
          Day-5+ orchestrator will fall back to a generic LLM response
          on 404 + log to Sentry for follow-up data-port work.
    """
    response = await client.get(
        "/composed-prompt",
        params={
            "influencer_id": "00000000-0000-0000-0000-deadbeef0000",
            "user_segment": "new",
        },
    )

    assert response.status_code == 404, response.text


async def test_get_composed_prompt_returns_422_for_invalid_user_segment(
    client: httpx.AsyncClient,
) -> None:
    """WHAT: GET with user_segment outside {new, paying, dormant} → 422.
    WHEN: caller sends a typo'd or arbitrary segment value.
    WHY:  Pydantic enforces the literal type at the route boundary so
          the composer never sees an invalid segment + can rely on
          the seed coverage assumption.
    """
    response = await client.get(
        "/composed-prompt",
        params={
            "influencer_id": FIXTURE_INFLUENCER_ID,
            "user_segment": "not-a-real-segment",
        },
    )

    assert response.status_code == 422, response.text


async def test_get_composed_prompt_returns_422_when_required_query_param_missing(
    client: httpx.AsyncClient,
) -> None:
    """WHAT: GET without `user_segment` → 422.
    WHEN: caller forgets the query parameter.
    WHY:  proves FastAPI/Pydantic enforces the required-param check
          before the route body runs. Saves the composer from having
          to defend against missing args.
    """
    response = await client.get(
        "/composed-prompt",
        params={"influencer_id": FIXTURE_INFLUENCER_ID},
    )

    assert response.status_code == 422, response.text


# ===========================================================================
# RELATED FILES:
#   conftest.py                       — database_pool + client fixtures
#   ../app/api/composed_prompt_routes.py
#                                    — route module under test
#   ../app/composer/four_layer_composer.py
#                                    — composer the route delegates to
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                                    — cross-service RPC contract this
#                                       test locks in
# ===========================================================================
