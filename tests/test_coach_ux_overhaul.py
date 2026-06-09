"""Coach UX overhaul (2026-06-04) — source-pin tests for the four mechanics.

Mirrors the source-pin pattern used by test_phase_23_*. The actual
behavior is verified via the curl plan in the PR description against
the deployed image; these pins guard against accidental rewiring
(opening-prompt deleted, request_proposal not wired, receipt removed).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── Mechanic 1 — resume ─────────────────────────────────────────────────


def test_repo_has_latest_session_for_bot():
    src = _read("app/repositories/coach_repo.py")
    assert "async def latest_session_for_bot(" in src
    pos = src.find("async def latest_session_for_bot(")
    body = src[pos : pos + 1500]
    # Must filter by BOTH creator + bot, and pick the most recent.
    assert "WHERE creator_user_id = $1" in body
    assert "AND bot_id = $2" in body
    assert "ORDER BY created_at DESC" in body
    assert "LIMIT 1" in body


def test_create_session_default_is_resume():
    """POST /conversations/{bot_id} returns resumed=True for existing
    session unless body has fresh=true."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def create_coach_session(")
    body = src[pos : pos + 4000]
    assert 'fresh = bool(body.get("fresh"))' in body
    assert "latest_session_for_bot(pool, user_id, bot_id)" in body
    assert '"resumed": True,' in body
    assert '"resumed": False,' in body


# ─── Mechanic 2 — coach speaks first ─────────────────────────────────────


def test_service_has_opening_prompt_and_function():
    src = _read("app/services/coach.py")
    assert "OPENING_PROMPT = " in src
    assert "async def coach_opening(" in src
    # The opening must produce greeting + 3 suggestion strings.
    open_pos = src.find("OPENING_PROMPT = ")
    body = src[open_pos : open_pos + 2000]
    assert '"greeting"' in body
    assert '"suggestions"' in body
    assert "tappable" in body or "chip" in body  # spec language pinned


def test_opening_function_falls_back_safely():
    """LLM may fail or return non-conforming JSON — opening must NEVER
    block session creation. Fallback emits a safe default greeting +
    3 chips so mobile always has something to render."""
    src = _read("app/services/coach.py")
    pos = src.find("async def coach_opening(")
    body = src[pos : pos + 4000]
    assert "non-conforming output" in body
    # Fallback returns 3 chips
    assert "Improve" in body and "Tighten" in body


def test_route_persists_opening_with_suggestions():
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def create_coach_session(")
    body = src[pos : pos + 4000]
    assert "coach_service.coach_opening(" in body
    assert "suggestions=suggestions" in body
    # Opening exception must be swallowed — session creation can't
    # depend on coach LLM availability.
    assert "proceeding without opening message" in body


def test_repo_add_message_accepts_suggestions():
    src = _read("app/repositories/coach_repo.py")
    pos = src.find("async def add_message(")
    # Window bumped to 2000 (was 1200) — Coach Fix 1 PR-B added the
    # `proposed_global_rule_override` kwarg + docstring lines, pushing
    # the `_json.dumps(suggestions)` literal past the original window.
    body = src[pos : pos + 2000]
    assert "suggestions: list[str] | None = None" in body
    # suggestions JSONB written via json.dumps so asyncpg accepts
    # the parameter shape.
    assert "_json.dumps(suggestions)" in body


def test_format_message_surfaces_suggestions():
    """Mobile must see suggestions in the GET /messages response so the
    chip UI renders on the opening turn."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("def _format_message(")
    body = src[pos : pos + 1500]
    assert '"suggestions": suggestions' in body


# ─── Mechanic 3 — forced proposal ────────────────────────────────────────


def test_force_proposal_instruction_exists():
    src = _read("app/services/coach.py")
    assert "FORCE_PROPOSAL_INSTRUCTION = " in src
    pos = src.find("FORCE_PROPOSAL_INSTRUCTION = ")
    body = src[pos : pos + 600]
    # Must instruct the LLM to commit (not ask) + reference Save.
    assert "Save" in body
    assert "MUST output" in body or "MUST" in body


def test_coach_reply_accepts_and_uses_force_proposal():
    src = _read("app/services/coach.py")
    pos = src.find("async def coach_reply(")
    body = src[pos : pos + 3000]
    assert "force_proposal: bool = False" in body
    assert "FORCE_PROPOSAL_INSTRUCTION" in body
    assert "if force_proposal:" in body


def test_route_threads_request_proposal_flag():
    src = _read("app/routes/creator_coach.py")
    # Body-bag read of the flag.
    assert 'force_proposal = bool((body or {}).get("request_proposal"))' in src
    # Threaded to the service call.
    assert "force_proposal=force_proposal," in src


# ─── Mechanic 4 — receipt after apply ────────────────────────────────────


def test_apply_writes_receipt_message():
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def apply_coach_proposal(")
    # Window bumped to 7000 (was 3500) — Coach Fix 1 PR-B added the
    # global_rule_override dispatch path (~80 lines) before the legacy
    # system_instructions branch. Receipt message now lives in BOTH
    # branches; the 3500-char window stopped before either.
    body = src[pos : pos + 7000]
    # Receipt content prefix
    assert "✅ Saved" in body
    # Persisted via add_message
    assert "coach_repo.add_message(" in body
    # Returned in the response
    assert '"receipt_message": _format_message(receipt_msg)' in body
