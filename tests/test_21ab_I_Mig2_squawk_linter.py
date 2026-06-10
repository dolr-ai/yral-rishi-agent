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


# ─── 2026-06-10 expansion (Rishi EOD #4) ─────────────────────────────────


def test_workflow_documents_default_rules_we_depend_on():
    """squawk's defaults enforce the dangerous-DDL rules we care about
    (ban-drop-column, adding-required-field, renaming-column, etc.).
    The workflow must document them explicitly so a future contributor
    knows what's covered without reading squawk source."""
    body = WF.read_text()
    for rule in (
        "ban-drop-column",
        "ban-drop-table",
        "renaming-column",
        "renaming-table",
        "adding-required-field",
        "adding-not-nullable-field",
        "constraint-missing-not-valid",
        "require-concurrent-index-creation",
    ):
        assert rule in body, f"workflow must document dependence on `{rule}`"


def test_lock_timeout_check_exists():
    """squawk doesn't have a rule for 'every long-lived migration must
    declare lock_timeout'. The complementary grep check fills that gap
    so prod ops can read worst-case blocking duration from the file."""
    body = WF.read_text()
    assert "Require SET lock_timeout" in body
    # The check looks for the long-lived markers
    assert "CREATE INDEX" in body
    assert "ALTER TABLE" in body
    # And requires `SET lock_timeout` declaration
    assert "SET\\s+(LOCAL\\s+)?lock_timeout" in body


def test_lock_timeout_check_exempts_short_DDL():
    """A migration that only does CREATE TABLE / ADD COLUMN doesn't need
    its own lock_timeout — the runner's default (5s) covers it. The
    check must EXEMPT those, not pessimistically fail every migration."""
    body = WF.read_text()
    pos = body.find("Require SET lock_timeout")
    block = body[pos : pos + 3000]
    assert "runner" in block.lower() and "default" in block.lower()
    # The else branch logs ✓ for short DDL
    assert "short DDL only" in block or "covers it" in block


def test_lock_timeout_check_collects_all_failures():
    """One CI run should surface every offending migration, not just
    the first. Important when a PR ships multiple migrations."""
    body = WF.read_text()
    pos = body.find("Require SET lock_timeout")
    block = body[pos : pos + 3000]
    assert "FAILED=$((FAILED + 1))" in block
