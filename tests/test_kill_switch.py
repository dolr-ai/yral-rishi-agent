"""Emergency Gemini-background kill switch.

Shipped 2026-05-30 in response to a shared-key rate-limit incident.
Tests pin: env-var semantics, the 4 known loops are gated, the gates
appear at the right call sites."""

import os
import sys
from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


# ─── pure-function env logic (kill_switch.py has no deps) ────────────────


ALL_LOOPS = (
    "proactive",
    "nudge",
    "quality_scorer",
    "memory_extraction",
    "memory_consolidation",
    "streak",
    "integrity",
    "email_digest",
    "etl",
)
ALL_ENV_KEYS = (
    "GEMINI_BACKGROUND_LOOPS_ENABLED",
    "ENABLE_PROACTIVE_LOOP",
    "ENABLE_NUDGE_LOOP",
    "ENABLE_QUALITY_SCORER",
    "ENABLE_MEMORY_EXTRACTION",
    "ENABLE_MEMORY_CONSOLIDATION",
    "ENABLE_STREAK_LOOP",
    "ENABLE_INTEGRITY_LOOP",
    "ENABLE_EMAIL_DIGEST",
    "ENABLE_ETL_LOOP",
)


def test_default_enabled_when_no_env_set(monkeypatch):
    """No env vars set = all loops enabled. We don't want a fresh
    deploy to silently disable background loops."""
    for key in ALL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    from kill_switch import is_enabled

    for loop in ALL_LOOPS:
        assert is_enabled(loop), f"{loop} should default-enabled"


def test_master_false_disables_all(monkeypatch):
    """The whole point — one env-var flip kills every background loop."""
    monkeypatch.setenv("GEMINI_BACKGROUND_LOOPS_ENABLED", "false")
    # Per-loop flags unset (default true) — master must still win
    for key in ALL_ENV_KEYS:
        if key == "GEMINI_BACKGROUND_LOOPS_ENABLED":
            continue
        monkeypatch.delenv(key, raising=False)
    from kill_switch import is_enabled

    for loop in ALL_LOOPS:
        assert not is_enabled(loop), f"{loop} should be disabled with master=false"


def test_per_loop_false_disables_just_that_loop(monkeypatch):
    """Surgical disable — kill just nudge, leave proactive on."""
    monkeypatch.setenv("GEMINI_BACKGROUND_LOOPS_ENABLED", "true")
    monkeypatch.setenv("ENABLE_NUDGE_LOOP", "false")
    monkeypatch.delenv("ENABLE_PROACTIVE_LOOP", raising=False)
    from kill_switch import is_enabled

    assert is_enabled("proactive") is True
    assert is_enabled("nudge") is False


def test_truthy_values_treated_correctly(monkeypatch):
    """'true', '1', 'yes' all enable; everything else (including
    empty) disables. Standard env-var truthy idiom — keep it strict
    to avoid 'enabled' / 'on' / etc. silently differing."""
    monkeypatch.setenv("GEMINI_BACKGROUND_LOOPS_ENABLED", "1")
    from kill_switch import _env_true

    assert _env_true("GEMINI_BACKGROUND_LOOPS_ENABLED") is True
    for val in ("true", "TRUE", "True", "yes", "1"):
        monkeypatch.setenv("X", val)
        assert _env_true("X") is True, f"{val!r} should be truthy"
    for val in ("false", "0", "no", "", "off", "disabled"):
        monkeypatch.setenv("X", val)
        assert _env_true("X") is False, f"{val!r} should be falsy"


def test_unknown_loop_name_defaults_open():
    """Forward-compat — a new background caller without a registered
    flag still gets through. Add the flag in a follow-up."""
    from kill_switch import is_enabled

    # No env, unknown loop name — defaults to True (master is True by
    # default)
    assert is_enabled("not_a_registered_loop_name") is True


def test_current_state_lists_all_known_loops():
    """current_state powers the diagnostics + future dashboard tile.
    Must enumerate all 9 known loops + master, with current env values."""
    from kill_switch import current_state

    state = current_state()
    assert "master" in state
    assert state["master"]["env"] == "GEMINI_BACKGROUND_LOOPS_ENABLED"
    for loop in ALL_LOOPS:
        assert loop in state["loops"], f"{loop} missing from current_state"


def test_all_5_new_loops_gated_at_source():
    """Source-inspection: each new background loop has its is_enabled
    gate at the top of the loop function. Without this the env flag
    does nothing in production."""
    pairs = (
        ("app/services/memory_consolidation.py", '"memory_consolidation"'),
        ("app/services/streak_tracker.py", '"streak"'),
        ("app/services/etl_integrity.py", '"integrity"'),
        ("app/services/email_digest.py", '"email_digest"'),
        ("app/services/etl_chat_ai.py", '"etl"'),
    )
    for path, gate_name in pairs:
        src = _read(path)
        assert "from kill_switch import is_enabled" in src, f"missing import in {path}"
        assert f"is_enabled({gate_name})" in src, f"missing gate in {path}"


# ─── source-inspection — gates are at the right call sites ───────────────


def test_engagement_loop_gates_proactive_and_nudge():
    """The main.py _engagement_loop is the only call site for both
    proactive.send_proactive_message + nudge.generate_nudge. Both
    must be gated; if one slips through, the kill switch is a lie."""
    src = _read("app/main.py")
    # Both keys named at the call site
    assert '_ks("proactive")' in src
    assert '_ks("nudge")' in src
    # And the import line introduces the gate function under that alias
    assert "from kill_switch import is_enabled as _ks" in src


def test_quality_scorer_loop_gates_pass():
    """scoring_loop must check kill switch BEFORE the score_all_bots_once
    call that fans out to Gemini per bot."""
    src = _read("app/services/quality_scorer.py")
    assert "from kill_switch import is_enabled" in src
    assert 'is_enabled("quality_scorer")' in src


def test_memory_extract_gates_at_top():
    """extract_and_store fires from chat.send_message via
    asyncio.create_task after the user reply lands. The kill-switch
    check must be at the TOP of the function so a flipped switch
    short-circuits before any LLM call.

    Phase 25.3 migration: memory.py now calls llm_registry.call() for
    memory_extraction (was direct _call_gemini before). The gate-position
    pin tracks the new call site marker."""
    src = _read("app/services/memory.py")
    assert "from kill_switch import is_enabled" in src
    assert 'is_enabled("memory_extraction")' in src
    # Pin position: the kill-switch check must come BEFORE the LLM call.
    # Accept either the new registry-based call or the legacy direct
    # _call_gemini, so the test survives partial migrations + future
    # refactors.
    gate_pos = src.find('is_enabled("memory_extraction")')
    llm_pos = src.find("llm_registry.call(")
    legacy_pos = src.find("_call_gemini(")
    call_pos = llm_pos if llm_pos > 0 else legacy_pos
    assert gate_pos > 0 and call_pos > 0 and gate_pos < call_pos


def test_user_facing_chat_path_NOT_gated():
    """The kill switch is for BACKGROUND loops. The user's POST
    /api/v1/.../messages path must keep working when the switch is
    flipped — Rishi explicitly said 'user-facing chat MUST still work'."""
    src = _read("app/routes/chat.py")
    # The send_message route handler must not have a kill_switch call
    # gating the generate_response call (line numbers + grep makes it
    # easy enough — kill_switch shouldn't import there at all)
    assert "kill_switch" not in src or "is_enabled" not in src
