"""Phase 21αβ.I-Mig3 — source-pin migration-CI wiring.

The real verification is: CI runs the workflow on every PR. These
tests just defend the shape (right image, right triggers, right
sanity checks, idempotency check) against unintentional regression.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WF = REPO / ".github" / "workflows" / "migrations-ci.yml"


def _wf_body() -> str:
    return WF.read_text()


# ─── idempotency expansion (Rishi 2026-06-10) ────────────────────────────


def test_workflow_has_re_apply_idempotency_step():
    """The 2nd-run step must exist. Catches non-IF-NOT-EXISTS migrations
    that would corrupt prod state on any replay."""
    body = _wf_body()
    assert "Re-apply all migrations" in body
    assert "idempotency check" in body or "idempotency" in body


def test_idempotency_step_uses_on_error_stop():
    """Without ON_ERROR_STOP the 2nd-run psql would log errors but
    keep going, masking the non-idempotent migration."""
    body = _wf_body()
    pos = body.find("Re-apply all migrations")
    block = body[pos : pos + 3500]
    assert "ON_ERROR_STOP=1" in block


def test_idempotency_step_collects_all_failures():
    """Failing on the FIRST non-idempotent migration would only surface
    one bug at a time. The step must keep going and report ALL
    failures so a single CI run shows the full picture."""
    body = _wf_body()
    pos = body.find("Re-apply all migrations")
    block = body[pos : pos + 3500]
    assert "FAILED=$((FAILED + 1))" in block
    assert "FAILED_LIST" in block
    assert "set +e" in block and "set -e" in block


def test_post_reapply_rowcount_check():
    """Belt-and-braces: a migration could exit 0 from re-apply but
    silently mutate state. The post-re-apply step counts rows in the
    floor tables (all should be 0 since the test DB is empty)."""
    body = _wf_body()
    assert "Verify schema unchanged by the re-apply" in body
    pos = body.find("Verify schema unchanged")
    block = body[pos : pos + 2000]
    for tbl in ("ai_influencers", "conversations", "messages"):
        assert tbl in block
    assert "SELECT count(*) FROM" in block


# ─── original 6 tests (unchanged below) ──────────────────────────────────
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
