"""Source-level pins for the 2026-06-09 auto-apply-migrations step in
deploy.yml. Closes the "expand part of expand-then-contract" gap: when
a PR ships code that depends on a new column, the migration must apply
BEFORE the new image starts serving traffic.

These tests are workflow-shape assertions — they don't run the SSH steps.
The real validation happens on the next live deploy with a migration in it.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(p: str) -> str:
    return (ROOT / p).read_text()


def test_deploy_workflow_applies_migrations_before_rolling_image():
    """The new step must exist + must run BEFORE the docker service
    update step. Otherwise the new image starts serving traffic on the
    old schema and crashes on the first request that uses the new column."""
    src = _read(".github/workflows/deploy.yml")
    apply_pos = src.find("Apply pending migrations BEFORE rolling new image")
    deploy_pos = src.find("Deploy via first responding swarm manager")
    assert apply_pos > 0, "missing migration-apply step"
    assert deploy_pos > 0, "missing deploy step"
    assert apply_pos < deploy_pos, (
        "migration-apply step must come BEFORE deploy step — otherwise new "
        "image starts on old schema and crashes"
    )


def test_apply_migrations_step_has_failover_across_managers():
    """If the first manager we try is a Patroni replica, writes fail
    (read-only). The step must retry on other managers until it hits
    the leader. Same failover pattern as the docker service update step."""
    src = _read(".github/workflows/deploy.yml")
    start = src.find("Apply pending migrations BEFORE rolling new image")
    end = src.find("Deploy via first responding swarm manager")
    body = src[start:end]
    # Must reference all 3 swarm managers in a loop.
    assert "SWARM_MANAGER_1" in body
    assert "SWARM_MANAGER_2" in body
    assert "SWARM_MANAGER_3" in body
    # Must have a loop construct + break on success.
    assert "for host in" in body
    assert "break" in body


def test_apply_migrations_step_uses_repo_runner_script():
    """The runner script (PR #309) already does pg_dump → S3 + restore
    point + apply. The workflow must use that exact script — don't
    reinvent the safety net."""
    src = _read(".github/workflows/deploy.yml")
    assert "scripts/ci/run-migrations.sh" in src


def test_apply_migrations_step_halts_deploy_on_failure():
    """If migrations fail (or all managers are unreachable), the step
    must exit non-zero so the deploy job stops before touching the
    swarm. Old image keeps serving on old schema; no broken state."""
    src = _read(".github/workflows/deploy.yml")
    start = src.find("Apply pending migrations BEFORE rolling new image")
    end = src.find("Deploy via first responding swarm manager")
    body = src[start:end]
    assert "exit 1" in body, (
        "migration-apply step must exit non-zero on failure so the deploy "
        "halts before rolling the new image"
    )


def test_apply_migrations_stages_project_config():
    """The runner script reads POSTGRES_DB from project.config (via
    `source` in the script). The workflow must scp project.config to
    the host alongside migrations/ + the script itself."""
    src = _read(".github/workflows/deploy.yml")
    start = src.find("Apply pending migrations BEFORE rolling new image")
    end = src.find("Deploy via first responding swarm manager")
    body = src[start:end]
    assert "project.config" in body, (
        "must scp project.config — the runner reads POSTGRES_DB from it"
    )


def test_apply_migrations_uses_unique_staging_dir():
    """If two deploys run back-to-back (which is rare given concurrency
    lock, but possible), staging dirs must not collide."""
    src = _read(".github/workflows/deploy.yml")
    start = src.find("Apply pending migrations BEFORE rolling new image")
    end = src.find("Deploy via first responding swarm manager")
    body = src[start:end]
    # Either commit SHA or PID — anything that varies per run.
    assert "resolve_sha.outputs.sha" in body or "$$" in body, (
        "staging dir must be unique per run to avoid collisions"
    )


def test_apply_migrations_cleans_up_staging_dir():
    """Don't leave a copy of the migrations files in /tmp/ on the host
    after every deploy. Best-effort cleanup at the end of each attempt."""
    src = _read(".github/workflows/deploy.yml")
    start = src.find("Apply pending migrations BEFORE rolling new image")
    end = src.find("Deploy via first responding swarm manager")
    body = src[start:end]
    assert "rm -rf $STAGING_DIR" in body, (
        "must clean up /tmp/migrate-* staging dir after each manager attempt"
    )


def test_run_migrations_script_handles_no_pending_case():
    """When no new migrations are present (most deploys), the script
    must skip cleanly so deploys don't break on the every-other-day
    case where nothing schema-related changed."""
    src = _read("scripts/ci/run-migrations.sh")
    assert "no pending migrations" in src or "no .sql files" in src
