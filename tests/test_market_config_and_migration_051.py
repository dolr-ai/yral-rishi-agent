"""US market launch PR1 — market column + dormant config.

Spec: docs/us-market-launch-spec-2026-08-08.md (Track B, PR1).

PR1 ships DORMANT: the column exists, the knobs exist, and no application
code reads either. So the tests that matter here are the ones that pin the
*safety* properties rather than behaviour:

  - the flags default to off, so merging changes nothing for anyone
  - `_env_list("")` is `[]` and not `[""]`, because a stray empty string
    would make the "is this market exclusive?" check truthy and silently
    arm the filter in PR2
  - the migration is additive, has no backfill, and carries the Squawk
    timeouts — a DEFAULT or an UPDATE here would rewrite a hot ~3,600-row
    table and change what every existing user sees

The migration assertions are source-level on purpose: this file has to
pass in CI with no database, and the real-DB coverage lands with the PR2
filter where there is behaviour worth exercising.
"""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "migrations" / "051_ai_influencers_target_markets.sql"


def _executable_sql() -> str:
    """Migration text with `--` comment lines dropped.

    The header comments discuss DDL ("CREATE INDEX CONCURRENTLY", why we
    skip it), so any test that reasons about statement ORDER has to look
    at what actually executes — matching on the raw file finds the prose
    first and asserts something meaningless."""
    return "\n".join(
        line
        for line in MIGRATION.read_text().splitlines()
        if not line.lstrip().startswith("--")
    )


# ─── _env_list helper ───────────────────────────────────────────────────


def test_env_list_empty_is_empty_list_not_blank_string():
    """`[""]` would be truthy and would arm the market filter in PR2."""
    from config import _env_list

    assert _env_list("NONEXISTENT_VAR_12345", "") == []


def test_env_list_splits_and_trims():
    from config import _env_list

    os.environ["TEST_MARKET_LIST"] = " US , CA,GB "
    try:
        assert _env_list("TEST_MARKET_LIST") == ["US", "CA", "GB"]
    finally:
        del os.environ["TEST_MARKET_LIST"]


def test_env_list_drops_empty_entries():
    """Trailing/doubled commas are a normal hand-editing slip in a Swarm
    env var; they must not produce phantom markets."""
    from config import _env_list

    os.environ["TEST_MARKET_LIST"] = "US,,CA,"
    try:
        assert _env_list("TEST_MARKET_LIST") == ["US", "CA"]
    finally:
        del os.environ["TEST_MARKET_LIST"]


def test_env_list_single_value():
    from config import _env_list

    os.environ["TEST_MARKET_LIST"] = "US"
    try:
        assert _env_list("TEST_MARKET_LIST") == ["US"]
    finally:
        del os.environ["TEST_MARKET_LIST"]


# ─── dormant defaults ───────────────────────────────────────────────────


def test_market_exclusive_countries_defaults_to_empty():
    """The dormancy guarantee: no env set → no market is exclusive."""
    import config

    assert config.MARKET_EXCLUSIVE_COUNTRIES == []


def test_market_debug_override_defaults_to_disabled():
    """X-Market-Debug is spoofable; it must be opt-in, never on by default."""
    import config

    assert config.MARKET_DEBUG_OVERRIDE_ENABLED is False


# ─── migration 051 ──────────────────────────────────────────────────────


def test_migration_exists_and_uses_the_next_free_number():
    """044 and 049 are reserved by open PRs (migrations/README.md); 051 is
    the next free number above main's highest."""
    assert MIGRATION.exists()
    claimed = {p.name.split("_")[0] for p in (REPO / "migrations").glob("*.sql")}
    assert "044" not in claimed and "049" not in claimed


def test_migration_is_additive_and_idempotent():
    sql = _executable_sql()
    assert "ADD COLUMN IF NOT EXISTS target_markets TEXT[]" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_ai_influencers_target_markets" in sql
    assert "USING GIN (target_markets)" in sql


def test_migration_declares_squawk_timeouts_before_any_ddl():
    """Both timeouts, and before the first DDL — the PR #427 lesson."""
    sql = _executable_sql()
    lock = sql.index("SET lock_timeout")
    stmt = sql.index("SET statement_timeout")
    first_ddl = min(sql.index("ALTER TABLE"), sql.index("CREATE INDEX"))
    assert lock < first_ddl and stmt < first_ddl


def test_migration_has_no_default_and_no_backfill():
    """NULL means global. A DEFAULT or an UPDATE would rewrite the table
    and change what every existing user sees — the opposite of dormant."""
    sql = _executable_sql().upper()
    assert "DEFAULT" not in sql
    assert "UPDATE " not in sql
    assert "NOT NULL" not in sql


def test_migration_does_not_touch_any_other_table():
    """Blast radius is exactly one table."""
    sql = _executable_sql()
    assert sql.upper().count("ALTER TABLE") == 1
    assert "ai_influencers" in sql
