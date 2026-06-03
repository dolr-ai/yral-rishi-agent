"""Engagement loop — `inactive` initialization fix.

Pre-fix bug surfaced 2026-06-03 during Tranche A of the internal_vllm
rollout: ENABLE_NUDGE_LOOP=true + ENABLE_PROACTIVE_LOOP=false skipped
the proactive branch where `inactive` was assigned, then crashed on
the summary log line that referenced `len(inactive)`. Loop crashed on
every iteration → zero engagement loop activity.

The kill-switch combination was never used pre-this-rollout, so the
bug lay dormant since the kill-switch landed (Phase 19.3, 2026-05-30).
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_engagement_loop_initializes_inactive_before_proactive_gate():
    """`inactive` must be assigned BEFORE the `if _ks("proactive"):` gate
    so the summary log at end-of-iteration has a defined value even when
    the proactive branch is skipped."""
    src = _read("app/main.py")
    fn_start = src.find("async def _engagement_loop(")
    assert fn_start > 0, "_engagement_loop not found"
    # Body up to the proactive gate
    gate_pos = src.find('if _ks("proactive"):', fn_start)
    init_to_gate = src[fn_start:gate_pos]
    # Must contain an initialization of inactive before the gate
    assert (
        "inactive: list[dict] = []" in init_to_gate
        or "inactive = []" in init_to_gate
        or "inactive: list = []" in init_to_gate
    ), "inactive must be initialized before the proactive gate"


def test_engagement_loop_summary_log_references_inactive():
    """The summary log line that broke pre-fix. If a future refactor
    moves the log inside the proactive branch (also a valid fix), this
    test would need updating — but pin the current shape so the bug
    doesn't regress silently."""
    src = _read("app/main.py")
    fn_start = src.find("async def _engagement_loop(")
    fn_body = src[fn_start : fn_start + 5000]
    assert "len(inactive)" in fn_body
    assert "Engagement loop:" in fn_body
