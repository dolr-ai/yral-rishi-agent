"""Coach parser bug fix — Gemini sometimes wraps JSON in ```json fences,
which broke the parser intermittently → mobile saw "incomplete messages"
in the Coach UI. Mobile expert blocked on this 2026-06-09 EOD.

These tests defend the fence-aware extractor in
services.coach._try_extract_proposal.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# ─── the actual fence-wrapped shapes Gemini emits ─────────────────────────


def test_extract_plain_json_no_fence_still_works():
    """Legacy path (no fence) must keep parsing — Gemini emits this
    most of the time, no regression allowed."""
    from services.coach import _try_extract_proposal

    text = '{"summary": "S", "proposed_changes": "C", "reasoning": "R"}'
    parsed = _try_extract_proposal(text)
    assert parsed is not None
    assert parsed["proposed_changes"] == "C"


def test_extract_handles_json_fence():
    """Gemini ~5-10% of the time wraps in ```json ... ```. Before this
    fix, the find/rfind approach often picked up garbage from
    surrounding prose. Now we extract the fenced content directly."""
    from services.coach import _try_extract_proposal

    text = (
        "Here's the proposal:\n"
        "```json\n"
        '{"summary": "S", "proposed_changes": "C", "reasoning": "R"}\n'
        "```\n"
        "Hope that works!"
    )
    parsed = _try_extract_proposal(text)
    assert parsed is not None
    assert parsed["proposed_changes"] == "C"


def test_extract_handles_bare_triple_backtick_fence():
    """Sometimes Gemini omits the `json` language hint and uses ``` alone."""
    from services.coach import _try_extract_proposal

    text = (
        "```\n"
        '{"summary": "S", "proposed_changes": "C", "reasoning": "R"}\n'
        "```"
    )
    parsed = _try_extract_proposal(text)
    assert parsed is not None
    assert parsed["proposed_changes"] == "C"


def test_extract_picks_last_fence_when_multiple():
    """A long Coach reply might include an EXAMPLE fenced block (e.g.
    "here's what the shape looks like: ```{...}``` ") followed by the
    REAL proposal in another fence. We try fences last-first so the
    actual proposal wins."""
    from services.coach import _try_extract_proposal

    text = (
        "For reference, the shape is:\n"
        "```json\n"
        '{"summary": "EXAMPLE", "proposed_changes": "EXAMPLE_TEXT"}\n'
        "```\n"
        "Here's my actual proposal:\n"
        "```json\n"
        '{"summary": "REAL", "proposed_changes": "REAL_TEXT", "reasoning": "R"}\n'
        "```"
    )
    parsed = _try_extract_proposal(text)
    assert parsed is not None
    assert parsed["proposed_changes"] == "REAL_TEXT"


def test_extract_handles_fence_with_override_shape():
    """The fence wrapping must work for the PR-B override shape too,
    not just system_instructions edits."""
    from services.coach import _try_extract_proposal

    text = (
        "```json\n"
        '{"summary": "Allow long replies for this bot", '
        '"proposed_global_rule_override": {"key": "response_length", "value": "long_allowed"}, '
        '"reasoning": "R"}\n'
        "```"
    )
    parsed = _try_extract_proposal(text)
    assert parsed is not None
    assert parsed["proposed_global_rule_override"]["key"] == "response_length"


def test_extract_falls_back_to_findrfind_on_no_fence():
    """Even with prose around the JSON (no fence), the find-rfind
    fallback still works."""
    from services.coach import _try_extract_proposal

    text = (
        "Sure, here's what I'd change: "
        '{"summary": "S", "proposed_changes": "C", "reasoning": "R"} '
        "Let me know!"
    )
    parsed = _try_extract_proposal(text)
    assert parsed is not None
    assert parsed["summary"] == "S"


def test_extract_returns_none_on_plain_text_reply():
    """Coach asking a clarifying question — no JSON anywhere — must
    return None so the route persists plain text (no proposed_changes)."""
    from services.coach import _try_extract_proposal

    assert _try_extract_proposal("What's the tone you're going for?") is None
    assert _try_extract_proposal("That's a platform rule — want me to override it?") is None


def test_extract_returns_none_on_empty_fence():
    """LLM emits an empty fence — graceful None, no crash."""
    from services.coach import _try_extract_proposal

    assert _try_extract_proposal("```json\n\n```") is None
    assert _try_extract_proposal("```\n```") is None


def test_extract_returns_none_on_malformed_json_inside_fence():
    """Fence is there but content isn't valid JSON — must not crash,
    must fall through to fallback paths and ultimately None."""
    from services.coach import _try_extract_proposal

    text = "```json\n{this is not valid json\n```"
    assert _try_extract_proposal(text) is None


def test_extract_handles_nested_braces_in_value():
    """proposed_changes text containing braces (e.g. template placeholders
    like {user_name}). The full JSON parse must succeed; find-rfind
    fallback could grab garbage but the fence path should be exact."""
    from services.coach import _try_extract_proposal

    text = (
        "```json\n"
        '{"summary": "Add template var", "proposed_changes": "Hi {user_name}, welcome!", "reasoning": "R"}\n'
        "```"
    )
    parsed = _try_extract_proposal(text)
    assert parsed is not None
    assert "{user_name}" in parsed["proposed_changes"]


def test_extract_handles_empty_input():
    """Defensive: None / empty string from a buggy LLM call must not raise."""
    from services.coach import _try_extract_proposal

    assert _try_extract_proposal("") is None
    assert _try_extract_proposal(None) is None  # type: ignore[arg-type]
