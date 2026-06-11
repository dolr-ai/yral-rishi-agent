"""Coach PR-2 — one JSON extractor, two validators.

PR #337 added fenced-block tolerance to `_try_extract_proposal` but
the inline parser in `coach_opening` STAYED naive (`text.find('{')`).
That's why ~5-10% of openings against bots with real history surfaced
the generic fallback greeting (plan §3 #5; Codex review §4).

This PR factored the common path into `_iter_json_candidates(text)` +
two shape validators: `parse_proposal(text)` and `parse_opening(text)`.
The tests below pin:

  - parse_opening recognizes fenced openings (the residual leak).
  - parse_proposal preserves all behaviors PR #337 introduced.
  - _try_extract_proposal stays as a back-compat shim.
  - coach_opening route uses parse_opening (source-pin).
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# ─── parse_opening — the residual leak PR-2 closes ───────────────────────


def test_parse_opening_recognizes_plain_json():
    """Legacy path (no fence) still works — no regression."""
    from services.coach import parse_opening

    text = '{"greeting": "Hi! Let\'s make Tara funnier.", "suggestions": ["Make her sassier", "Tighten bio", "Add humor"]}'
    parsed = parse_opening(text)
    assert parsed is not None
    greeting, chips = parsed
    assert greeting.startswith("Hi! Let")
    assert chips == ["Make her sassier", "Tighten bio", "Add humor"]


def test_parse_opening_recognizes_fenced_json():
    """The bug PR-2 fixes. Gemini wraps the opener in ```json fences
    ~5-10% of the time; pre-refactor coach_opening fell back to generic
    on those. Now it parses cleanly."""
    from services.coach import parse_opening

    text = (
        "```json\n"
        '{"greeting": "Hi! Let\'s make Tara better.", '
        '"suggestions": ["A", "B", "C"]}\n'
        "```"
    )
    parsed = parse_opening(text)
    assert parsed is not None
    greeting, chips = parsed
    assert "Tara" in greeting
    assert len(chips) == 3


def test_parse_opening_recognizes_bare_triple_backtick_fence():
    """Same fence handling as proposals."""
    from services.coach import parse_opening

    text = (
        "```\n"
        '{"greeting": "Hello creator!", "suggestions": ["X", "Y", "Z"]}\n'
        "```"
    )
    assert parse_opening(text) is not None


def test_parse_opening_picks_real_block_when_example_fence_precedes():
    """Like proposals — last fence wins when an example block leads."""
    from services.coach import parse_opening

    text = (
        "For reference, the shape is:\n"
        "```json\n"
        '{"greeting": "EXAMPLE", "suggestions": ["e1", "e2", "e3"]}\n'
        "```\n"
        "Here's the real one:\n"
        "```json\n"
        '{"greeting": "Real greeting", "suggestions": ["r1", "r2", "r3"]}\n'
        "```"
    )
    parsed = parse_opening(text)
    assert parsed is not None
    greeting, _ = parsed
    assert greeting == "Real greeting"


def test_parse_opening_rejects_missing_greeting():
    """Validator must enforce the shape contract."""
    from services.coach import parse_opening

    assert parse_opening('{"suggestions": ["a", "b", "c"]}') is None
    assert parse_opening('{"greeting": "", "suggestions": ["a", "b", "c"]}') is None
    assert (
        parse_opening('{"greeting": "Hello", "suggestions": ["a", "b"]}') is None
    )  # too few chips


def test_parse_opening_returns_none_on_plain_text():
    """Plain text → caller uses its fallback greeting."""
    from services.coach import parse_opening

    assert parse_opening("Hi! Just chat with me.") is None
    assert parse_opening("") is None
    assert parse_opening(None) is None  # type: ignore[arg-type]


# ─── parse_proposal — keep PR #337's tolerance ──────────────────────────


def test_parse_proposal_still_recognizes_fences():
    """Smoke test that the refactor didn't break the fenced-proposal
    behavior PR #337 introduced. Full coverage stays in
    test_coach_parser_fence_robustness.py."""
    from services.coach import parse_proposal

    text = (
        "```json\n"
        '{"summary": "S", "proposed_changes": "C", "reasoning": "R"}\n'
        "```"
    )
    parsed = parse_proposal(text)
    assert parsed is not None
    assert parsed["proposed_changes"] == "C"


def test_parse_proposal_still_recognizes_override_shape():
    from services.coach import parse_proposal

    text = (
        '{"summary": "Allow long replies", '
        '"proposed_global_rule_override": {"key": "response_length", "value": "long_allowed"}, '
        '"reasoning": "R"}'
    )
    parsed = parse_proposal(text)
    assert parsed is not None
    assert parsed["proposed_global_rule_override"]["key"] == "response_length"


# ─── back-compat shim ────────────────────────────────────────────────────


def test_try_extract_proposal_is_back_compat_shim():
    """External test files + sibling modules import this name. Keep
    it as a thin wrapper so the refactor doesn't ripple churn."""
    import services.coach as coach_module

    assert hasattr(coach_module, "_try_extract_proposal")
    # And the behavior matches parse_proposal exactly
    text = '{"summary": "S", "proposed_changes": "C", "reasoning": "R"}'
    assert coach_module._try_extract_proposal(text) == coach_module.parse_proposal(text)


# ─── source-pin: shared extractor exists + coach_opening uses parse_opening ──


def test_shared_extractor_helper_exists():
    """`_iter_json_candidates` is the new single source of truth.
    Both validators (parse_proposal, parse_opening) build on it."""
    src = (REPO / "app" / "services" / "coach.py").read_text()
    assert "def _iter_json_candidates(" in src
    assert "def parse_proposal(" in src
    assert "def parse_opening(" in src


def test_coach_opening_route_uses_parse_opening():
    """The route MUST call parse_opening, not a local naive parser.
    Without this wiring the residual generic-greeting leak stays
    open (the whole point of PR-2)."""
    src = (REPO / "app" / "services" / "coach.py").read_text()
    pos = src.find("async def coach_opening(")
    body = src[pos : pos + 6000]
    assert "parse_opening(text)" in body
    # And the old inline `text.find("{")` parser is GONE
    # (we still have the proposal-side parsers via parse_proposal
    # and _iter_json_candidates, but not in coach_opening's body).
    # Verify by absence: the specific 5-line inline parser shape
    # we replaced should no longer be in coach_opening's body.
    assert "json.loads(text[start:end])" not in body


def test_iter_json_candidates_handles_empty_input_safely():
    from services.coach import _iter_json_candidates

    assert _iter_json_candidates("") == []
    assert _iter_json_candidates(None) == []  # type: ignore[arg-type]


def test_iter_json_candidates_returns_dicts_only():
    """Helper must skip valid-JSON-but-not-dict candidates (a JSON
    array, a number, etc.) — the validators can't operate on those."""
    from services.coach import _iter_json_candidates

    # A JSON array in fenced form
    text = "```json\n[1, 2, 3]\n```"
    # No dict candidates — the array is valid JSON but not what we want.
    result = _iter_json_candidates(text)
    assert all(isinstance(o, dict) for o in result)
