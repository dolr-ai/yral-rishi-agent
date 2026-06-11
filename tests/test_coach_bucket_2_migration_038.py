"""Coach Bucket 2 PR-1 — migration 038 source-pin.

Adds `system_instructions_sections JSONB NOT NULL DEFAULT '[]'::jsonb`
on ai_influencers. Default is the small literal `[]`, which keeps the
ALTER TABLE metadata-only on pg11+ (no row rewrite on the 3,941
ai_influencers rows).

These tests pin the migration's exact shape so a future refactor that
breaks any of (column name, JSONB type, NOT NULL, default `[]`, the
squawk preamble, the Rule-9 documentation) gets caught here. The
behavioural sections-aware compose() path + Coach META_PROMPT changes
are pinned in PR-2's test file.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIG = REPO / "migrations" / "038_ai_influencers_system_instructions_sections.sql"


def test_migration_038_exists():
    assert MIG.exists(), "migration 038 missing"


def test_migration_038_adds_column_idempotently():
    """IF NOT EXISTS lets the migration runner be idempotent — re-running
    after a partial failure shouldn't error."""
    src = MIG.read_text()
    assert "ALTER TABLE ai_influencers" in src
    assert "ADD COLUMN IF NOT EXISTS system_instructions_sections" in src


def test_migration_038_uses_jsonb_not_null_with_empty_array_default():
    """JSONB NOT NULL DEFAULT '[]'::jsonb. NOT NULL keeps compose() from
    needing a null-guard at chat time; '[]' makes the ALTER TABLE
    metadata-only on pg11+ (no row rewrite). NOT NULL without DEFAULT
    would have failed on the existing 3,941 rows."""
    src = MIG.read_text()
    assert "JSONB NOT NULL" in src
    assert "DEFAULT '[]'::jsonb" in src


def test_migration_038_has_squawk_preamble():
    """Per I-Mig2 rule (#340): every migration that touches a populated
    table must declare its own lock_timeout + statement_timeout so prod
    ops can read worst-case blocking duration from the file alone.

    ai_influencers gets 30s lock_timeout (NOT the 3s default the other
    migrations use) because it's the hottest read table on the
    service. The 2026-06-11T09:46Z deploy of 038 timed out at 3s with
    `canceling statement due to lock timeout`; 30s lets the ALTER slot
    in between in-flight chat-send reads."""
    src = MIG.read_text()
    assert "SET lock_timeout = '30s';" in src
    assert "SET statement_timeout = '60s';" in src


def test_migration_038_documents_rule_9():
    """Rule 9: pg_dump before schema changes. The runner's auto-pg_dump
    (PR #309) IS the safety net — the migration must point at it so an
    operator reading the file alone sees both halves of the contract."""
    src = MIG.read_text()
    assert "Rule 9" in src
    assert "pg_dump" in src.lower()


def test_migration_038_documents_metadata_only_safety():
    """The reason this is safe on a live 3,941-row table — pin it so a
    future migration author copying this pattern understands WHY the
    DEFAULT is a small literal value and not a function call."""
    src = MIG.read_text()
    assert "metadata-only" in src
    assert "pg11+" in src or "pg11" in src


def test_migration_038_documents_section_shape_in_comment():
    """COMMENT ON COLUMN should describe the JSONB shape so an operator
    inspecting the DB schema sees what the array elements look like
    without grepping the codebase."""
    src = MIG.read_text()
    assert "COMMENT ON COLUMN" in src
    # The four required fields per the contract
    for field in ("id", "heading", "body", "editable"):
        assert field in src, f"section field '{field}' missing from migration comment"


def test_migration_038_flags_dependency_on_flag_and_pr2():
    """Header must point at COACH_SECTIONED_V2_ENABLED + PR-2 so a
    reader who lands on 038 first understands it's dormant on its own.
    Without this pin, someone could mistakenly think 038 changes
    chat-time behaviour."""
    src = MIG.read_text()
    assert "COACH_SECTIONED_V2_ENABLED" in src
    assert "PR-2" in src


def test_migration_038_points_at_contract_doc():
    """The contract doc is the canonical spec. Source-pin the link so
    future-Coach/future-Rishi can find it from the migration."""
    src = MIG.read_text()
    assert "coach-bucket-2-sections-contract.md" in src
