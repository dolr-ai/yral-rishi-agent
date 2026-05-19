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
    database_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: compose(FIXTURE_INFLUENCER_ID, 'new') equals the golden file.
    WHEN: after seeding the L3 fixture row.
    WHY:  golden-file diff is the diff-friendly review surface — any
          drift in the layer order, separator, or seed-body text
          surfaces as a Codex-reviewable patch.
    """
    await _seed_l3_companion(database_pool)

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
    database_pool: asyncpg.Pool, app_pool_bound: None
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
    database_pool: asyncpg.Pool, app_pool_bound: None
) -> None:
    """WHAT: missing L4 row for a known segment → SoulFileDataIntegrityError.
    WHEN: someone retired the segment's L4 row without a replacement.
    WHY:  defensive — per Day-4 directive "if it ever does, returns a
          clear error (don't silently return empty)." The HTTP layer
          turns this into a 500 (our fault, not caller's).
    """
    await _seed_l3_companion(database_pool)

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
    database_pool: asyncpg.Pool, app_pool_bound: None, rep: int
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
    await _seed_l3_companion(database_pool)

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
# ⭐ E1 PARALLEL-FETCH GATE (Codex PR-#104 round-4)
# ===========================================================================


@pytest.mark.asyncio
async def test_compose_fetches_l1_l2_l4_in_parallel_after_l3(
    database_pool: asyncpg.Pool, app_pool_bound: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WHAT: L1, L2, L4 reads run CONCURRENTLY (asyncio.gather) once L3
          returns its archetype. The spy increments a counter on entry +
          waits on an Event; the test asserts all three are in-flight
          simultaneously BEFORE any complete.
    WHEN: every chat turn — the composer's hot path.
    WHY:  Codex PR-#104 round-4 — the round-3 code issued three
          SEQUENTIAL `await get_current(...)` calls after L3, costing
          3× the per-call round-trip. Once Day-5 real LLM enablement
          lands and the composer is on the chat hot path, that
          serialisation would eat into the E1 latency budget. The
          parallel fetch is the minimal fix; full Redis caching of
          composed prompts stays deferred to Day-5+ per the Day-4
          directive.

          Regression-gate shape (per directive: "asyncio task-counting
          or gather-call assertion rather than timing-based — flaky"):
          monkeypatch the composer's local `get_current` reference to
          a spy that blocks L1/L2/L4 callers on an asyncio.Event +
          tracks how many are in-flight at any moment. If the
          composer code path is parallel, all three callers reach the
          spy + `started_count == 3` before any of them resolves. If
          serial, only one would be in flight at a time.
    """
    await _seed_l3_companion(database_pool)

    # Track concurrent in-flight L1/L2/L4 fetches.
    started_layers: list[int] = []
    release_event = asyncio.Event()

    # `from app.repository.soul_file_repository import get_current`
    # binds a LOCAL reference inside the composer module — patching
    # `soul_file_repository.get_current` would NOT intercept the
    # composer's call. Same Python import-shadowing pattern the
    # orchestrator's round-3 concurrent test handled for
    # `mark_complete`.
    import app.composer.four_layer_composer as four_layer_composer_module
    real_get_current = four_layer_composer_module.get_current

    async def parallel_fetch_observing_spy(layer: int, scope_key: str):
        """Real call wrapped with a concurrency observer.

        L3 (layer == 3) must NOT block — the composer needs its
        archetype before launching L1/L2/L4. Only L1/L2/L4 fetches
        wait on the release_event.
        """
        if layer == 3:
            return await real_get_current(layer, scope_key)

        started_layers.append(layer)
        # Wait for the test body to confirm all 3 are queued + then
        # release. This is what catches a serial implementation: a
        # serial composer would only start ONE of L1/L2/L4 before
        # waiting; the event would never receive its "3 in flight"
        # confirmation + the test would time out.
        await release_event.wait()
        return await real_get_current(layer, scope_key)

    monkeypatch.setattr(
        four_layer_composer_module, "get_current", parallel_fetch_observing_spy,
    )

    # Fire the compose call as a task. It'll block inside the L1/L2/L4
    # spy until we release_event.set().
    compose_task = asyncio.create_task(
        compose(influencer_id=FIXTURE_INFLUENCER_ID, user_segment="new"),
    )

    # Give the event loop a chance to schedule all three parallel
    # fetches. With asyncio.gather the composer kicks off ALL three
    # coroutines before any awaits resolve, so `started_layers`
    # should contain {1, 2, 4} within a tiny number of yields.
    for _ in range(50):
        await asyncio.sleep(0.001)
        if len(started_layers) >= 3:
            break

    assert len(started_layers) == 3, (
        "Composer parallel-fetch regression: expected all 3 of L1, L2, "
        "L4 to be in flight concurrently after L3 returns; got "
        f"started_layers={started_layers!r}. The round-3 sequential "
        "`await get_current(...)` chain would only have 1 fetch in "
        "flight at a time."
    )
    assert set(started_layers) == {1, 2, 4}, (
        f"expected L1+L2+L4 (not including L3 or any other layer); "
        f"got {set(started_layers)!r}"
    )

    # Release the event so the gather completes + verify the response.
    release_event.set()
    response = await compose_task

    assert response.layered_prompt
    assert response.cache_hit is False
    assert len(response.version_pin) == 16


# ===========================================================================
# RELATED FILES:
#   conftest.py                       — database_pool + app_pool_bound fixtures
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
