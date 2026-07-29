"""Integration: the schema builds and the app's DB layer works against a
REAL pgvector Postgres.

These are the "does the harness actually work end-to-end" tests — a real
container, the 47 migrations applied, and the app's own pool + health code
exercised against it. See tests/conftest.py.
"""

import asyncio

import asyncpg

CONTRACT_TABLES = ("ai_influencers", "conversations", "messages")


def test_all_migrations_applied_and_contract_tables_present(pg_dsn):
    """All 47 migrations applied cleanly (the pg_dsn fixture would have
    failed otherwise) and the three mobile-contract tables exist — the same
    floor migrations-ci.yml checks."""

    async def _check():
        conn = await asyncpg.connect(pg_dsn)
        try:
            return {
                t: await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t)
                for t in CONTRACT_TABLES
            }
        finally:
            await conn.close()

    present = asyncio.run(_check())
    missing = [t for t, ok in present.items() if not ok]
    assert not missing, f"missing contract tables after migrations: {missing}"


def test_pgvector_extension_enabled(pg_dsn):
    """Migration 008 needs the pgvector extension — this is why the test DB
    is pgvector/pgvector:pg15 and not plain postgres."""

    async def _check():
        conn = await asyncpg.connect(pg_dsn)
        try:
            return await conn.fetchval(
                "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
            )
        finally:
            await conn.close()

    assert asyncio.run(_check()) == 1


def test_check_db_health_true_against_real_db(pg_dsn):
    """Bind the app's `database._pool` global to the real container and call
    the app's own `check_db_health()` — proves the pool-binding harness that
    the HTTP integration tests rely on actually works."""
    import database  # app/database.py, via pyproject pythonpath

    async def _run():
        pool = await asyncpg.create_pool(dsn=pg_dsn, min_size=1, max_size=2)
        database._pool = pool
        try:
            return await database.check_db_health()
        finally:
            database._pool = None
            await pool.close()

    assert asyncio.run(_run()) is True
