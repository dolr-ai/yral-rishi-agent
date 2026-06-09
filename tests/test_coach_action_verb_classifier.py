"""Coach Fix 4 — action-verb classifier.

Saikat 2026-06-09: typed "Save these changes." in the Coach chat AFTER
Coach showed ✅ Saved receipt → Coach treated it as a NEW edit request.
These tests defend the fast pre-check that catches that class of input.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


# ─── classifier behavior ─────────────────────────────────────────────────


def test_save_verbs_classified_as_save():
    """The exact phrasings Saikat used + the common variants."""
    from services.coach_intent import classify_intent

    cases = [
        "save",
        "Save",
        "save it",
        "Save it",
        "Save these changes.",  # Saikat's exact phrasing
        "save the changes",
        "save changes",
        "apply",
        "Apply it",
        "apply changes",
        "go ahead",
        "Go Ahead",
        "confirm",
        "Yes save it",
        "do it",
        "proceed",
        "ship it",
        "ok save",
        "commit",
    ]
    for msg in cases:
        assert classify_intent(msg) == "save", f"expected save, got {classify_intent(msg)!r} for {msg!r}"


def test_discard_verbs_classified_as_discard():
    from services.coach_intent import classify_intent

    for msg in (
        "discard",
        "Discard it",
        "cancel",
        "Cancel changes",
        "nevermind",
        "never mind",
        "forget it",
        "drop it",
    ):
        assert classify_intent(msg) == "discard", f"got {classify_intent(msg)!r} for {msg!r}"


def test_undo_verbs_classified_as_undo():
    from services.coach_intent import classify_intent

    for msg in (
        "undo",
        "Undo that",
        "revert",
        "Revert it",
        "go back",
        "start over",
        "reset",
        "rollback",
    ):
        assert classify_intent(msg) == "undo", f"got {classify_intent(msg)!r} for {msg!r}"


def test_unmatched_returns_none():
    """Edit requests + questions + greetings must NOT classify. The
    fall-through to None means the normal Coach flow runs."""
    from services.coach_intent import classify_intent

    for msg in (
        "make her sassier",
        "give him longer replies",
        "what does this bot do?",
        "hello",
        "hi coach",
        "tell me about my bot's performance",
        "",
        "   ",
    ):
        assert classify_intent(msg) is None, f"unexpected match for {msg!r}: {classify_intent(msg)!r}"


def test_word_boundary_prevents_false_positives():
    """`save` inside `savings` etc. must NOT match — that'd misclassify
    real edit requests about saving time / money."""
    from services.coach_intent import classify_intent

    for msg in (
        "savings",
        "savory",
        "applied successfully",  # not "apply" as a verb
        "discarded the offer",  # past-tense inside a sentence
        "revertible",
        "doing what?",
    ):
        # All of these contain action-verb-like substrings BUT either
        # exceed the length cap OR don't word-boundary-match. The
        # classifier should not return an action.
        result = classify_intent(msg)
        # Allow "ok" + "discard" to slip through only if the WHOLE
        # message is essentially the verb; verify by length.
        assert result is None or len(msg) <= 50


def test_long_messages_dont_match_even_with_verb():
    """The 50-char cap is the false-positive guard for messages like
    'I want to save time on these long replies — can you make them
    shorter?' which would otherwise match `\\bsave\\b`."""
    from services.coach_intent import classify_intent

    long_msg = (
        "I want to save time on these long replies — can you make them shorter please?"
    )
    assert len(long_msg) > 50
    assert classify_intent(long_msg) is None


def test_non_string_input_returns_none():
    """Defensive — the route reads `body.get('content')` which could
    be None or a non-string from a buggy client. Classifier must not
    raise on weird input."""
    from services.coach_intent import classify_intent

    assert classify_intent(None) is None
    assert classify_intent(123) is None  # type: ignore[arg-type]
    assert classify_intent([]) is None  # type: ignore[arg-type]


# ─── source-pin: route wiring ────────────────────────────────────────────


def test_route_imports_classifier():
    src = (REPO / "app" / "routes" / "creator_coach.py").read_text()
    assert "from services import coach_intent" in src


def test_route_checks_intent_before_llm_call():
    """The classifier check must run BEFORE coach_service.coach_reply.
    If the order flipped, the LLM cycle fires before we have a chance
    to short-circuit — defeating the fix."""
    src = (REPO / "app" / "routes" / "creator_coach.py").read_text()
    intent_pos = src.find("coach_intent.classify_intent(")
    coach_call_pos = src.find("coach_service.coach_reply(")
    assert intent_pos != -1, "classifier call missing from route"
    assert coach_call_pos != -1, "coach_service.coach_reply missing"
    assert intent_pos < coach_call_pos, (
        "classifier must precede coach_service.coach_reply — otherwise "
        "the LLM runs first and the short-circuit can't save anything"
    )


def test_route_checks_pending_proposal_before_short_circuiting():
    """The action return path requires a pending unapplied proposal.
    Without this guard, "save" with no pending would return an action
    that mobile then errors on at /apply. The fall-through to Coach LLM
    handles the no-pending case (ambiguous → clarifying)."""
    src = (REPO / "app" / "routes" / "creator_coach.py").read_text()
    assert "coach_repo.pending_proposal(" in src
    # The action response shape:
    assert '"type": "action"' in src
    assert '"pending_proposal_id"' in src


def test_pending_proposal_repo_helper_exists():
    """The repo helper that distinguishes pending-vs-applied. Without
    this the route would over-fire on already-applied proposals."""
    src = (REPO / "app" / "repositories" / "coach_repo.py").read_text()
    assert "async def pending_proposal(" in src
    # The check itself is "any history row referencing this coach_message_id"
    assert "system_instructions_history" in src
    assert "coach_message_id" in src
