"""Phase 23.6 — source-pin tests for the skill-driven proactive loop.

Mirror of test_phase_23_routes_onboarding.py: read-and-pin invariants.
The actual end-to-end fire happens on the rishi-5 host; this pass
catches accidental rewiring (the loop branch silently disappearing,
mark_event_fired dropping out, the kill-switch reverting to a
hardcoded True, etc.).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# --- kill_switch.py ------------------------------------------------------


def test_kill_switch_registers_skill_proactive():
    """A new background loop without a kill-switch entry can't be killed
    cleanly — every Gemini-calling loop must have one."""
    src = _read("app/kill_switch.py")
    assert '"skill_proactive":' in src
    assert "ENABLE_SKILL_PROACTIVE_LOOP" in src


# --- proactive.py — new surface ------------------------------------------


def test_proactive_exposes_three_skill_helpers():
    src = _read("app/services/proactive.py")
    assert "async def find_due_skill_events(" in src
    assert "async def generate_skill_checkin(" in src
    assert "async def send_skill_checkin(" in src


def test_find_due_skill_events_thin_wraps_repo():
    """The engagement loop must talk to ONE find_due_* surface;
    find_due_skill_events is a thin pass-through to the repo so we
    don't fork the partial-index query that makes it cheap."""
    src = _read("app/services/proactive.py")
    pos = src.find("async def find_due_skill_events(")
    body = src[pos : pos + 400]
    assert "skill_state_repo.list_due(pool" in body


def test_skill_checkin_uses_soul_file_composer():
    """The check-in prompt must go through compose() so the skill
    layer + user_skill_state plan layer end up in the system prompt.
    Otherwise the bot would speak as a generic check-in template."""
    src = _read("app/services/proactive.py")
    pos = src.find("async def generate_skill_checkin(")
    body = src[pos : pos + 2000]
    assert "soul_file.compose(" in body
    assert 'skill_slug=inf.get("skill_slug")' in body
    assert 'user_skill_state=state_row.get("state")' in body


def test_send_skill_checkin_advances_schedule():
    """If we don't advance next_event_at after delivery, the loop
    fires the same row every tick — a flood. Pin the mark_event_fired
    call so a refactor can't drop it."""
    src = _read("app/services/proactive.py")
    pos = src.find("async def send_skill_checkin(")
    body = src[pos : pos + 4500]
    assert "skill_state_repo.mark_event_fired(" in body


def test_send_skill_checkin_handles_no_conversation():
    """User_skill_state can exist without a conversation row (if it
    were ever pre-seeded). The loop must skip + still advance the
    schedule so we don't hot-loop. Pin both branches."""
    src = _read("app/services/proactive.py")
    pos = src.find("async def send_skill_checkin(")
    body = src[pos : pos + 4500]
    # Skip-when-no-conversation branch + still-advance call
    assert "no conversation yet" in body
    # mark_event_fired must appear at LEAST twice (no-conv path
    # advances + happy path advances). Pin the count.
    assert body.count("skill_state_repo.mark_event_fired(") >= 2


def test_skill_checkin_falls_through_on_unknown_slug():
    """A state row with an orphan skill_slug (catalog removed it)
    must not 500 — log + skip."""
    src = _read("app/services/proactive.py")
    pos = src.find("async def send_skill_checkin(")
    body = src[pos : pos + 4500]
    assert "unknown skill_slug" in body


# --- main.py — engagement loop wiring ------------------------------------


def test_engagement_loop_runs_skill_block():
    """The engagement_loop must call find_due_skill_events + dispatch
    each row to send_skill_checkin. Pin both."""
    src = _read("app/main.py")
    assert "proactive.find_due_skill_events(" in src
    assert "proactive.send_skill_checkin(" in src


def test_engagement_loop_gates_skill_block_independently():
    """Skill block gated by `skill_proactive`, NOT by the legacy
    `proactive` switch — ops must be able to disable one without
    the other."""
    src = _read("app/main.py")
    assert '_ks("skill_proactive")' in src


def test_engagement_loop_initializes_skill_due_before_summary():
    """Mirror of the inactive-list latent-bug fix (PR #259). If the
    summary log references skill_due len, the variable must be
    defined before the gated block — else an exception inside the
    block leaves it undefined."""
    src = _read("app/main.py")
    init_pos = src.find("skill_due: list[dict] = []")
    summary_pos = src.find("skill check-ins")
    assert init_pos != -1 and summary_pos != -1
    assert init_pos < summary_pos


def test_engagement_loop_summary_mentions_skill_check_ins():
    """Operator-visible: the per-tick summary must surface the
    skill count alongside proactive + nudge so ops can see it
    moving."""
    src = _read("app/main.py")
    assert "skill check-ins" in src


# --- proactive.py — imports symmetry -------------------------------------


def test_proactive_imports_skill_state_repo_and_skills_catalog():
    src = _read("app/services/proactive.py")
    assert "skill_state_repo" in src
    assert "skills as skills_catalog" in src
    assert "soul_file" in src
