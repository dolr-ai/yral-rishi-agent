"""Migration runner — refusal vs replica-rejection are distinguishable.

Rishi 2026-06-10 EOD: deploy.yml's "✗ likely a replica" message fires
even when the runner refused for a config reason (e.g. empty
schema_migrations on a populated DB). The two are different failures
and warrant different log messages + different retry behavior.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_runner_exits_78_on_config_refusal():
    """sysexits.h `EX_CONFIG = 78` is the convention for "configuration
    error, not a transient one." The runner must use it on the
    schema_migrations-empty-but-schema-populated refusal so deploy.yml
    can distinguish from a writes-rejected exit-1."""
    body = (REPO / "scripts" / "ci" / "run-migrations.sh").read_text()
    # The refusal block exists and exits 78.
    pos = body.find("schema_migrations is empty but ai_influencers already exists")
    assert pos != -1
    block = body[pos : pos + 1500]
    assert "exit 78" in block, "config refusal must exit 78 (EX_CONFIG)"
    # And NOT the generic exit 1 anymore.
    assert "exit 1" not in block, (
        "config refusal must be the typed 78, not generic 1 — otherwise "
        "deploy.yml can't distinguish from a writes-rejected error"
    )


def test_deploy_yml_distinguishes_exit_codes():
    """The deploy workflow's manager-retry loop must check the runner's
    exit code, treat 78 as "halt the loop" and any other non-zero as
    "retry on next manager"."""
    body = (REPO / ".github" / "workflows" / "deploy.yml").read_text()
    # The loop captures the runner's RC.
    assert "RUNNER_RC=$?" in body
    # 78 → break out of the manager loop (no point retrying same refusal)
    assert "RUNNER_RC -eq 78" in body
    assert "REFUSED" in body
    # Non-78 still keeps the "likely a replica" hint (but with the
    # actual exit code surfaced, not just "failed").
    assert "(exit=$RUNNER_RC)" in body
    # The two messages must be distinct.
    assert "REFUSED" in body and "likely a replica" in body


def test_runner_keeps_zero_exit_on_success():
    """The success path (migrations applied OR no-op) must still exit 0
    — otherwise deploy.yml would never get the green branch."""
    body = (REPO / "scripts" / "ci" / "run-migrations.sh").read_text()
    # Look for the success log lines without an `exit` between them.
    assert "all migrations already applied" in body
    assert "migration(s) applied successfully" in body
