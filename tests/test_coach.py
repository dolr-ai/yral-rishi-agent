"""Phase 7.5 — Soul File Coach.

Pure-function pins. The DB + Gemini portions are exercised via the live
smoke test in the deploy step.
"""


def test_extract_proposal_finds_clean_json():
    """When the coach returns plain JSON, _try_extract_proposal returns
    the parsed dict with proposed_changes populated."""
    from services.coach import _try_extract_proposal

    text = '{"summary": "make warmer", "proposed_changes": "You are warm. Be kind.", "reasoning": "users want warmth"}'
    out = _try_extract_proposal(text)
    assert out is not None
    assert out["proposed_changes"] == "You are warm. Be kind."
    assert "summary" in out
    assert "reasoning" in out


def test_extract_proposal_tolerates_wrapping_prose():
    """LLMs sometimes wrap JSON in commentary even when told not to.
    The extractor picks the JSON object out anyway."""
    from services.coach import _try_extract_proposal

    text = (
        'Here is my proposal: {"summary": "x", "proposed_changes": "new sys", '
        '"reasoning": "y"} — let me know!'
    )
    out = _try_extract_proposal(text)
    assert out is not None
    assert out["proposed_changes"] == "new sys"


def test_extract_proposal_returns_none_for_clarifying_question():
    """When the coach asks a question instead of proposing changes, return
    None so the route knows to save the message without proposed_changes."""
    from services.coach import _try_extract_proposal

    text = "What specifically do you want to change about how the bot greets users?"
    assert _try_extract_proposal(text) is None


def test_extract_proposal_rejects_empty_proposal():
    """A JSON object missing proposed_changes (or with empty string) is
    NOT a real proposal — the /apply endpoint would have nothing to commit."""
    from services.coach import _try_extract_proposal

    assert _try_extract_proposal('{"summary": "x", "reasoning": "y"}') is None
    assert (
        _try_extract_proposal(
            '{"summary": "x", "proposed_changes": "", "reasoning": "y"}'
        )
        is None
    )


def test_format_conv_excerpt_truncates_safely():
    """Long conversation samples get clipped to 200 chars per message so
    the meta-prompt stays under Gemini's input budget."""
    from services.coach import _format_conv_excerpt

    long_text = "x" * 500
    rows = [{"conversation_id": "c1", "role": "user", "content": long_text}]
    out = _format_conv_excerpt(rows)
    # Original 500 chars + heading + role label — under 400 chars per line
    assert len(out.splitlines()[1]) <= 220
