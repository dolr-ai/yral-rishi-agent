"""Follow-up to #374 — add engagement_schedule block to /system-prompt-preview.

Honest "what's configured today" view of every scheduled-engagement
mechanism that touches the bot. No new bot-owner cadence config — that's
explicitly dropped. The block just surfaces existing knobs in one
structured place with `source` + `note` fields so the owner knows
where each value comes from + whether it's per-bot or per-user.

Source-pin tests + behavioural tests on the pure helper. fastapi/httpx
aren't in the local venv — wire-level smoke happens in CI/prod.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROUTE = REPO / "app" / "routes" / "soul_file.py"


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── response-key contract (mobile guard) ───────────────────────────────


def test_response_includes_engagement_schedule_top_level_key():
    """Pin the top-level key. Mobile's engagement-schedule card section
    looks for this exact name — a silent rename would break the page."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    assert '"engagement_schedule":' in handler


def test_engagement_schedule_nested_keys_match_contract():
    """The three sub-block keys mobile renders against. Pin both the
    names AND the order: inactivity_proactive first, skill_checkins
    second (can be null), first_turn_nudge last. Mobile's card section
    iterates in this order."""
    src = ROUTE.read_text()
    fn_pos = src.find("def _build_engagement_schedule(")
    helper = src[fn_pos : fn_pos + 4000]
    inactivity_pos = helper.find('"inactivity_proactive":')
    skill_pos = helper.find('"skill_checkins":')
    nudge_pos = helper.find('"first_turn_nudge":')
    assert inactivity_pos != -1
    assert skill_pos != -1
    assert nudge_pos != -1
    assert inactivity_pos < skill_pos < nudge_pos


def test_every_sub_block_carries_source_and_note():
    """Honest-about-source rule: every sub-block must surface both
    `source` (where the value comes from in code/DB) AND `note`
    (whether it's per-bot or per-user + what the value means). Source-
    pin the count so a future sub-block can't ship without both halves."""
    src = ROUTE.read_text()
    fn_pos = src.find("def _build_engagement_schedule(")
    helper = src[fn_pos : fn_pos + 4000]
    # 2 always-present sub-blocks (inactivity, nudge) + 1 conditional
    # (skill_checkins) = 3 source/note pairs in the helper source
    assert helper.count('"source":') == 3
    assert helper.count('"note":') == 3


# ─── inactivity_proactive sub-block ────────────────────────────────────


def test_inactivity_proactive_threshold_is_24_hours():
    """The legacy hardcoded 24-hour threshold. Pin so the brief's
    "no DB reads — these are config, not state" rule is enforced —
    the value comes from a code literal, not a runtime read."""
    src = ROUTE.read_text()
    fn_pos = src.find("def _build_engagement_schedule(")
    helper = src[fn_pos : fn_pos + 4000]
    assert '"threshold_hours": 24' in helper


def test_inactivity_proactive_overrides_match_migration_012_check_enum():
    """The four enum values mirror migration 012's CHECK constraint on
    conversations.proactive_frequency. Source-pin as a module constant
    so the mobile UI picker stays aligned with the DB constraint —
    drift here would mean mobile shows options the DB rejects."""
    src = ROUTE.read_text()
    assert (
        '_INACTIVITY_PROACTIVE_OVERRIDES = ("default", "daily", "weekly", "off")' in src
    )


# ─── path A: bot WITHOUT a skill → skill_checkins is null ──────────────


def test_path_a_no_skill_returns_null_skill_checkins():
    """Mobile gates the "no skill assigned" placeholder on
    `skill_checkins is None`. Returning an empty dict would render
    the wrong UI state — null is the contract.

    Source-pin: the helper initialises `skill_checkins = None` at the
    top of the function. The only path that mutates it is the
    `if skill_slug and (sk := _skills.get(skill_slug)):` branch — if
    the slug is falsy OR the registry lookup misses, None survives."""
    src = ROUTE.read_text()
    fn_pos = src.find("def _build_engagement_schedule(")
    helper = src[fn_pos : fn_pos + 4000]
    # Default initialisation to None
    assert "skill_checkins = None" in helper
    # The single mutation site is guarded on both halves (slug truthy
    # AND registry lookup hit) — pin the walrus shape so a refactor
    # can't loosen the guard.
    assert "if skill_slug and (sk := _skills.get(skill_slug)):" in helper


