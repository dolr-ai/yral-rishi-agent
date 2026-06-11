"""Coach PR-4 — expose pending_proposal_exists on the message-list +
send-message responses (plan §4 item D).

Mobile uses this bool to gate the Save button: visible/enabled when
there's a pending UNAPPLIED proposal, disabled with a tooltip
otherwise. Removes the "tap Save → mystery LLM round-trip" path the
mobile expert flagged.

Source-pin tests only — the behavior is "thread one boolean from
coach_repo.pending_proposal through three response shapes." No new
logic to behavior-test.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROUTES = REPO / "app" / "routes" / "creator_coach.py"


def _src() -> str:
    return ROUTES.read_text()


# ─── send_coach_message — happy path returns the field ──────────────────


def test_send_message_response_includes_pending_proposal_exists():
    """The non-action-verb path (regular Coach LLM call) must include
    the field in its response dict. Without it mobile can't gate the
    Save button after a coach turn lands."""
    src = _src()
    pos = src.find("async def send_coach_message(")
    body = src[pos : pos + 6000]
    # The response dict that follows touch_session(...) must have the field.
    # Find the response after touch_session
    tspos = body.find("touch_session(pool, coach_conversation_id)")
    assert tspos != -1
    response_block = body[tspos : tspos + 1500]
    assert '"pending_proposal_exists":' in response_block
    # And it's computed from pending_proposal — not hard-coded
    assert "pending_proposal(pool, coach_conversation_id)" in response_block


def test_send_message_action_short_circuit_also_includes_field():
    """The action-verb fast path (save/discard/undo) ALSO returns the
    field so the contract shape is constant across both branches. The
    value is True there because the short-circuit only fires when
    pending exists."""
    src = _src()
    pos = src.find("async def send_coach_message(")
    body = src[pos : pos + 6000]
    intent_pos = body.find('"type": "action"')
    assert intent_pos != -1
    # The action response block should also include the field
    action_block = body[intent_pos : intent_pos + 1000]
    assert '"pending_proposal_exists":' in action_block
    # Inside the action branch, the value is True (it's only reached when
    # pending is not None — guaranteed)
    assert '"pending_proposal_exists": True' in action_block


# ─── list_coach_messages — session-reload returns the field ─────────────


def test_list_messages_response_includes_pending_proposal_exists():
    """Mobile reads this on session-reload (navigated-away-then-back).
    Field must be present so the Save button gets the right state on
    re-render without mobile having to scan every coach_message client-
    side."""
    src = _src()
    pos = src.find("async def list_coach_messages(")
    body = src[pos : pos + 2000]
    assert '"pending_proposal_exists":' in body
    # Wired to pending_proposal
    assert "pending_proposal(pool, coach_conversation_id)" in body


# ─── consistency — the field is a top-level bool everywhere it ships ────


def test_pending_proposal_exists_is_a_bool_in_all_three_responses():
    """Mobile expects a Bool, not None/Optional. Pin the
    `is not None` shape (not `pending` directly — which would be a
    dict or None on the wire)."""
    src = _src()
    # Across all three response shapes
    occurrences = src.count('"pending_proposal_exists":')
    assert occurrences == 3, (
        f"expected exactly 3 occurrences (send-message LLM path, send-message "
        f"action path, list-messages); found {occurrences}"
    )
    # Bool-cast pattern
    assert "pending is not None" in src


def test_pending_proposal_helper_still_exists_in_repo():
    """PR-4 leans on coach_repo.pending_proposal(...) — pin that the
    helper hasn't been removed (Fix 4 added it; would be a regression
    to drop)."""
    repo_src = (
        REPO / "app" / "repositories" / "coach_repo.py"
    ).read_text()
    assert "async def pending_proposal(" in repo_src
