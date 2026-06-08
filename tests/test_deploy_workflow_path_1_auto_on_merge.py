"""Source-level pins for the Path 1 auto-deploy workflow shape (2026-06-08).

These are workflow YAML structure assertions — not behavior tests (those
require pushing to main + observing a real deploy, which we exercise on
the live merge of THIS PR). They're guardrails so a future contributor
doesn't accidentally regress the auto-deploy + auto-rollback contract
when refactoring the workflow.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(p: str) -> str:
    return (ROOT / p).read_text()


# ─── Auto-deploy on merge to main ────────────────────────────────────


def test_deploy_workflow_triggers_after_ci_completes_on_main():
    """The whole point of Path 1: merges to main auto-trigger the
    deploy AFTER CI's build-and-push has produced the image. Without
    this chain, we hit the race condition discovered 2026-06-08 with
    PR #297 (deploy fired 4 sec after merge, before CI had built the
    image)."""
    src = _read(".github/workflows/deploy.yml")
    assert "workflow_run:" in src
    assert 'workflows: ["CI"]' in src
    assert "types: [completed]" in src
    assert "branches: [main]" in src


def test_deploy_job_gated_on_successful_ci_push_event():
    """A workflow_run trigger fires on ANY CI completion (success or
    failure, push or PR). The job's `if:` guard must filter to only
    successful CI runs from push events on main."""
    src = _read(".github/workflows/deploy.yml")
    assert "github.event.workflow_run.conclusion == 'success'" in src
    assert "github.event.workflow_run.event == 'push'" in src
    assert "github.event.workflow_run.head_branch == 'main'" in src
    # And the manual button must always bypass this guard.
    assert "github.event_name == 'workflow_dispatch'" in src


def test_deploy_workflow_keeps_manual_button_for_emergencies():
    """Auto-deploy is the default but the manual button must remain so
    we can re-deploy specific SHAs or recover from infra glitches."""
    src = _read(".github/workflows/deploy.yml")
    assert "workflow_dispatch:" in src
    # And the input field for SHA must still be there (manual re-deploy
    # of an older commit is the main reason for the button).
    assert "Commit SHA to deploy" in src


def test_resolve_sha_uses_workflow_run_head_sha():
    """When triggered by a workflow_run, the deploy must use the
    triggering CI run's head_sha (= the commit that was merged), NOT
    the current main HEAD (which can drift between trigger fire and
    job start if another merge lands)."""
    src = _read(".github/workflows/deploy.yml")
    assert "github.event.workflow_run.head_sha" in src


# ─── Concurrency lock ───────────────────────────────────────────────


def test_deploy_workflow_has_concurrency_lock():
    """Two merges in quick succession must queue, not race. Matches
    chat-ai's deploy-baremetal.yml pattern."""
    src = _read(".github/workflows/deploy.yml")
    assert "concurrency:" in src
    assert "group:" in src
    # Critical: don't cancel an in-flight deploy. Queue it instead.
    assert "cancel-in-progress: false" in src


# ─── Auto-rollback safety net ───────────────────────────────────────


def test_deploy_workflow_has_health_check_step_with_id():
    """The auto-rollback step keys on the health-check step's outcome.
    If the step has no id, the rollback step can't conditionally
    reference it."""
    src = _read(".github/workflows/deploy.yml")
    assert "name: Wait for /health to return 200" in src
    assert "id: health_check" in src


def test_deploy_workflow_auto_rollback_step_exists():
    """The whole point of Path 1's safety upgrade — if /health fails,
    the rollback workflow fires automatically. No human gate."""
    src = _read(".github/workflows/deploy.yml")
    assert "name: Auto-rollback if /health failed" in src
    # Must run only when the health check failed.
    assert "if: failure() && steps.health_check.outcome == 'failure'" in src
    # Must invoke the Rollback workflow via the gh CLI.
    assert "gh workflow run" in src
    assert "rollback.yml" in src


def test_deploy_workflow_has_actions_write_permission():
    """gh workflow run needs actions:write to trigger another workflow.
    Without this, the auto-rollback can't actually fire."""
    src = _read(".github/workflows/deploy.yml")
    assert "actions: write" in src


# ─── No-approval-gate for auto-deploy ───────────────────────────────


def test_deploy_workflow_does_not_require_environment_approval():
    """When auto-deploy is on, an environment with required reviewer
    would pause indefinitely (CI bot can't approve). The workflow no
    longer references the 'production' environment."""
    src = _read(".github/workflows/deploy.yml")
    # Active environment usage. Comments are fine.
    lines = [
        line.strip()
        for line in src.splitlines()
        if not line.strip().startswith("#")
    ]
    nonComment = "\n".join(lines)
    assert "environment: production" not in nonComment, (
        "Path 1 removed `environment: production`. If you're re-adding "
        "it, also configure the production env to NOT require reviewers, "
        "or auto-deploys will hang waiting for approval."
    )


# ─── Rollback workflow still exists + matches deploy's expectations ──


def test_rollback_workflow_accepts_reason_input():
    """The deploy workflow passes -f reason='Auto-rollback: ...' to the
    rollback workflow. If the rollback workflow doesn't declare a
    `reason` input, the auto-rollback fails."""
    src = _read(".github/workflows/rollback.yml")
    assert "inputs:" in src
    assert "reason:" in src


def test_rollback_workflow_listed_as_workflow_dispatch():
    """The rollback workflow must be dispatchable via `gh workflow run` —
    that requires the workflow_dispatch trigger."""
    src = _read(".github/workflows/rollback.yml")
    assert "workflow_dispatch:" in src
