"""Source-pin tests for the WAL-G restore drill (Phase 21αβ.H6).

Pins the safety properties of both the bash script and the workflow so
a future refactor can't accidentally remove the guards.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "walg_restore_drill.sh"
WF = ROOT / ".github" / "workflows" / "walg-restore-drill.yml"


def _src(p: Path) -> str:
    return p.read_text()


# ─── Script: file shape ─────────────────────────────────────────────


def test_script_exists():
    assert SCRIPT.exists(), "walg_restore_drill.sh missing"


def test_script_is_executable():
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "script needs +x"


def test_script_uses_set_minus_e():
    """Without set -e, a failing wal-g step would still let teardown +
    'PASSED' run. Need fail-fast for honest exit codes."""
    src = _src(SCRIPT)
    assert "set -euo pipefail" in src or "set -e" in src


# ─── Script: safety properties ──────────────────────────────────────


def test_script_never_touches_live_data_dir():
    """The drill MUST NOT write to /home/postgres/pgdata anywhere. That's
    the live Patroni data dir; any write here corrupts production.

    Scan non-comment lines only — the comments LEGITIMATELY mention the
    live data dir to explain what the drill avoids."""
    code_lines = [
        line for line in _src(SCRIPT).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    # All restore writes go to /tmp/walg-drill-<ts>
    assert "/tmp/walg-drill-" in code
    # Live data dirs never appear in actual commands
    assert "/home/postgres/pgdata" not in code, (
        "live data dir appears in a non-comment line — drill could "
        "corrupt production"
    )
    assert "/var/lib/postgresql/data" not in code


def test_script_uses_custom_port_not_5432():
    """5432 is the live Patroni port; sidecar must be on 5433 (or
    configurable). Binding 5432 would conflict with the live cluster."""
    src = _src(SCRIPT)
    assert "DRILL_PORT" in src
    assert "5433" in src
    # Confirm 5432 is not directly used as the drill port
    assert "port = 5432" not in src and "-p 5432" not in src


def test_script_invokes_wal_g_read_only_operations_only():
    """wal-g backup-fetch (read) + wal-fetch (read) + backup-list (read).
    NO backup-push or wal-push — those would write into the bucket."""
    src = _src(SCRIPT)
    assert "wal-g backup-fetch" in src
    assert "wal-g wal-fetch" in src
    assert "wal-g backup-list" in src
    assert "wal-g backup-push" not in src
    assert "wal-g wal-push" not in src


def test_script_runs_sanity_queries_on_three_critical_tables():
    """The point of the drill is "data is actually there." Confirm we
    query the load-bearing tables (V2 schema has no `users` table —
    principal IDs are on conversations/messages directly per
    migrations/001_initial.sql)."""
    src = _src(SCRIPT)
    assert 'FROM ai_influencers' in src
    assert 'FROM conversations' in src
    assert 'FROM messages' in src
    # Latest message timestamp also checked (proves backup is recent)
    assert "MAX(created_at)" in src or "max(created_at)" in src
    # And the (drill #4) `users` regression must not come back
    assert 'SELECT COUNT(*) FROM users' not in src


def test_script_validates_minimum_row_counts():
    """Without minimum thresholds, an empty restored DB would pass.
    Confirm the script compares counts to a min."""
    src = _src(SCRIPT)
    assert "MIN_ROW_COUNT_AI_INFLUENCERS" in src
    assert "MIN_ROW_COUNT_CONVERSATIONS" in src
    assert "MIN_ROW_COUNT_MESSAGES" in src


def test_script_rejects_non_numeric_query_results():
    """Drill #4 (2026-06-11) reported PASSED while one query returned
    'ERROR: relation does not exist' because bash arithmetic compare
    silently treats non-numeric as 0. Confirm we guard against that."""
    src = _src(SCRIPT)
    assert "is_numeric" in src
    assert "non-numeric result" in src or "non-numeric:" in src


def test_script_checks_latest_message_freshness():
    """Backup that's a month old is technically "restorable" but proves
    nothing — we need to confirm WAL replay caught fresh data."""
    src = _src(SCRIPT)
    assert "MAX_LATEST_MESSAGE_AGE_SECONDS" in src


def test_script_has_distinct_exit_codes_for_each_failure_mode():
    """When the workflow surfaces the exit code, an operator needs to be
    able to map it back to a specific failure class. Confirm we use 1-5."""
    src = _src(SCRIPT)
    # 0 = pass, 1 = prereqs, 2 = fetch fail, 3 = sidecar fail, 4 = data fail, 5 = teardown
    for code in ["exit 1", "exit 2", "exit 3", "exit 4", "exit 5"]:
        assert code in src, f"missing distinct exit code: {code}"


def test_script_does_teardown_in_failure_paths_too():
    """Stale sidecar postgres on port 5433 + /tmp/walg-drill-* dir would
    block the next drill run. Confirm we attempt teardown even on data-fail."""
    src = _src(SCRIPT)
    # Teardown phase must run even when FAILED=1
    assert "tearing down before exit" in src or "teardown" in src.lower()
    # Note that exit 4 (sanity fail) still goes through teardown
    teardown_idx = src.find("─── 7. teardown")
    exit_4_idx = src.find("exit 4")
    assert teardown_idx > 0
    assert exit_4_idx > teardown_idx, "exit 4 must come AFTER teardown"


# ─── Workflow: shape + safety ───────────────────────────────────────


def test_workflow_exists():
    assert WF.exists(), "walg-restore-drill.yml missing"


def test_workflow_is_manual_only():
    """Running the drill on every push would burn the patroni container
    with a sidecar postgres every commit. Manual only."""
    src = _src(WF)
    on_block = src.split("on:")[1].split("env:")[0]
    assert "workflow_dispatch" in on_block
    assert "push:" not in on_block
    assert "pull_request" not in on_block
    assert "schedule" not in on_block  # No accidental cron either


def test_workflow_requires_typed_confirmation():
    """Same accidental-click guard as rollback / bootstrap / rotate /
    roll-patroni-image workflows."""
    src = _src(WF)
    assert "RUN WAL-G DRILL" in src
    assert "i_understand" in src
    assert 'if [ "${{ inputs.i_understand }}" != "RUN WAL-G DRILL" ]' in src


def test_workflow_target_host_is_a_choice_input():
    """Operator must pick rishi-4, rishi-5, or rishi-6 from a dropdown
    — typing a wrong IP would scp/run against the wrong host."""
    src = _src(WF)
    assert "type: choice" in src
    assert "rishi-4" in src and "rishi-5" in src and "rishi-6" in src


def test_workflow_uses_ssh_keyscan_not_static_known_hosts():
    """Lesson from PR #331 — modern OpenSSH wants ED25519; static
    KNOWN_HOSTS only had RSA. ssh-keyscan picks them all up dynamically."""
    src = _src(WF)
    assert "ssh-keyscan" in src
    assert "secrets.KNOWN_HOSTS" not in src


def test_workflow_cleans_up_staging_dir_even_on_failure():
    """The drill script lands in /tmp/walg-drill-<run_id>/ on the host.
    A failed drill must still rm -rf that dir or it accumulates forever."""
    src = _src(WF)
    assert "rm -rf $STAGING" in src
    # Tolerate the cleanup ssh failing — `|| true` accepts the failure
    assert "rm -rf $STAGING" in src and "|| true" in src


def test_workflow_distinguishes_pass_from_messy_cleanup():
    """Exit code 5 means "drill itself passed, only teardown was messy"
    — that's a softer signal than a real failure. Workflow should still
    exit 0 in that case so the operator sees green."""
    src = _src(WF)
    assert "DRILL_RC" in src or "rc=" in src
    assert 'DRILL_RC" -eq 5' in src or "DRILL_RC -eq 5" in src or "rc=5" in src
