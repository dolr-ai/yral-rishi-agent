"""Phase 21αβ.I-Mig2 — source-pin the squawk migration linter wiring.

These are static-shape tests. Real lint coverage is verified when the
next migration PR runs squawk in CI.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github" / "workflows" / "squawk.yml"
CFG = REPO / ".squawk.toml"


def test_workflow_exists():
    assert WF.exists(), ".github/workflows/squawk.yml missing"


def test_config_exists():
    assert CFG.exists(), ".squawk.toml missing"


def test_workflow_triggers_on_migration_prs():
    """Workflow runs on PRs that touch migrations OR the linter config
    itself. Doesn't run on every PR (would be wasted CI minutes)."""
    body = WF.read_text()
    assert "pull_request:" in body
    assert "migrations/**" in body
    # The workflow itself + config are also triggers (so a config-only
    # tweak gets CI-tested).
    assert ".squawk.toml" in body


def test_squawk_version_pinned():
    """No `latest` tags anywhere in deploy. squawk must follow the
    same rule — a silent rule-set change would surprise us at PR time."""
    body = WF.read_text()
    assert ":latest" not in body
    # The download URL uses VERSION="X.Y.Z" interpolated into vX.Y.Z.
    assert 'VERSION="2.' in body


def test_lints_only_changed_migrations():
    """Historical migrations 001-032 predate this linter and aren't
    being fixed in this PR. Workflow lints ONLY the migrations changed
    in the current PR — `git diff origin/main...HEAD`."""
    body = WF.read_text()
    assert "git diff" in body
    assert "origin/main" in body


def test_filters_down_sql_files_from_diff():
    """Down-migrations are tracked separately and should not be linted
    by the same workflow."""
    body = WF.read_text()
    # The grep regex escapes dots — match that form.
    assert "down" in body and "sql" in body and "grep -v" in body


def test_config_excludes_have_justification_comments():
    """Every excluded rule must come with a justification comment.
    Otherwise the exclusion list silently grows as a 'just-fix-CI' move."""
    body = CFG.read_text()
    # The three excludes we currently document.
    for rule in ("prefer-text-field", "prefer-big-int", "transaction-nesting"):
        assert rule in body, f"missing excluded rule: {rule}"
    # And each has surrounding prose (the `#` comment lines).
    assert "VARCHAR" in body
    assert "BEGIN/COMMIT" in body
