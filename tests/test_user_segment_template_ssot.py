"""Follow-up to #374 — single source of truth for the L4 user-segment template.

Pre-extraction the preview route hardcoded a copy of the template text
that `soul_file.compose()` emits inside its `if user_skill_state:` branch.
Any edit to the chat-time wording would silently drift; bot owners on
the preview page would see stale template text vs what the LLM actually
gets at chat time.

Post-extraction the template lives ONCE in `services/soul_file.py` as
`USER_SEGMENT_PLAN_TEMPLATE` with a `{plan_lines}` format hole. Both
sites render from it:

  * compose() — fills `plan_lines` with real bullet-rendered state items
  * route preview — fills `plan_lines` with a fixed placeholder string

This file pins the SSOT so a future refactor that re-introduces a
duplicate constant fails here.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── source-pin: constant lives in services/soul_file.py ────────────────


def test_template_constant_defined_in_services_soul_file():
    """The template module-level constant `USER_SEGMENT_PLAN_TEMPLATE`
    is the single source of truth — pin it lives in the services layer
    so the route can import it cleanly."""
    src = _read("app/services/soul_file.py")
    assert "USER_SEGMENT_PLAN_TEMPLATE = (" in src
    # Format hole MUST be `{plan_lines}` — both callers depend on this
    # exact key. A rename would silently break compose() at chat time.
    assert "{plan_lines}" in src


def test_template_constant_contains_canonical_preamble_and_footer():
    """Pin the exact wording so a future tweak to the constant is a
    deliberate review-gated change, not an accidental edit. The
    preamble + footer are the parts mobile + the LLM both see — they
    can't drift apart."""
    src = _read("app/services/soul_file.py")
    assert "**Your current plan for this user:**" in src
    assert "Reference these naturally — don't recite the whole plan back." in src


# ─── source-pin: compose() consumes the constant (no inline copy) ───────


def test_compose_formats_from_template_constant():
    """compose() must render the L4 user-state layer via
    USER_SEGMENT_PLAN_TEMPLATE.format(plan_lines=...) — NOT a duplicated
    inline string. Pin the call shape so a refactor that hardcodes the
    text back inline (the pre-extraction state) is caught."""
    src = _read("app/services/soul_file.py")
    fn_start = src.find("def compose(")
    body = src[fn_start : fn_start + 5000]
    assert "USER_SEGMENT_PLAN_TEMPLATE.format(plan_lines=" in body
    # And the inline duplicate is gone — the preamble text only appears
    # ONCE in the file (in the module-level constant), not inside compose()
    assert body.count("Your current plan for this user") == 0


# ─── source-pin: route imports + uses (no duplicate definition) ─────────


def test_route_imports_template_not_redefines_it():
    """The preview route MUST import the canonical constant — not
    re-declare its own copy. The pre-extraction route had its own
    `_USER_SEGMENT_TEMPLATE`; this regression guard is the whole point
    of the PR."""
    src = _read("app/routes/soul_file.py")
    assert "from services.soul_file import USER_SEGMENT_PLAN_TEMPLATE" in src
    # No re-definition under the old or new name in the route file
    assert "USER_SEGMENT_PLAN_TEMPLATE = (" not in src
    assert "_USER_SEGMENT_TEMPLATE = (" not in src


def test_route_renders_template_with_placeholder_plan_lines():
    """The preview surface shows the slot structure with a placeholder
    line — the owner sees `- <user-specific plan keys appear here...>`
    where compose() would put real bullets at chat time."""
    src = _read("app/routes/soul_file.py")
    assert "USER_SEGMENT_PLAN_TEMPLATE.format(" in src
    assert "plan_lines=" in src
    # The placeholder line itself is pinned so the preview text stays
    # readable even after the constant tightens its surrounding text
    assert "<user-specific plan keys appear here at chat time>" in src


# ─── behavioral: regression guard against template edits ───────────────


def test_compose_user_state_output_contains_canonical_preamble_and_footer():
    """Behavioral regression guard. Given a non-empty user_skill_state,
    `compose()`'s output MUST contain the preamble + footer substrings
    byte-for-byte from the template constant. If a future PR edits the
    template wording, this test catches the drift at CI time before
    bot owners on the preview page see stale text vs the LLM."""
    from services import soul_file

    out = soul_file.compose(
        system_instructions="be warm",
        category=None,
        user_skill_state={"setup": {"primary_goal": "lose 5kg"}},
    )
    # Preamble — the "**Your current plan…**" line marks where L4 begins
    assert "**Your current plan for this user:**" in out
    # Footer — the "Reference these naturally…" line marks where L4 ends
    assert "Reference these naturally — don't recite the whole plan back." in out
    # The real plan content (the bullet) MUST sit BETWEEN preamble + footer
    preamble_pos = out.find("**Your current plan for this user:**")
    footer_pos = out.find("Reference these naturally")
    bullet_pos = out.find("- primary_goal: lose 5kg")
    assert preamble_pos < bullet_pos < footer_pos


def test_compose_skips_user_state_layer_when_state_empty():
    """No regression on the existing "skip when empty" path — the
    template constant doesn't suddenly start appearing in flat-bot
    chat prompts. Pin so a future refactor doesn't accidentally
    unconditionally append the L4 block."""
    from services import soul_file

    out = soul_file.compose(
        system_instructions="be warm",
        category=None,
        user_skill_state=None,
    )
    assert "Your current plan for this user" not in out

    out_empty_dict = soul_file.compose(
        system_instructions="be warm",
        category=None,
        user_skill_state={"setup": {}, "runtime": {}},
    )
    assert "Your current plan for this user" not in out_empty_dict
