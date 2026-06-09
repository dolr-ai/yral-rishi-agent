"""Coach Fix 1 PR-A — per-bot global-rule overrides.

The 2026-06-09 Saikat alpha session surfaced the bug: GLOBAL_RULES
wrapped every chat regardless of per-influencer system_instructions.
When Saikat asked Coach to "send longer responses", Coach edited the
bot's instructions, but the global "1-3 sentences max" rule kept
winning. These tests defend the new override path so Coach's intent
actually lands at the LLM.

Two layers:
- Behavioral: `_render_global_rules` and `compose()` actually drop
  the right rules when an override is set.
- Source-pin: call sites pass `global_rule_overrides=inf.get(...)`
  + the migration exists + the repo SELECTs the column.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# ─── behavioral ──────────────────────────────────────────────────────────


def test_render_default_includes_all_overrideable_rules():
    """With no overrides, every overrideable rule must render. Otherwise
    a future migration that adds an override key would silently disable
    behavior on existing bots."""
    from services.soul_file import (
        GLOBAL_RULES_OVERRIDEABLE,
        _render_global_rules,
    )

    rendered = _render_global_rules(None)
    for rule_text in GLOBAL_RULES_OVERRIDEABLE.values():
        assert rule_text in rendered


def test_render_drops_response_length_when_override_set():
    """Saikat's case: bot opts out of '1-3 sentences max'. The line
    must NOT appear in the rendered output."""
    from services.soul_file import (
        GLOBAL_RULES_OVERRIDEABLE,
        _render_global_rules,
    )

    rendered = _render_global_rules({"response_length": "long_allowed"})
    assert GLOBAL_RULES_OVERRIDEABLE["response_length"] not in rendered
    # Other overrideable rules still present:
    assert GLOBAL_RULES_OVERRIDEABLE["language_mirror"] in rendered


def test_render_drops_language_mirror_when_override_set():
    """Other example from Rishi's spec — bot pinned to a specific
    language regardless of user's language."""
    from services.soul_file import (
        GLOBAL_RULES_OVERRIDEABLE,
        _render_global_rules,
    )

    rendered = _render_global_rules({"language_mirror": "always_english"})
    assert GLOBAL_RULES_OVERRIDEABLE["language_mirror"] not in rendered
    assert GLOBAL_RULES_OVERRIDEABLE["response_length"] in rendered


def test_fixed_rules_never_drop_under_overrides():
    """The three FIXED rules (in-character, no AI mention, warm tone)
    are non-negotiable platform behavior. No override key should remove
    them — even a malformed override blob with their text as the key."""
    from services.soul_file import GLOBAL_RULES_FIXED, _render_global_rules

    # Try to "override" everything — fixed rules must still appear.
    rendered = _render_global_rules(
        {
            "response_length": "x",
            "language_mirror": "x",
            "character_consistency": "x",  # not a real key — should be ignored
            "tone": "x",  # not a real key
        }
    )
    for rule_text in GLOBAL_RULES_FIXED:
        assert rule_text in rendered


def test_render_accepts_json_string_form():
    """asyncpg returns JSONB columns as strings in this codebase
    (see app/routes/influencers.py:59 for the pattern). The renderer
    must handle both — otherwise call sites would each need to
    json.loads() before passing."""
    from services.soul_file import (
        GLOBAL_RULES_OVERRIDEABLE,
        _render_global_rules,
    )

    rendered = _render_global_rules('{"response_length": "long_allowed"}')
    assert GLOBAL_RULES_OVERRIDEABLE["response_length"] not in rendered


def test_render_handles_malformed_json_safely():
    """If the JSONB blob is somehow malformed (manual SQL write, partial
    write), the renderer must fall back to the platform defaults rather
    than crash chat-send. Defensive: garbage in → safe output."""
    from services.soul_file import (
        GLOBAL_RULES_OVERRIDEABLE,
        _render_global_rules,
    )

    rendered = _render_global_rules("not-json")
    # Defaults preserved
    for rule_text in GLOBAL_RULES_OVERRIDEABLE.values():
        assert rule_text in rendered


def test_compose_passes_through_overrides():
    """End-to-end: compose() with an override produces a system prompt
    that doesn't carry the dropped rule. The Saikat reproduction case."""
    from services.soul_file import GLOBAL_RULES_OVERRIDEABLE, compose

    sys_instructions = "You give long, thoughtful, multi-paragraph replies."
    prompt = compose(
        system_instructions=sys_instructions,
        global_rule_overrides={"response_length": "long_allowed"},
    )
    # The per-bot instruction is in the prompt
    assert sys_instructions in prompt
    # The platform default that would have fought it is GONE
    assert GLOBAL_RULES_OVERRIDEABLE["response_length"] not in prompt


def test_legacy_global_rules_constant_unchanged_in_shape():
    """Backward-compat: the GLOBAL_RULES module constant kept its shape
    (text format with preamble + dashed bullets). Anything that imported
    GLOBAL_RULES before PR-A still sees a usable string."""
    from services.soul_file import GLOBAL_RULES

    assert GLOBAL_RULES.startswith("You are an AI personality on the YRAL")
    assert "Mirror the user's language" in GLOBAL_RULES
    assert "1-3 sentences" in GLOBAL_RULES


# ─── source-pin ───────────────────────────────────────────────────────────


def test_migration_033_exists_with_safe_default():
    """ADD COLUMN with constant DEFAULT is metadata-only on pg11+ (no row
    rewrite). The migration must use that pattern, not a backfill."""
    mig = (REPO / "migrations" / "033_ai_influencers_global_rule_overrides.sql").read_text()
    assert "ALTER TABLE ai_influencers" in mig
    assert "ADD COLUMN IF NOT EXISTS global_rule_overrides" in mig
    assert "JSONB" in mig
    assert "DEFAULT '{}'::jsonb" in mig


def test_repo_selects_global_rule_overrides():
    """All ai_influencers SELECTs that feed into compose() must include
    the column or it'll come back as None and the override is silently
    a no-op."""
    src = (REPO / "app" / "repositories" / "influencer_repo.py").read_text()
    # 6 wide SELECTs: get_by_id, get_by_name, get_by_id_or_name,
    # get_with_conversation_count, list_all, list_trending. All updated
    # for symmetry per Rule 1.
    assert src.count("global_rule_overrides") >= 6


def test_chat_route_passes_overrides_to_compose():
    """Both chat-send paths (non-stream + stream) must pass the override
    blob through. Missing either leaves a regression hole."""
    src = (REPO / "app" / "routes" / "chat.py").read_text()
    # Two call sites of compose() — both must mention global_rule_overrides.
    assert src.count("global_rule_overrides=inf.get(") >= 2


def test_proactive_passes_overrides_to_compose():
    """Skill proactive check-ins also go through compose() — they must
    respect the override too (a bot with 'long_allowed' shouldn't get
    1-3 sentence check-ins)."""
    src = (REPO / "app" / "services" / "proactive.py").read_text()
    assert "global_rule_overrides=inf.get(" in src
