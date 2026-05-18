# ---------------------------------------------------------------------------
# test_composer.py — coverage for `app/composer/four_layer_composer.py`.
#
# ⭐ START HERE: this file exercises the composer's three responsibilities:
#
#   HAPPY PATH (with golden-file diff)
#     test_compose_matches_committed_golden_when_l3_seeded
#
#   ERROR PATHS (the two distinct exception types)
#     test_compose_raises_when_layer_3_missing
#     test_compose_raises_data_integrity_when_layer_4_missing
#
#   ⭐ BYTE-IDENTITY CONTRACT (PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md)
#     test_compose_returns_byte_identical_layered_prompt_across_reps_5x
#
# Per the Day-4 directive verbatim: "BYTE-IDENTITY CI GATE — the README §F2 /
# pre-spawn-contracts gate: call compose(inf, 'new') twice 100ms apart;
# assert returned layered_prompt is byte-identical (== with bytes, not
# just str); assert version_pin identical; Run this test under pytest's
# repeat-N facility to catch intermittent nondeterminism (5 reps minimum)."
# Per A2.1: implemented via `parametrize` over a 5-rep range — same effect
# as a pytest-repeat plugin without adding a dependency.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import asyncio
from pathlib import Path

import asyncpg
import pytest

from app.composer.four_layer_composer import (
    InfluencerSoulFileMissingError,
    SoulFileDataIntegrityError,
    compose,
)
from app.repository.soul_file_repository import (
    LAYER_PER_USER_SEGMENT,
    create_new_version,
    retire_current,
)


# Path to the committed golden output file. Tests assert the composer
# produces exactly this byte sequence for the seeded L1/L2/L4 + the
# test-fixture L3 row inserted below.
GOLDEN_FIXTURE: Path = Path(__file__).parent / "fixtures" / "composer_golden_layer_output.txt"


# Fixed UUID + body for the L3 test-fixture row. Hardcoded so the
# golden file's byte content stays diffable. The UUID is from
# RFC 4122 - it's not a real influencer; using a recognisable
# "33333333-..." string for greppability.
FIXTURE_INFLUENCER_ID: str = "33333333-3333-3333-3333-333333333333"
FIXTURE_L3_BODY: str = (
    "[v2 phase-1 day-4 Layer 3 body for test fixture influencer "
    f"{FIXTURE_INFLUENCER_ID} — archetype=companion]"
)


async def _seed_l3_companion(pool: asyncpg.Pool) -> None:
    """Insert the test-fixture L3 row (influencer = FIXTURE_INFLUENCER_ID).

    WHAT: creates a Layer 3 row with archetype=companion + the fixture body.
    WHEN: called from happy-path + byte-identity tests.
    WHY:  Day-4 ships no L3 seed (data port deferred). The composer
          can't run without one; the fixture row gives every L3-needing
          test a deterministic input.
    """
    await create_new_version(
        layer=3,
        scope_key=FIXTURE_INFLUENCER_ID,
        body=FIXTURE_L3_BODY,
        archetype="companion",
    )


# ===========================================================================
# HAPPY PATH — golden-file diff
# ===========================================================================


