"""Source-level pins for the 2026-06-09 defensive check that refuses to
replay 001+ on a populated database when schema_migrations is empty.

Without this check, a fresh `schema_migrations` table on a long-lived
cluster (created by `CREATE TABLE IF NOT EXISTS` on first runner pass)
looks identical to a fresh cluster — the runner would otherwise start
applying 001_initial.sql against an already-populated DB. The 2026-06-09
#314 deploy hit exactly this trap; only the pg_dump → S3 failure stopped
real damage.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "scripts" / "ci" / "run-migrations.sh"


def test_runner_checks_ai_influencers_existence_when_schema_migrations_is_empty():
    body = RUNNER.read_text()
    # The check must use ai_influencers as the proxy for "schema is
    # already populated" — it's in 001_initial.sql so it's the earliest
    # signal we can rely on.
    assert "ai_influencers" in body
    assert "information_schema.tables" in body


def test_runner_refuses_with_clear_message_on_empty_schema_migrations_plus_populated_schema():
    body = RUNNER.read_text()
    # The fatal message must explain WHY we refused — not just "exit 1".
    assert "schema_migrations is empty but ai_influencers already exists" in body
    assert "bootstrap" in body.lower()


def test_runner_provides_escape_hatch_for_fresh_clusters():
    body = RUNNER.read_text()
    # A legitimate fresh-cluster bootstrap path must exist — otherwise
    # this check would brick `make dev-up` or any new staging environment.
    assert "FORCE_RUN_ON_EMPTY_SCHEMA_MIGRATIONS" in body


def test_defensive_check_happens_before_first_apply():
    body = RUNNER.read_text()
    # The exit-1 for the defensive check must come BEFORE the first
    # `psql ... INSERT INTO schema_migrations` (i.e. before any migration
    # would actually be recorded as applied).
    refusal_pos = body.find("FATAL: schema_migrations is empty but")
    first_apply_pos = body.find("INSERT INTO schema_migrations")
    assert refusal_pos > 0
    assert first_apply_pos > 0
    assert refusal_pos < first_apply_pos, (
        "defensive check must fire before any migration application"
    )
