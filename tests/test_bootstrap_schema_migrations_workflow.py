"""Source-level pins for the one-shot bootstrap workflow.

This workflow seeds prod's schema_migrations with rows for every .sql
file currently in migrations/, so the runner can tell what's already
been applied (vs. mistakenly thinking everything is pending — the trap
the 2026-06-09 #314 deploy hit).

These tests defend the workflow's safety properties so a future refactor
can't accidentally remove the guards.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "bootstrap-schema-migrations.yml"


def _src() -> str:
    return WF.read_text()


def test_workflow_exists():
    assert WF.exists(), "bootstrap-schema-migrations.yml missing"


def test_workflow_is_manual_only():
    """Must NEVER run on push — only workflow_dispatch."""
    src = _src()
    assert "workflow_dispatch" in src
    assert "on:\n  workflow_dispatch:" in src or "workflow_dispatch:" in src.split("on:")[1].split("env:")[0]
    # Must NOT trigger on push/PR.
    on_block = src.split("on:")[1].split("env:")[0]
    assert "push:" not in on_block
    assert "pull_request" not in on_block


def test_workflow_requires_typed_confirmation():
    """The 'BOOTSTRAP' phrase prevents accidental clicks."""
    src = _src()
    assert "BOOTSTRAP" in src
    assert "i_understand" in src
    assert 'if [ "${{ inputs.i_understand }}" != "BOOTSTRAP" ]' in src


def test_workflow_uses_on_conflict_do_nothing():
    """Idempotency — re-runs are safe."""
    src = _src()
    assert "ON CONFLICT (filename) DO NOTHING" in src


def test_workflow_uses_unix_socket_trust_path():
    """Match the runner's auth path (PR #323) — no -h localhost, no
    PGPASSWORD."""
    src = _src()
    apply_section = src.split("Apply bootstrap on first responding leader")[1]
    assert "-h localhost" not in apply_section
    assert "PGPASSWORD" not in apply_section
    assert "psql -U postgres" in apply_section


def test_workflow_fails_over_across_managers():
    """If the first manager is a replica, INSERT fails with read-only —
    workflow must try the other managers."""
    src = _src()
    assert "SWARM_MANAGER_1" in src
    assert "SWARM_MANAGER_2" in src
    assert "SWARM_MANAGER_3" in src
    assert "for host in" in src


def test_workflow_exits_nonzero_when_all_managers_fail():
    """Otherwise the job is a silent no-op when the cluster is unreachable."""
    src = _src()
    assert "exit 1" in src
    assert 'Bootstrap failed on all 3 swarm managers' in src
