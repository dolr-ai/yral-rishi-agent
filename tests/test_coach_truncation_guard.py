"""Coach PR-1 — truncation guard.

Two changes under test:
  1. max_tokens bumped 2048 → 4096 (source-pin).
  2. When the LLM emits JSON-shaped output that doesn't parse, the
     route surfaces a clean reprompt instead of dumping the half-string.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# ─── max_tokens bump (source-pin) ────────────────────────────────────────


def test_coach_reply_max_tokens_4096():
    """The reasoning block in proposals frequently exceeded 2048 →
    creator saw answers ending mid-thought (Rishi 2026-06-10). 4096
    leaves headroom; Gemini Flash returns most coach replies under 1500
    so this is a safety belt, not a typical limit."""
    src = (REPO / "app" / "services" / "coach.py").read_text()
    # Pin the literal so a future refactor can't quietly drop it back.
    assert "max_tokens=4096" in src
    # And explicitly that the previous 2048 is gone from coach_reply.
    pos = src.find("async def coach_reply(")
    assert pos != -1
    body = src[pos : pos + 4000]
    assert "max_tokens=4096" in body
    assert "max_tokens=2048" not in body


def test_coach_opening_max_tokens_unchanged():
    """coach_opening's prompt is shorter + emits 1 JSON object with 3
    chips — 1024 is the right ceiling there, no bump needed."""
    src = (REPO / "app" / "services" / "coach.py").read_text()
    pos = src.find("async def coach_opening(")
    assert pos != -1
    body = src[pos : pos + 2000]
    assert "max_tokens=1024" in body


# ─── truncation detector (behavioral) ────────────────────────────────────


def test_detector_flags_unclosed_brace_with_proposal_markers():
    """The classic truncation: model started emitting a proposal,
    Gemini got cut off mid-stream, response ends with `"summary": "...`
    and no closing brace. Must flag this."""
    from services.coach import _looks_like_truncated_proposal

    truncated = '{"summary": "Make Tara sassier", "proposed_changes": "You ar'
    assert _looks_like_truncated_proposal(truncated) is True


def test_detector_flags_unbalanced_braces():
    from services.coach import _looks_like_truncated_proposal

    truncated = '{"summary": "x", "proposed_changes": "{nested unclosed"'
    assert _looks_like_truncated_proposal(truncated) is True


def test_detector_passes_clean_plain_text():
    """A clarifying question with no JSON markers must NOT trigger the
    detector — the route should surface it as normal plain text. The
    detector is the false-positive guard, not a parse-everything check."""
    from services.coach import _looks_like_truncated_proposal

    assert _looks_like_truncated_proposal("What tone are you going for?") is False
    assert _looks_like_truncated_proposal("Got it. Saving now.") is False
    assert _looks_like_truncated_proposal("") is False
    assert _looks_like_truncated_proposal(None) is False


def test_detector_passes_full_valid_proposal_text():
    """A well-formed proposal text shouldn't be flagged — structurally
    balanced + parseable. The detector only runs after
    _try_extract_proposal returned None, but defense-in-depth says it
    must not false-positive on a healthy string.

    Mobile expert report: the prior catch-all `return True` at the
    bottom of the detector turned long plain-English Coach replies
    that quoted JSON-y vocabulary (e.g. "summary"/"reasoning" inside
    prose) into reprompt loops on Rishi's Anastasia session. The
    detector now requires actual structural damage (unbalanced
    braces or odd quote count) — balanced text falls through to
    plain-text surfacing even if markers are present."""
    from services.coach import _looks_like_truncated_proposal

    healthy = (
        '{"summary": "Make sassier", '
        '"proposed_changes": "You are sassy.", '
        '"reasoning": "Matches alpha pattern."}'
    )
    # Balanced braces (1=1), balanced quotes (even count), markers
    # present → False (no damage signal).
    assert _looks_like_truncated_proposal(healthy) is False


def test_detector_passes_plain_text_quoting_marker_vocabulary():
    """The 2026-06-12 false-positive: a long plain-English Coach reply
    that mentions JSON-y vocabulary in quotes (e.g. explaining what a
    "summary" or "reasoning" field would carry) must NOT trigger the
    reprompt path. Braces + quotes balanced because the text isn't
    JSON in the first place — the old catch-all flagged it anyway,
    which kicked Rishi's Anastasia session into a loop."""
    from services.coach import _looks_like_truncated_proposal

    chatty = (
        'Got it — the "summary" line for that change would be '
        '"make Tara sassier" and the "reasoning" would lean on the '
        'alpha tone we discussed. Want me to draft it?'
    )
    assert _looks_like_truncated_proposal(chatty) is False


def test_detector_passes_plain_text_with_quotes_but_no_markers():
    """A reply with regular quoted speech ("Tara said: 'hi'") must not
    be flagged. The proposal-shape markers are the gate."""
    from services.coach import _looks_like_truncated_proposal

    text = 'Tara could say "hello there!" — that\'s the warmer tone.'
    assert _looks_like_truncated_proposal(text) is False


def test_detector_flags_unmatched_quote_with_markers():
    """The other truncation shape: model emits opening quote on the
    value side, gets cut off → odd quote count + markers present."""
    from services.coach import _looks_like_truncated_proposal

    truncated = '{"summary": "Make Tara'  # quote opened, not closed
    assert _looks_like_truncated_proposal(truncated) is True


# ─── reprompt constant ──────────────────────────────────────────────────


def test_reprompt_constant_is_user_friendly():
    """The fallback text shown to the creator must (a) acknowledge
    something went wrong, (b) ask them to try again, (c) not mention
    JSON / tokens / internals. Sanity-check the wording so a future
    refactor doesn't drop the user-friendly framing."""
    from services.coach import TRUNCATED_REPROMPT_TEXT

    lower = TRUNCATED_REPROMPT_TEXT.lower()
    # Acknowledges the cut-off
    assert any(w in lower for w in ("cut off", "got cut", "sorry"))
    # Asks them to try again
    assert any(w in lower for w in ("again", "redo", "tell me"))
    # No internals
    for forbidden in ("json", "token", "max_tokens", "llm", "gemini"):
        assert forbidden not in lower


# ─── source-pin: the route uses the constant ────────────────────────────


def test_coach_reply_uses_reprompt_on_truncation():
    """The fallback path in coach_reply must use TRUNCATED_REPROMPT_TEXT,
    not raw response_text, when the detector flags truncation. Without
    this wiring the bump-tokens + new detector are dead code."""
    src = (REPO / "app" / "services" / "coach.py").read_text()
    pos = src.find("async def coach_reply(")
    body = src[pos : pos + 6000]
    assert "_looks_like_truncated_proposal(response_text)" in body
    assert "TRUNCATED_REPROMPT_TEXT" in body
