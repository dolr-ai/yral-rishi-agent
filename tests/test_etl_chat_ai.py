"""Continuous ETL — pin the table specs + interval + safety caps.

The live ETL pass is exercised after deploy with the operator-provided
CHAT_AI_DATABASE_URL; here we just guard the static configuration."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_synced_tables_in_dependency_order():
    """ai_influencers must precede conversations (which FK to it), which
    must precede messages (which FK to conversations). Wrong order = FK
    violations on first run."""
    from services.etl_chat_ai import SYNCED_TABLES

    names = [t["name"] for t in SYNCED_TABLES]
    assert names == ["ai_influencers", "conversations", "messages"]


def test_table_specs_have_required_keys():
    """Each spec needs name, columns, id_column — the upsert SQL builder
    KeyErrors otherwise. Test catches typos at load time, not at the
    first 5-min tick."""
    from services.etl_chat_ai import SYNCED_TABLES

    for spec in SYNCED_TABLES:
        assert "name" in spec
        assert isinstance(spec["columns"], list)
        assert len(spec["columns"]) > 0
        assert "id" in spec["columns"]
        assert "created_at" in spec["columns"]
        assert spec["id_column"] in spec["columns"]


def test_upsert_sql_uses_on_conflict_do_nothing():
    """Idempotency contract: re-running the same window must NOT change
    existing rows. ON CONFLICT DO NOTHING enforces it."""
    from services.etl_chat_ai import _build_upsert_sql

    sql = _build_upsert_sql("messages", ["id", "content"], "id")
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert "INSERT INTO messages" in sql
    assert "$1" in sql and "$2" in sql


def test_interval_5_min():
    """Below 1 min we'd hammer chat-ai; above 15 we'd stale out."""
    from services.etl_chat_ai import SYNC_INTERVAL_SEC

    assert 60 <= SYNC_INTERVAL_SEC <= 15 * 60


def test_safety_batch_limit_bounded():
    """Per-tick cap so one ETL tick can't monopolize the loop or pull
    millions of rows on a runaway."""
    from services.etl_chat_ai import SAFETY_BATCH_LIMIT, PAGE_SIZE

    assert 10 <= SAFETY_BATCH_LIMIT <= 100
    assert 100 <= PAGE_SIZE <= 5000


def test_chat_ai_dsn_reads_env(tmp_path):
    """Env-var fallback path. Used in local dev / CI where Swarm secrets
    aren't available. Helper must read dynamically (not at import time) so
    `docker service update --env-add` takes effect on the next loop tick."""
    from services.etl_chat_ai import _chat_ai_dsn

    # Point the file path at a non-existent file so the env-var branch is
    # exercised even on machines that happen to have /run/secrets populated.
    os.environ["CHAT_AI_DATABASE_URL_FILE"] = str(tmp_path / "does-not-exist")
    os.environ.pop("CHAT_AI_DATABASE_URL", None)
    try:
        assert _chat_ai_dsn() is None
        os.environ["CHAT_AI_DATABASE_URL"] = "postgresql://test"
        assert _chat_ai_dsn() == "postgresql://test"
    finally:
        os.environ.pop("CHAT_AI_DATABASE_URL", None)
        os.environ.pop("CHAT_AI_DATABASE_URL_FILE", None)


def test_chat_ai_dsn_reads_secret_file(tmp_path):
    """Swarm-secret path — file at CHAT_AI_DATABASE_URL_FILE takes priority
    over the env var. This is how prod activates the ETL: the secret is
    mounted at /run/secrets/chat_ai_database_url and the env var is never
    set, so the DSN stays out of `docker service inspect`."""
    from services.etl_chat_ai import _chat_ai_dsn

    secret = tmp_path / "chat_ai_database_url"
    secret.write_text("postgresql://from-file\n")  # trailing newline must strip
    os.environ["CHAT_AI_DATABASE_URL_FILE"] = str(secret)
    os.environ["CHAT_AI_DATABASE_URL"] = "postgresql://from-env-should-be-ignored"
    try:
        assert _chat_ai_dsn() == "postgresql://from-file"
    finally:
        os.environ.pop("CHAT_AI_DATABASE_URL", None)
        os.environ.pop("CHAT_AI_DATABASE_URL_FILE", None)


def test_chat_ai_dsn_empty_file_is_none(tmp_path):
    """An empty/whitespace-only file means the operator hasn't actually
    populated the secret yet — must treat as 'unset' so the loop idles
    instead of trying to connect with an empty DSN."""
    from services.etl_chat_ai import _chat_ai_dsn

    secret = tmp_path / "chat_ai_database_url"
    secret.write_text("   \n")
    os.environ["CHAT_AI_DATABASE_URL_FILE"] = str(secret)
    os.environ.pop("CHAT_AI_DATABASE_URL", None)
    try:
        assert _chat_ai_dsn() is None
    finally:
        os.environ.pop("CHAT_AI_DATABASE_URL_FILE", None)
