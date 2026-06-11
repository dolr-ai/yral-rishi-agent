"""Coach PR-3 — proposal_id binding + typed lifecycle status.

Closes Codex review §3 (wrong-proposal-applied trust bug). Migration
035 adds `status` + `status_changed_at`. /apply now requires
`proposal_id` in the body; new /discard endpoint is its counterpart.

Test mix:
  - Migration 035 source-pin (columns + CHECK + backfill + index).
  - Repo helpers source-pin (status-aware queries + new helpers).
  - Route source-pin (proposal_id required, 404/409 surfaces).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── Migration 035 ──────────────────────────────────────────────────────


def test_migration_035_exists():
    mig = REPO / "migrations" / "035_coach_messages_status_lifecycle.sql"
    assert mig.exists(), "migration 035 missing"


def test_migration_035_adds_status_with_check_constraint():
    body = _read("migrations/035_coach_messages_status_lifecycle.sql")
    assert "ALTER TABLE coach_messages" in body
    assert "ADD COLUMN IF NOT EXISTS status" in body
    # The 5 allowed values from the design doc + Rishi's confirmation
    for val in ("'pending'", "'applied'", "'discarded'", "'superseded'", "'na'"):
        assert val in body
    assert "DEFAULT 'pending'" in body


def test_migration_035_adds_status_changed_at_nullable():
    body = _read("migrations/035_coach_messages_status_lifecycle.sql")
    assert "ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ" in body
    # NULL allowed (no NOT NULL) — used to detect "still in default state"
    # The line shouldn't say NOT NULL right after status_changed_at
    pos = body.find("status_changed_at")
    assert pos != -1
    snippet = body[pos : pos + 200]
    assert "NOT NULL" not in snippet


def test_migration_035_three_pass_backfill():
    """Pass 1 marks applied via system_instructions_history join;
    Pass 2 marks older pending as superseded; Pass 3 marks
    non-proposal rows as 'na'."""
    body = _read("migrations/035_coach_messages_status_lifecycle.sql")
    # Pass 1 — applied via audit-table join
    assert "system_instructions_history" in body
    assert "SET status = 'applied'" in body
    # Pass 2 — supersede older pending
    assert "SET status = 'superseded'" in body
    assert "ROW_NUMBER()" in body
    # Pass 3 — non-proposal rows → 'na'
    assert "SET status = 'na'" in body


def test_migration_035_creates_partial_index_on_pending():
    """The per-session 'is there a pending proposal' query runs on
    every send-message + list-messages (PR-4). A partial index keeps
    it O(log pending), not O(log session_size)."""
    body = _read("migrations/035_coach_messages_status_lifecycle.sql")
    assert "CREATE INDEX IF NOT EXISTS" in body
    assert "WHERE status = 'pending'" in body


# ─── Repo helpers ───────────────────────────────────────────────────────


def test_repo_pending_proposal_filters_on_status():
    """PR-4's pending_proposal moved from "no audit-table row references
    this proposal" to "WHERE status = 'pending'". Faster + captures
    discarded/superseded states the audit-table-join couldn't see."""
    src = _read("app/repositories/coach_repo.py")
    pos = src.find("async def pending_proposal(")
    assert pos != -1
    body = src[pos : pos + 2500]
    assert "status = 'pending'" in body


def test_repo_get_proposal_by_id_exists():
    """The new helper /apply + /discard call. Scoped to session for
    defense-in-depth against ID forgery from another creator's session."""
    src = _read("app/repositories/coach_repo.py")
    assert "async def get_proposal_by_id(" in src
    pos = src.find("async def get_proposal_by_id(")
    body = src[pos : pos + 2000]
    # Scoped to the session (defense)
    assert "coach_conversation_id = $2::uuid" in body
    # Only real proposal rows (proposed_* IS NOT NULL)
    assert "proposed_changes IS NOT NULL" in body


def test_repo_supersede_and_apply_is_transactional():
    """The lifecycle invariant — after /apply runs, the session has
    exactly 1 applied + 0 pending — depends on these two UPDATEs
    running in one transaction. Test asserts the `transaction()` block
    is wrapped around both."""
    src = _read("app/repositories/coach_repo.py")
    pos = src.find("async def supersede_and_apply(")
    assert pos != -1
    body = src[pos : pos + 2500]
    # Transaction block
    assert "transaction()" in body
    # Both UPDATEs inside it
    assert "SET status = 'superseded'" in body
    assert "SET status = 'applied'" in body
    # status_changed_at stamped on both transitions
    assert body.count("status_changed_at = NOW()") >= 2


