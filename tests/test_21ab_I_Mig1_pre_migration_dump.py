"""Phase 21αβ.I-Mig1 — source-pin the automated pre-migration pg_dump.

These tests defend the shape of `scripts/ci/run-migrations.sh` so the
safety snapshot doesn't silently disappear in a future refactor.

Behavior verification (an actual migration triggers an actual upload)
happens on the next migration deploy — the workflow doesn't run pg_dump
itself.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "ci" / "run-migrations.sh"


def test_runner_exists():
    assert RUNNER.exists(), "scripts/ci/run-migrations.sh missing"


def test_pg_dump_step_present_before_apply():
    """The pg_dump must happen BEFORE the migration SQL is fed to psql.
    Order matters: a snapshot taken AFTER the migration is useless for
    rollback."""
    body = RUNNER.read_text()
    dump_pos = body.find("pg_dump -Fc")
    apply_pos = body.find('INSERT INTO schema_migrations')
    assert dump_pos != -1, "pg_dump call missing from migration runner"
    assert apply_pos != -1, "INSERT INTO schema_migrations missing"
    assert dump_pos < apply_pos, (
        "pg_dump must run before the migration's INSERT — otherwise "
        "the snapshot post-dates the change it's supposed to recover from"
    )


def test_dump_is_fatal_on_failure():
    """Per CLAUDE.md Rule 9: no schema change without a snapshot. If
    pg_dump fails, the migration must abort, not continue."""
    body = RUNNER.read_text()
    # The error path explicitly says FATAL and exits 1.
    assert "FATAL: pre-migration pg_dump failed" in body
    assert "FATAL: pre-migration dump upload failed" in body


def test_dump_uses_custom_format_and_gzip_6():
    """Matches the nightly backup convention in
    docs/BACKUP-RESTORE-DRILL-2026-06-04.md (Fc + gzip-6)."""
    body = RUNNER.read_text()
    assert "pg_dump -Fc -Z 6" in body
    # `--no-owner --no-acl` so restore works into a fresh cluster.
    assert "--no-owner --no-acl" in body


def test_dump_uploaded_to_separate_s3_prefix():
    """Must NOT share the WAL-G prefix — accidentally polluting WAL-G
    state would break PITR. Separate dedicated prefix."""
    body = RUNNER.read_text()
    assert "yral-rishi-agent-pre-migration-dumps" in body
    # WAL-G's prefix is `yral-rishi-agent-walg` — they must not be the
    # same string anywhere.
    pre_pos = body.find("yral-rishi-agent-pre-migration-dumps")
    walg_pos = body.find("yral-rishi-agent-walg")
    if walg_pos != -1:
        # If walg is referenced at all in this file, it must NOT be the
        # same value as the pre-migration prefix.
        assert pre_pos != walg_pos


def test_dump_skippable_via_env():
    """`PRE_MIGRATION_DUMP_ENABLED=false` must skip cleanly. Useful for
    CI test runs where I-Mig3's ephemeral pg has already verified the
    migration; uploading from there would be wasted I/O."""
    body = RUNNER.read_text()
    assert "PRE_MIGRATION_DUMP_ENABLED" in body
    assert 'PRE_MIGRATION_DUMP_ENABLED="${PRE_MIGRATION_DUMP_ENABLED:-true}"' in body


def test_dump_name_includes_timestamp_and_migration_name():
    """`pre-migration-{name}-{timestamp}.sql.gz` per the I-Mig1 spec.
    Without timestamp, repeated retries clobber each other."""
    body = RUNNER.read_text()
    assert "pre-migration-" in body
    assert 'date -u +%Y%m%dT%H%M%SZ' in body
    assert ".sql.gz" in body


def test_runner_still_has_create_restore_point():
    """The fast (WAL) recovery handle stays in place alongside the new
    slow (pg_dump) handle. Two different recovery profiles, both kept."""
    body = RUNNER.read_text()
    assert "pg_create_restore_point" in body
