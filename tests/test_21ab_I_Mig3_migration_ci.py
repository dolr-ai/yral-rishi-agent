"""Phase 21αβ.I-Mig3 — source-pin migration-CI wiring.

The real verification is: CI runs the workflow on every PR. These
tests just defend the shape (right image, right triggers, right
sanity check) against unintentional regression.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github" / "workflows" / "migrations-ci.yml"


def test_workflow_exists():
    assert WF.exists(), "migrations-ci.yml missing"


def test_triggers_on_pr_and_push_to_main():
    body = WF.read_text()
    assert "push:" in body
    assert "pull_request:" in body
    assert "branches: [main]" in body
    # The path filter limits churn — workflow only runs when migrations
    # or the workflow itself changes.
    assert "migrations/**" in body


def test_uses_pgvector_image_pinned_to_pg15():
    """pgvector/pgvector:pg15 — needed for migration 008's CREATE
    EXTENSION vector, and pg15 matches V2 production."""
    body = WF.read_text()
    assert "pgvector/pgvector:pg15" in body
    # No `:latest` anywhere — explicit pins only.
    assert ":latest" not in body


def test_applies_with_on_error_stop():
    """ON_ERROR_STOP is the difference between 'CI green on broken
    migration' and 'CI fails on broken migration'. Without it psql
    keeps going past errors and exits 0."""
    body = WF.read_text()
    assert "ON_ERROR_STOP=1" in body


def test_sanity_checks_three_critical_tables():
    """If migrations applied but didn't create the floor tables, the
    runtime can't boot. CI must catch that here, not at deploy time."""
    body = WF.read_text()
    for tbl in ("ai_influencers", "conversations", "messages"):
        assert tbl in body, f"sanity check missing table: {tbl}"


def test_filters_out_down_sql_files():
    """The runner ignores `.down.sql` files; CI must mirror that or
    it'd apply downs in lex order with ups, which would break."""
    body = WF.read_text()
    assert "*.down.sql" in body