@pytest.mark.asyncio
async def test_compose_matches_committed_golden_when_l3_seeded(
    db_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: compose(FIXTURE_INFLUENCER_ID, 'new') equals the golden file.
    WHEN: after seeding the L3 fixture row.
    WHY:  golden-file diff is the diff-friendly review surface — any
          drift in the layer order, separator, or seed-body text
          surfaces as a Codex-reviewable patch.
    """
    await _seed_l3_companion(db_pool)

    response = await compose(influencer_id=FIXTURE_INFLUENCER_ID, user_segment="new")

    expected = GOLDEN_FIXTURE.read_text(encoding="utf-8")
    assert response.layered_prompt == expected, (
        "Composer output drifted from the committed golden file. "
        "If the change is intentional, update "
        f"{GOLDEN_FIXTURE.relative_to(Path(__file__).parent.parent)} "
        "in the same PR + cite the contract-update reason."
    )
    # cache_hit honesty per the file-header rationale — Day-4 always False.
    assert response.cache_hit is False
    # version_pin shape: 16 hex chars. Specific value depends on seed
    # versions; we only assert the shape, not the exact bytes.
    assert len(response.version_pin) == 16
    assert all(c in "0123456789abcdef" for c in response.version_pin)


# ===========================================================================
# ERROR PATHS
# ===========================================================================


@pytest.mark.asyncio
async def test_compose_raises_when_layer_3_missing(
    db_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: missing L3 row → InfluencerSoulFileMissingError.
    WHEN: caller asks about an influencer with no Soul File row.
    WHY:  this exception is what the HTTP layer turns into 404.
    """
    with pytest.raises(InfluencerSoulFileMissingError):
        await compose(
            influencer_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            user_segment="new",
        )


@pytest.mark.asyncio
async def test_compose_raises_data_integrity_when_layer_4_missing(
    db_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: missing L4 row for a known segment → SoulFileDataIntegrityError.
    WHEN: someone retired the segment's L4 row without a replacement.
    WHY:  defensive — per Day-4 directive "if it ever does, returns a
          clear error (don't silently return empty)." The HTTP layer
          turns this into a 500 (our fault, not caller's).
    """
    await _seed_l3_companion(db_pool)

    # Retire the 'new' segment's L4 row, leaving the slot empty.
    retired = await retire_current(LAYER_PER_USER_SEGMENT, "new")
    assert retired, "expected L4 'new' row to be retired"

    with pytest.raises(SoulFileDataIntegrityError):
        await compose(
            influencer_id=FIXTURE_INFLUENCER_ID,
            user_segment="new",
        )


# ===========================================================================
# ⭐ BYTE-IDENTITY GATE (5 reps minimum per directive)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("rep", range(5))
async def test_compose_returns_byte_identical_layered_prompt_across_reps_5x(
    db_pool: asyncpg.Pool, app_pool_bound: None, rep: int
) -> None:
    """WHAT: compose() called twice ~100ms apart yields BYTE-IDENTICAL output.
    WHEN: every rep (5 minimum per directive).
    WHY:  byte-identity is the LOAD-BEARING engineering contract per
          `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md`. Provider-side prompt
          caching keys on the prefix byte-sequence; one drifting byte =
          full cache miss = 3-10× TTFT regression. The 5-rep parametrize
          catches intermittent nondeterminism (e.g. a `dict` iteration
          order leaking into the prompt) without a pytest-repeat plugin.
    """
    await _seed_l3_companion(db_pool)

    first = await compose(influencer_id=FIXTURE_INFLUENCER_ID, user_segment="new")
    await asyncio.sleep(0.1)
    second = await compose(influencer_id=FIXTURE_INFLUENCER_ID, user_segment="new")

    # `==` on str compares bytes since both are UTF-8 strings here.
    # Explicit `.encode("utf-8")` makes the byte-identity check ABSOLUTE
    # regardless of any future surface where the field type changes.
    assert first.layered_prompt.encode("utf-8") == second.layered_prompt.encode("utf-8"), (
        f"layered_prompt drifted between back-to-back compose() calls "
        f"(rep {rep}). Pre-spawn engineering contract violation — investigate "
        f"timestamps / UUIDs / random ordering inside the composer or seed."
    )
    assert first.version_pin == second.version_pin, (
        f"version_pin drifted between back-to-back compose() calls "
        f"(rep {rep}). Same versions → same hash; if this fails, the "
        f"hash inputs aren't stable."
    )
    assert first.cache_hit is False  # Day-4 always False per the model default
    assert second.cache_hit is False


# ===========================================================================
# RELATED FILES:
#   conftest.py                       — db_pool + app_pool_bound fixtures
#   fixtures/composer_golden_layer_output.txt
#                                    — committed expected output the happy
#                                       test diffs against
#   ../app/composer/four_layer_composer.py
#                                    — module under test
#   ../app/repository/soul_file_repository.py
#                                    — used to seed the L3 fixture row +
#                                       retire L4 in the data-integrity test
#   ../PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md
#                                    — the byte-identity engineering
#                                       contract these tests defend
# ===========================================================================
