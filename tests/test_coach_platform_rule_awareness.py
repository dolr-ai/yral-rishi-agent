"""Coach Fix 1 PR-B — Coach knows about overrideable platform rules
and proposes overrides instead of editing system_instructions when the
creator's request conflicts.

Layers:
- Behavioral on `_try_extract_proposal`: recognizes both proposal shapes,
  validates override-key.
- Source-pin on META_PROMPT: includes the platform-constraints section,
  enumerates overrideable rules, the "ask first" rule.
- Source-pin on route dispatch: apply endpoint writes to
  global_rule_overrides when proposal has the override blob.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# ─── _try_extract_proposal behavior ──────────────────────────────────────


def test_extract_recognises_system_instructions_edit():
    """Legacy proposal shape still parses (no regression)."""
    from services.coach import _try_extract_proposal

    text = (
        'Some preamble.\n'
        '{"summary": "make sassier", "proposed_changes": "You are a sassy bot...", "reasoning": "match alpha pattern"}\n'
        'trailing prose.'
    )
    parsed = _try_extract_proposal(text)
    assert parsed is not None
    assert parsed["proposed_changes"].startswith("You are a sassy bot")
    # Override shape absent
    assert "proposed_global_rule_override" not in parsed or not parsed.get(
        "proposed_global_rule_override"
    )


def test_extract_recognises_override_proposal():
    """The new shape — proposed_global_rule_override blob."""
    from services.coach import _try_extract_proposal

    text = (
        '{"summary": "Allow long replies for Stap Sister", '
        '"proposed_global_rule_override": {"key": "response_length", "value": "long_allowed"}, '
        '"reasoning": "Saikat wants multi-paragraph responses for this bot."}'
    )
    parsed = _try_extract_proposal(text)
    assert parsed is not None
    assert parsed["proposed_global_rule_override"]["key"] == "response_length"
    assert parsed["proposed_global_rule_override"]["value"] == "long_allowed"


def test_extract_rejects_unknown_override_key():
    """Defense against an LLM hallucinating an override slug that
    isn't actually overrideable. `character_consistency` is in the
    FIXED list (non-overrideable) — should NOT be accepted."""
    from services.coach import _try_extract_proposal

    text = (
        '{"summary": "...", '
        '"proposed_global_rule_override": {"key": "character_consistency", "value": "off"}, '
        '"reasoning": "..."}'
    )
    parsed = _try_extract_proposal(text)
    assert parsed is None  # rejected → falls back to plain-text reply


def test_extract_rejects_plain_text_with_no_proposal():
    """When Coach replies with just a question (the override-ask turn),
    no JSON proposal block — extract must return None so persistence
    skips proposed_changes."""
    from services.coach import _try_extract_proposal

    plain = (
        "That's a platform-wide rule — want me to override "
        "'1-3 sentences max' specifically for Stap Sister?"
    )
    assert _try_extract_proposal(plain) is None


# ─── META_PROMPT source-pin ───────────────────────────────────────────────


def test_meta_prompt_includes_platform_constraints_section():
    """The platform-constraints section is what gives Coach the
    awareness PR-B is supposed to add. Without it, Rule 5 is dead text."""
    src = (REPO / "app" / "services" / "coach.py").read_text()
    assert "PLATFORM CONSTRAINTS" in src
    assert "Overrideable platform rules" in src
    assert "Non-overrideable platform rules" in src


def test_meta_prompt_has_ask_first_rule():
    """Rule 5 — Coach must ASK before flipping an override. Without
    this rule, Coach would silently emit override proposals on first
    turn, defeating the user-consent UX Rishi specified."""
    src = (REPO / "app" / "services" / "coach.py").read_text()
    # Rule 5 keyword markers
    assert "PLATFORM RULE OVERRIDE" in src
    assert "FIRST TURN" in src or "first turn" in src
    assert "ONCE THE CREATOR CONFIRMS" in src or "once the creator confirms" in src


def test_meta_prompt_documents_override_proposal_shape():
    """The {summary, proposed_global_rule_override, reasoning} shape
    must be in the prompt so Coach actually emits it correctly. We
    search past the module docstring's first mention of the override
    column to find the actual META_PROMPT shape spec."""
    src = (REPO / "app" / "services" / "coach.py").read_text()
    assert "proposed_global_rule_override" in src
    # The shape spec lives inside META_PROMPT — narrow the search to
    # that prompt block.
    meta_start = src.find("META_PROMPT = ")
    meta_end = src.find('"""', meta_start + len('META_PROMPT = """'))
    meta_block = src[meta_start:meta_end]
    assert "proposed_global_rule_override" in meta_block
    assert '"key"' in meta_block
    assert '"value"' in meta_block


def test_meta_prompt_overrideable_rules_sourced_from_soul_file():
    """Single source of truth — overrideable list comes from
    GLOBAL_RULES_OVERRIDEABLE. Helper function exists to render it."""
    src = (REPO / "app" / "services" / "coach.py").read_text()
    assert "from services.soul_file import GLOBAL_RULES_OVERRIDEABLE" in src
    assert "_format_overrideable_rules" in src
    # And the META_PROMPT consumes the formatted output via .format(overrideable_rules=...)
    assert "{overrideable_rules}" in src
    assert "overrideable_rules=_format_overrideable_rules()" in src


# ─── coach_reply return signature ─────────────────────────────────────────


def test_coach_reply_returns_4_tuple():
    """Bucket 2 PR-2 (2026-06-11): coach_reply grew to a 5-tuple
    (display, proposed_changes, reasoning, proposed_override,
    proposed_section_change). The PR-B's 4-tuple invariant — at least
    one None per non-proposal turn — still holds; just check the
    section slot lives in the return shape."""
    src = (REPO / "app" / "services" / "coach.py").read_text()
    # 5-tuple shape: str + 4×(str|None or dict|None) — pinned by counting
    # the | None tokens within the coach_reply return-type annotation.
    pos = src.find("async def coach_reply(")
    sig_block = src[pos : pos + 2000]
    assert sig_block.count("| None") >= 4


# ─── route persistence ───────────────────────────────────────────────────


def test_route_destructures_override_from_coach_reply():
    """Bucket 2 PR-2 (2026-06-11): unpack is now 5 values
    (display, proposed, reasoning, proposed_override, proposed_section).
    Persistence still routes the override blob through
    coach_repo.add_message's proposed_global_rule_override kwarg."""
    src = (REPO / "app" / "routes" / "creator_coach.py").read_text()
    # Tolerate both multi-line tuple-unpack shapes (formatter-dependent)
    # by checking for the 5 names individually in proximity to coach_reply
    pos = src.find("coach_service.coach_reply(")
    assert pos != -1
    unpack_window = src[max(0, pos - 400) : pos]
    for name in ("display", "proposed", "reasoning", "proposed_override", "proposed_section"):
        assert name in unpack_window, f"unpack missing name: {name}"
    assert "proposed_global_rule_override=proposed_override" in src