def test_repo_mark_discarded_only_acts_on_pending():
    """Idempotent — re-call on an already-discarded id is a no-op
    (the WHERE clause excludes non-pending rows)."""
    src = _read("app/repositories/coach_repo.py")
    pos = src.find("async def mark_discarded(")
    assert pos != -1
    body = src[pos : pos + 1500]
    assert "SET status = 'discarded'" in body
    assert "status = 'pending'" in body  # only acts on pending


# ─── Route /apply ───────────────────────────────────────────────────────


def test_apply_route_requires_proposal_id_in_body():
    """The trust-bug fix: /apply takes proposal_id from body, not
    "whatever is most recent". Source-pin the validation surface."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def apply_coach_proposal(")
    body = src[pos : pos + 3500]
    # Body parameter added
    assert "body: dict" in body
    # Proposal id is read + validated
    assert 'body or {}).get("proposal_id")' in body
    # 422 on missing/empty
    assert "status_code=422" in body
    assert "proposal_id is required" in body


def test_apply_route_returns_404_on_unknown_proposal_id():
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def apply_coach_proposal(")
    body = src[pos : pos + 3500]
    assert "get_proposal_by_id(" in body
    # 404 on miss (id not found or wrong session)
    assert "status_code=404" in body


def test_apply_route_returns_409_on_non_pending_status():
    """The actual trust-bug guard — proposal must be 'pending'."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def apply_coach_proposal(")
    body = src[pos : pos + 3500]
    assert "proposal_not_pending" in body
    assert "status_code=409" in body
    # Current status surfaced in the body so mobile can render the
    # right error message
    assert '"current_status"' in body


def test_apply_route_calls_supersede_and_apply_in_both_branches():
    """All THREE dispatch branches (section_change + override +
    system_instructions) must transition the lifecycle. Without this,
    the row stays 'pending' forever and pending_proposal_exists keeps
    returning true. Window 15000 — Bucket 2 PR-2 added the section
    branch (~150 lines). Count ≥3 now."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def apply_coach_proposal(")
    body = src[pos : pos + 15000]
    assert body.count("supersede_and_apply(") >= 3


# ─── Route /discard ─────────────────────────────────────────────────────


def test_discard_route_exists():
    src = _read("app/routes/creator_coach.py")
    assert '@router.post("/conversations/{coach_conversation_id}/discard")' in src
    assert "async def discard_coach_proposal(" in src


def test_discard_route_idempotent_on_already_discarded():
    """Re-call on a discarded id returns 200 with discarded:false
    (nothing changed) instead of 409. Mobile may double-tap."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def discard_coach_proposal(")
    body = src[pos : pos + 3000]
    # `discarded` is allowed in the 409-bypass list (just `pending`
    # actually does work; `discarded` is a no-op success)
    assert 'current_status not in ("pending", "discarded")' in body


def test_discard_route_returns_409_on_applied_or_superseded():
    """Cannot discard a proposal that has already been applied or
    superseded — different error than "doesn't exist". Pin the surface."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def discard_coach_proposal(")
    body = src[pos : pos + 3000]
    assert "proposal_not_discardable" in body


# ─── _format_message surfaces status ────────────────────────────────────


def test_format_message_surfaces_status_and_changed_at():
    """Mobile uses status to render active/passive/applied/discarded
    card states. status_changed_at lets mobile show "Applied 2 min ago"."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("def _format_message(")
    # Window 3500 — Bucket 2 section-snapshot follow-up (2026-06-12)
    # inserted the section_change JSONB coercion block before the
    # return dict, pushing the status keys further down.
    body = src[pos : pos + 3500]
    assert '"status":' in body
    assert '"status_changed_at":' in body


# ─── add_message defaults status correctly ──────────────────────────────


def test_add_message_inserts_pending_for_proposals():
    """Newly inserted proposal rows must start as 'pending' so the
    lifecycle invariant holds. Source-pin the is_proposal computation
    + the column in the INSERT. Window bumped to 5500 — Bucket 2 PR-2
    wrapped INSERT in a transaction with supersede-on-insert UPDATE
    above + added proposed_section_change kwarg + target_section_id
    denormalisation, so the INSERT now sits further into the function."""
    src = _read("app/repositories/coach_repo.py")
    pos = src.find("async def add_message(")
    body = src[pos : pos + 5500]
    # is_proposal heuristic
    assert "is_proposal" in body
    # The status column is in the INSERT list
    assert "status" in body[body.find("INSERT INTO coach_messages") : body.find("VALUES")]