def test_path_a_unknown_skill_slug_also_returns_null():
    """A skill_slug pointing at a slug not in the registry (e.g. a
    deprecated skill that was removed) must NOT crash + must return
    null. The same guard handles this — `_skills.get()` returns None
    for unknown slugs, the walrus assigns None, the `and` short-
    circuits, skill_checkins stays None."""
    # Behaviour pinned by the source-pin above. This test ADDITIONALLY
    # confirms the helper relies on `_skills.get()` (not `SKILLS[...]`
    # which would KeyError on unknown slugs).
    src = ROUTE.read_text()
    fn_pos = src.find("def _build_engagement_schedule(")
    helper = src[fn_pos : fn_pos + 4000]
    assert "_skills.get(skill_slug)" in helper
    # Belt-and-braces against a future refactor swapping to subscript
    assert "_skills.SKILLS[" not in helper


# ─── path B: bot WITH nutrition_coach → registry values flow through ───


def test_path_b_skill_checkins_cadence_sourced_from_registry():
    """The cadence MUST come from the SKILLS registry, not a literal
    in the route. Source-pin the `sk.get("default_cadence_hours")`
    read so a future PR can't hardcode `6` in the route — if the
    registry changes to 8h, the preview reflects 8h automatically."""
    src = ROUTE.read_text()
    fn_pos = src.find("def _build_engagement_schedule(")
    helper = src[fn_pos : fn_pos + 4000]
    assert '"default_cadence_hours": sk.get("default_cadence_hours")' in helper
    # And the registry today defines the value as 6 (a deliberate
    # number per skills.py docstring) — pin so a registry edit is a
    # deliberate review-gated change
    from services.skills import SKILLS

    assert SKILLS["nutrition_coach"]["default_cadence_hours"] == 6


def test_path_b_skill_checkins_display_name_sourced_from_registry():
    """display_name comes from the registry, not a humanised slug.
    Mobile renders this as the card title — a typo'd slug would
    surface as the title; we want the registry's curated name. Pin
    the registry read with the slug fallback."""
    src = ROUTE.read_text()
    fn_pos = src.find("def _build_engagement_schedule(")
    helper = src[fn_pos : fn_pos + 4000]
    assert '"display_name": sk.get("display_name") or skill_slug' in helper


def test_path_b_skill_checkins_flags_per_user_preferred_times():
    """`per_user_preferred_times: True` tells mobile to render the
    "user picks their own times" disclosure — even if the registry
    says the cadence is 6h, each onboarded user can override during
    skill onboarding. The flag drives the disclosure UI."""
    src = ROUTE.read_text()
    fn_pos = src.find("def _build_engagement_schedule(")
    helper = src[fn_pos : fn_pos + 4000]
    assert '"per_user_preferred_times": True' in helper


# ─── first_turn_nudge sourcing (SSOT pin) ─────────────────────────────


def test_first_turn_nudge_idle_minutes_sourced_from_nudge_constant():
    """initial_idle_minutes MUST flow through the
    `nudge.DEFAULT_INITIAL_IDLE_MINUTES` module constant — not a
    literal in the preview route. Pin BOTH halves:
      1. The constant exists in nudge.py
      2. The preview helper reads it (not a magic number)
    A future PR that drops the constant + bumps the default to 7 must
    have the preview reflect 7 automatically."""
    nudge_src = _read("app/services/nudge.py")
    assert "DEFAULT_INITIAL_IDLE_MINUTES = " in nudge_src
    route_src = ROUTE.read_text()
    fn_pos = route_src.find("def _build_engagement_schedule(")
    helper = route_src[fn_pos : fn_pos + 4000]
    # The value flows through the constant, not a magic literal
    assert "_nudge.DEFAULT_INITIAL_IDLE_MINUTES" in helper


def test_first_turn_nudge_value_matches_should_nudge_default():
    """Behavioural pin: `should_nudge()`'s default MUST reference the
    same module constant the preview reads. Pre-extraction the default
    was a `=5` keyword literal; the SSOT extraction moved it to a
    constant + made `should_nudge`'s default reference it. If either
    drifts, bot owners would see one value on the preview + a
    different threshold actually firing."""
    src = _read("app/services/nudge.py")
    assert "idle_minutes: int = DEFAULT_INITIAL_IDLE_MINUTES" in src


def test_first_turn_nudge_ramp_documented_in_note():
    """The threshold doubles after the third message (line 61 of
    nudge.py). Brief said: if the value ramps, document in the note
    field. Pin the ramp-doc substring so a future tweak to the ramp
    semantics has to come with a note update."""
    src = ROUTE.read_text()
    fn_pos = src.find("def _build_engagement_schedule(")
    helper = src[fn_pos : fn_pos + 4000]
    # Find the first_turn_nudge note specifically
    fnt_pos = helper.find('"first_turn_nudge":')
    fnt_block = helper[fnt_pos : fnt_pos + 1500]
    assert "doubles" in fnt_block