def test_repo_add_message_accepts_override_kwarg():
    """The repo layer must accept + persist the new column."""
    src = (REPO / "app" / "repositories" / "coach_repo.py").read_text()
    assert "proposed_global_rule_override: dict | None = None" in src
    # SQL INSERT must include the new column
    pos = src.find("INSERT INTO coach_messages")
    body = src[pos : pos + 600]
    assert "proposed_global_rule_override" in body


def test_repo_latest_proposal_matches_either_column():
    """`latest_proposal` previously only matched proposed_changes IS
    NOT NULL — would miss override proposals and the /apply endpoint
    would 409 'no proposal to apply'."""
    src = (REPO / "app" / "repositories" / "coach_repo.py").read_text()
    pos = src.find("async def latest_proposal(")
    body = src[pos : pos + 1200]
    assert "proposed_changes IS NOT NULL" in body
    assert "proposed_global_rule_override IS NOT NULL" in body


# ─── apply dispatch ──────────────────────────────────────────────────────


def test_apply_writes_to_global_rule_overrides_on_override_proposal():
    """The apply path detects an override proposal and writes to
    ai_influencers.global_rule_overrides (the JSONB column added in
    migration 033) instead of system_instructions."""
    src = (REPO / "app" / "routes" / "creator_coach.py").read_text()
    pos = src.find("async def apply_coach_proposal(")
    # Window 14000 — Bucket 2 PR-2 added the proposed_section_change
    # dispatch branch (~150 lines) at the TOP of the dispatch ladder,
    # pushing the override + legacy branches further down. Previous
    # window 7000 stopped before the override branch's body even began.
    body = src[pos : pos + 18000]
    # The override branch dispatches BEFORE the legacy path
    override_pos = body.find('proposal.get("proposed_global_rule_override")')
    legacy_pos = body.find('proposal["proposed_changes"]')
    assert override_pos != -1
    assert legacy_pos != -1
    assert override_pos < legacy_pos, (
        "override branch must dispatch before legacy path or override "
        "proposals would silently fall through to system_instructions"
    )
    # The UPDATE targets global_rule_overrides with JSONB merge
    assert "global_rule_overrides" in body
    assert "jsonb_build_object" in body


def test_apply_response_carries_applied_type():
    """Mobile needs to know whether an override or a system_instructions
    edit was applied — different UX for each."""
    src = (REPO / "app" / "routes" / "creator_coach.py").read_text()
    pos = src.find("async def apply_coach_proposal(")
    # Window 20000 — Bucket 2 section-snapshot follow-up (2026-06-12)
    # added the section_change JSONB coercion block in _format_message
    # AND inserted `_ensure_section_snapshots` helper above the apply
    # handler, pushing the system_instructions branch line offset
    # further out. Previous bumps: 3500→7000 (PR-B), 7000→9000 (PR-3),
    # 9000→15000 (Bucket 2 PR-2), 15000→20000 (snapshot follow-up).
    body = src[pos : pos + 20000]
    assert '"applied_type": "global_rule_override"' in body
    assert '"applied_type": "system_instructions"' in body


def test_format_message_surfaces_override_blob():
    """The GET /messages response must include proposed_global_rule_override
    so mobile can render the right Save UX for override proposals."""
    src = (REPO / "app" / "routes" / "creator_coach.py").read_text()
    pos = src.find("def _format_message(")
    body = src[pos : pos + 2500]
    assert '"proposed_global_rule_override": override' in body


# ─── migration ────────────────────────────────────────────────────────────


def test_migration_034_exists():
    mig = (REPO / "migrations" / "034_coach_message_proposed_override.sql").read_text()
    assert "ALTER TABLE coach_messages" in mig
    assert "ADD COLUMN IF NOT EXISTS proposed_global_rule_override" in mig
    assert "JSONB" in mig
    # Nullable (no DEFAULT, no NOT NULL) — safe metadata-only ADD COLUMN.
    assert "DEFAULT" not in mig.split("ADD COLUMN IF NOT EXISTS proposed_global_rule_override JSONB")[1].split(";")[0]
