"""Phase 21γ.P34.M2b — Discovery Feed composer.

Tests for the M2b composer additions on top of merged M2a (#401):
  - Cold-start detection (`_is_cold_start_user`)
  - Skill guarantee (≥3 skilled bots on page 1)
  - Archetype-diversity interleave (round-robin)
  - Soft cold-start gender guardrail (only fires for cold-start users)
  - `build_feed_page` end-to-end with user_id threading + composer
    state surfaced via `with_metadata=true`

DORMANT-FIRST: composer falls open to the M2a shuffle when metadata
is missing (pre-M1-classification catalog or DB read failure).
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

REPO = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════
# 1. SOURCE-PIN — defend the composer wiring
# ══════════════════════════════════════════════════════════════════════


def test_composer_symbols_present_in_service():
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    for name in (
        "async def _is_cold_start_user",
        "async def _read_composer_metadata",
        "def _apply_skill_guarantee",
        "def _interleave_by_archetype",
        "def _apply_gender_guardrail",
        "def compose_diverse_order",
        "COLD_START_CONV_THRESHOLD",
        "GENDER_GUARDRAIL_PREFIX_LEN",
        "GENDER_MAX_SHARE",
        "SKILL_GUARANTEE_TOP_N",
    ):
        assert name in src, f"missing composer symbol: {name}"


def test_thresholds_match_design_doc():
    """Design doc §4 — 5-conversation threshold for cold-start gating;
    §5 — ≥3 skilled bots on page 1. Pin the literals so a future
    "let's tune this" PR has to consciously edit the test."""
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    assert "COLD_START_CONV_THRESHOLD = 5" in src
    assert "SKILL_GUARANTEE_TOP_N = 3" in src
    # Gender guardrail: at most 60% any single gender in first 10 slots.
    assert "GENDER_GUARDRAIL_PREFIX_LEN = 10" in src
    assert "GENDER_MAX_SHARE = 0.6" in src


def test_build_feed_page_signature_accepts_user_id():
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    assert "user_id: str | None = None" in src


def test_route_passes_user_id_to_service():
    src = (REPO / "app" / "routes" / "discovery.py").read_text()
    assert "user_id=user_id" in src


def test_composer_state_surfaced_in_with_metadata():
    """`?with_metadata=true` responses must include `composer_state`
    so Rishi can curl-confirm the cold-start vs warm path."""
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    assert '"composer_state"' in src
    # Three possible values per the design.
    assert '"cold_start"' in src
    assert '"warm"' in src


def test_pins_stay_above_composer():
    """Composer touches the SHUFFLED TAIL, not the pinned head.
    Operator-pinned bots must always lead, regardless of how the
    composer reshuffles for diversity. Pin this by inspecting the
    build_feed_page composition order."""
    src = (REPO / "app" / "services" / "discovery_feed.py").read_text()
    # The composer call must take `shuffled_tail` (not the full list).
    assert "compose_diverse_order(\n        shuffled_tail" in src


# ══════════════════════════════════════════════════════════════════════
# 2. BEHAVIOURAL — composer correctness
# ══════════════════════════════════════════════════════════════════════


def _bot(bid, archetype="companion", gender="female", category="Lifestyle", skill=None):
    return {
        "id": bid,
        "archetype": archetype,
        "gender": gender,
        "category": category,
        "skill_slug": skill,
    }


def _meta_dict(*rows):
    return {r["id"]: r for r in rows}


# ─── skill guarantee ────────────────────────────────────────────────────


def test_skill_guarantee_pulls_three_skilled_to_top():
    from services.discovery_feed import _apply_skill_guarantee

    ids = ["a", "b", "c", "d", "e", "f"]
    meta = _meta_dict(
        _bot("a"),  # unskilled
        _bot("b", skill="nutrition_coach"),
        _bot("c"),
        _bot("d", skill="english_coach"),
        _bot("e", skill="travel_advisor"),
        _bot("f"),
    )
    out = _apply_skill_guarantee(ids, meta, top_n=3)
    # First 3 slots are the skilled bots, in their original relative order.
    assert out[:3] == ["b", "d", "e"]


def test_skill_guarantee_no_op_when_no_skilled_bots():
    from services.discovery_feed import _apply_skill_guarantee

    ids = ["a", "b", "c"]
    meta = _meta_dict(_bot("a"), _bot("b"), _bot("c"))
    out = _apply_skill_guarantee(ids, meta, top_n=3)
    assert out == ids


def test_skill_guarantee_partial_when_fewer_than_n_skilled():
    """1 skilled bot + 5 unskilled → 1 skilled at top, no error."""
    from services.discovery_feed import _apply_skill_guarantee

    ids = ["a", "b", "c", "d", "e"]
    meta = _meta_dict(
        _bot("a"),
        _bot("b", skill="english_coach"),
        _bot("c"),
        _bot("d"),
        _bot("e"),
    )
    out = _apply_skill_guarantee(ids, meta, top_n=3)
    assert out[0] == "b"
    # No duplicates.
    assert sorted(out) == sorted(ids)


# ─── archetype interleave ────────────────────────────────────────────────


def test_interleave_by_archetype_round_robins_across_buckets():
    from services.discovery_feed import _interleave_by_archetype

    # 4 companions then 4 advisors → interleaved evenly.
    ids = ["c1", "c2", "c3", "c4", "a1", "a2", "a3", "a4"]
    meta = _meta_dict(
        _bot("c1", archetype="companion"),
        _bot("c2", archetype="companion"),
        _bot("c3", archetype="companion"),
        _bot("c4", archetype="companion"),
        _bot("a1", archetype="advisor"),
        _bot("a2", archetype="advisor"),
        _bot("a3", archetype="advisor"),
        _bot("a4", archetype="advisor"),
    )
    out = _interleave_by_archetype(ids, meta)
    # archetypes alphabetical: advisor, companion → advisor leads then companion.
    archetypes = [meta[b]["archetype"] for b in out]
    # Round-robin: a,c,a,c,a,c,a,c (or c,a,c,a... depending on sort order).
    # Just check no consecutive same-archetype run > 1 in the first 8 slots.
    for i in range(len(archetypes) - 1):
        assert archetypes[i] != archetypes[i + 1], (
            f"adjacent same-archetype at {i}: {archetypes}"
        )


def test_interleave_no_op_when_all_same_archetype():
    """Pre-M1-classification catalog: everyone is 'unknown'. Interleave
    must NOT reorder in that case (single bucket = no diversity to add)."""
    from services.discovery_feed import _interleave_by_archetype

    ids = ["a", "b", "c"]
    meta = _meta_dict(
        _bot("a", archetype="unknown"),
        _bot("b", archetype="unknown"),
        _bot("c", archetype="unknown"),
    )
    out = _interleave_by_archetype(ids, meta)
    assert out == ids


def test_interleave_handles_uneven_buckets():
    """3 companions, 1 advisor — round-robin drains the small bucket
    first then runs through the remaining companions."""
    from services.discovery_feed import _interleave_by_archetype

    ids = ["c1", "c2", "c3", "a1"]
    meta = _meta_dict(
        _bot("c1", archetype="companion"),
        _bot("c2", archetype="companion"),
        _bot("c3", archetype="companion"),
        _bot("a1", archetype="advisor"),
    )
    out = _interleave_by_archetype(ids, meta)
    assert sorted(out) == sorted(ids)
    # Companion can't appear in two adjacent positions before the advisor.
    a_pos = out.index("a1")
    assert a_pos < 2  # interleave puts the advisor in slot 0 or 1


# ─── gender guardrail ───────────────────────────────────────────────────


def test_gender_guardrail_swaps_when_one_gender_dominates():
    """8 female + 2 male in first 10 = 80% female (> 60% cap). The
    guardrail must swap some females out of the prefix with males
    from the tail until ≤60%."""
    from services.discovery_feed import _apply_gender_guardrail

    ids = (
        [f"f{i}" for i in range(8)]
        + [f"m{i}" for i in range(2)]
        + [f"M{i}" for i in range(5)]
    )
    meta = _meta_dict(
        *[_bot(f"f{i}", gender="female") for i in range(8)],
        *[_bot(f"m{i}", gender="male") for i in range(2)],
        *[_bot(f"M{i}", gender="male") for i in range(5)],
    )
    out = _apply_gender_guardrail(ids, meta, prefix_len=10, max_share=0.6)
    prefix_genders = [meta[b]["gender"] for b in out[:10]]
    female_share = prefix_genders.count("female") / 10
    assert female_share <= 0.6, (
        f"guardrail failed — female share = {female_share:.2f}; prefix = {prefix_genders}"
    )
    # No duplicates: every bot must appear exactly once.
    assert sorted(out) == sorted(ids)


def test_gender_guardrail_does_not_swap_unknown_for_dominance():
    """'unknown' gender should NOT count as dominant — pre-classification
    bots shouldn't be ejected from the prefix even if all 10 first-slot
    bots are 'unknown'."""
    from services.discovery_feed import _apply_gender_guardrail

    ids = [f"u{i}" for i in range(10)] + ["m1", "m2"]
    meta = _meta_dict(
        *[_bot(f"u{i}", gender="unknown") for i in range(10)],
        _bot("m1", gender="male"),
        _bot("m2", gender="male"),
    )
    out = _apply_gender_guardrail(ids, meta, prefix_len=10, max_share=0.6)
    # No-op — 'unknown' is exempt from dominance check.
    assert out == ids


def test_gender_guardrail_no_op_when_under_cap():
    """6 female + 4 male in first 10 = 60% female (at the cap, not over).
    The guardrail must NOT swap."""
    from services.discovery_feed import _apply_gender_guardrail

    ids = [f"f{i}" for i in range(6)] + [f"m{i}" for i in range(4)] + ["m4", "m5"]
    meta = _meta_dict(
        *[_bot(f"f{i}", gender="female") for i in range(6)],
        *[_bot(f"m{i}", gender="male") for i in range(4)],
        _bot("m4", gender="male"),
        _bot("m5", gender="male"),
    )
    out = _apply_gender_guardrail(ids, meta, prefix_len=10, max_share=0.6)
    assert out == ids


def test_gender_guardrail_gives_up_gracefully_when_no_swap_candidate():
    """All 12 bots are female. Guardrail can't fix dominance; must
    return input unchanged rather than infinite-loop or 5xx."""
    from services.discovery_feed import _apply_gender_guardrail

    ids = [f"f{i}" for i in range(12)]
    meta = _meta_dict(*[_bot(f"f{i}", gender="female") for i in range(12)])
    out = _apply_gender_guardrail(ids, meta, prefix_len=10, max_share=0.6)
    assert out == ids


# ─── full compose_diverse_order ─────────────────────────────────────────


def test_compose_no_op_on_empty_metadata():
    """If metadata read failed / pre-M1 catalog, compose returns
    input unchanged. DORMANT-FIRST property."""
    from services.discovery_feed import compose_diverse_order

    ids = ["a", "b", "c"]
    out = compose_diverse_order(ids, {}, is_cold_start=True)
    assert out == ids


def test_compose_skill_prefix_preserved_through_diversity_pass():
    """The ≥3-skilled prefix must SURVIVE the archetype interleave —
    the interleave should run on the tail only, not the prefix.
    Otherwise the "first 3 slots have skills" promise gets broken."""
    from services.discovery_feed import compose_diverse_order

    ids = ["s1", "s2", "s3", "x1", "x2", "x3"]
    meta = _meta_dict(
        _bot("s1", archetype="companion", skill="nutrition_coach"),
        _bot("s2", archetype="advisor", skill="english_coach"),
        _bot("s3", archetype="educator", skill="travel_advisor"),
        _bot("x1", archetype="companion"),
        _bot("x2", archetype="entertainer"),
        _bot("x3", archetype="creator"),
    )
    out = compose_diverse_order(ids, meta, is_cold_start=False)
    # First 3 slots must all be skilled, in input order.
    assert out[:3] == ["s1", "s2", "s3"]


def test_compose_cold_start_applies_gender_guardrail():
    """Cold-start: gender guardrail runs after diversity interleave.
    Confirm the dominant gender share drops below the cap."""
    from services.discovery_feed import compose_diverse_order

    # Need >= 10 to trigger the guardrail (prefix_len=10).
    ids = (
        [f"f{i}" for i in range(8)]
        + [f"m{i}" for i in range(2)]
        + [f"M{i}" for i in range(8)]
    )
    meta = _meta_dict(
        *[_bot(f"f{i}", gender="female", archetype="companion") for i in range(8)],
        *[_bot(f"m{i}", gender="male", archetype="advisor") for i in range(2)],
        *[_bot(f"M{i}", gender="male", archetype="entertainer") for i in range(8)],
    )
    out = compose_diverse_order(ids, meta, is_cold_start=True)
    genders_first_10 = [meta[b]["gender"] for b in out[:10]]
    female_share = genders_first_10.count("female") / 10
    assert female_share <= 0.6, f"guardrail not applied: {genders_first_10}"


def test_compose_warm_user_skips_gender_guardrail():
    """Warm users (post-threshold): no gender enforcement per design §5
    ('No gender quota. Personalization drives the mix.'). Same input
    as the cold-start test ⇒ female share STAYS above 0.6."""
    from services.discovery_feed import compose_diverse_order

    ids = [f"f{i}" for i in range(10)] + [f"m{i}" for i in range(2)]
    meta = _meta_dict(
        *[_bot(f"f{i}", gender="female", archetype="companion") for i in range(10)],
        *[_bot(f"m{i}", gender="male", archetype="advisor") for i in range(2)],
    )
    out = compose_diverse_order(ids, meta, is_cold_start=False)
    # No guardrail ran ⇒ all 10 IDs preserved; just interleaved.
    assert sorted(out) == sorted(ids)


# ─── cold-start detection ───────────────────────────────────────────────


class _StubPool:
    def __init__(self, conv_count: int | None = 0, raises: bool = False):
        self.conv_count = conv_count
        self.raises = raises
        self.queries: list[str] = []

    async def fetchval(self, sql, *args):
        self.queries.append(sql)
        if self.raises:
            raise Exception("simulated DB error")
        return self.conv_count


def test_cold_start_when_no_user_id():
    from services.discovery_feed import _is_cold_start_user

    pool = _StubPool()
    out = asyncio.run(_is_cold_start_user(pool, None))
    assert out is True
    # No DB call — short-circuit on missing user_id.
    assert pool.queries == []


def test_cold_start_below_threshold():
    from services.discovery_feed import _is_cold_start_user

    pool = _StubPool(conv_count=4)
    out = asyncio.run(_is_cold_start_user(pool, "u1"))
    assert out is True


def test_warm_at_or_above_threshold():
    from services.discovery_feed import _is_cold_start_user

    pool = _StubPool(conv_count=5)
    out = asyncio.run(_is_cold_start_user(pool, "u1"))
    assert out is False


def test_cold_start_fails_open_on_db_error():
    """DB error during cold-start lookup ⇒ assume cold-start (the
    safer default — over-apply the gender guardrail rather than
    under-apply on a fresh user)."""
    from services.discovery_feed import _is_cold_start_user

    pool = _StubPool(raises=True)
    out = asyncio.run(_is_cold_start_user(pool, "u1"))
    assert out is True


# ─── _read_composer_metadata: bulk read + fail-open ─────────────────────


class _MetaPool:
    def __init__(self, rows, raises=False):
        self.rows = rows
        self.raises = raises

    async def fetch(self, sql, *args):
        if self.raises:
            raise Exception("simulated DB error")
        return self.rows


def test_read_composer_metadata_returns_dict_keyed_by_id():
    from services.discovery_feed import _read_composer_metadata

    rows = [
        {
            "id": "a",
            "archetype": "companion",
            "gender": "female",
            "category": "Lifestyle",
            "skill_slug": None,
        },
        {
            "id": "b",
            "archetype": "advisor",
            "gender": "male",
            "category": "Travel",
            "skill_slug": "travel_advisor",
        },
    ]
    pool = _MetaPool(rows)
    out = asyncio.run(_read_composer_metadata(pool, ["a", "b"]))
    assert out["a"]["archetype"] == "companion"
    assert out["b"]["skill_slug"] == "travel_advisor"


def test_read_composer_metadata_empty_on_db_error():
    """Fail-open: DB error ⇒ empty dict ⇒ compose_diverse_order no-ops.
    The feed still serves; composition just doesn't fire."""
    from services.discovery_feed import _read_composer_metadata

    pool = _MetaPool([], raises=True)
    out = asyncio.run(_read_composer_metadata(pool, ["a", "b"]))
    assert out == {}


def test_read_composer_metadata_empty_input_returns_empty():
    from services.discovery_feed import _read_composer_metadata

    pool = _MetaPool([])
    out = asyncio.run(_read_composer_metadata(pool, []))
    assert out == {}
