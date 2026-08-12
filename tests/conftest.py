"""Shared pytest fixtures for the yral-rishi-agent test suite.

Wave 1 PR6 — the testcontainers-Postgres harness. These fixtures give the
integration tests a REAL pgvector Postgres with the real asyncpg codecs,
which the old mocked-pool tests could never do (see
docs/wave1-plan-2026-07-29.md and the collage_date codec bug at
app/routes/chat.py:531).

Design notes (why it looks the way it does):

- `pytest-asyncio` is intentionally NOT a dependency. Adding it would make
  the ~38 existing `@pytest.mark.asyncio` tests suddenly execute for real
  — a separate, larger change. So the integration tests are plain SYNC
  functions that drive async code through `asyncio.run()`, and these
  fixtures are sync. Each test opens its own asyncpg connection/pool inside
  its own event loop, so there is no cross-loop pool sharing (asyncpg pools
  are bound to the loop that created them).

- The container + full schema are built ONCE per session (`pg_dsn`).
  Per-test isolation is a TRUNCATE of the tables the integration tests
  write to (`db`); migration-seeded config tables are left intact.

- Everything Docker/testcontainers is imported INSIDE the fixture, never at
  module top, so a run without the test extra (or without Docker) SKIPS the
  integration tests instead of erroring the whole suite.
"""

import asyncio
import glob
from pathlib import Path

import asyncpg
import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Tables the integration tests write to; truncated per test for isolation.
# CASCADE handles the messages -> conversations -> ai_influencers FK chain.
_TRUNCATE_TABLES = "messages, conversations, ai_influencers"


def _normalize_dsn(url: str) -> str:
    """testcontainers hands back a SQLAlchemy URL; asyncpg wants the plain form."""
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://"):
        if url.startswith(prefix):
            return url.replace(prefix, "postgresql://", 1)
    return url


async def _apply_migrations(dsn: str) -> int:
    """Apply migrations/*.sql (excluding *.down.sql) in filename order, the
    same order + set the prod runner uses. Bare execute per file mirrors
    `psql -f` (migrations-ci.yml); all of them are transaction-safe."""
    files = sorted(
        f for f in glob.glob(str(MIGRATIONS_DIR / "*.sql")) if not f.endswith(".down.sql")
    )
    conn = await asyncpg.connect(dsn)
    try:
        for path in files:
            await conn.execute(Path(path).read_text())
    finally:
        await conn.close()
    return len(files)


@pytest.fixture(scope="session")
def pg_dsn():
    """Session-scoped pgvector container with all migrations applied.

    Yields an asyncpg DSN string. Skips (does not fail) the integration
    tests if the test extra or Docker is unavailable.
    """
    try:
        # testcontainers moved the Postgres module under .community.
        try:
            from testcontainers.community.postgres import PostgresContainer
        except ImportError:
            from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed (pip install -r requirements-test.txt)")

    try:
        container = PostgresContainer("pgvector/pgvector:pg15")
        container.start()
    except Exception as exc:  # docker not running, image pull blocked, etc.
        pytest.skip(f"Docker unavailable for integration tests: {exc}")

    try:
        dsn = _normalize_dsn(container.get_connection_url())
        asyncio.run(_apply_migrations(dsn))
        yield dsn
    finally:
        container.stop()


@pytest.fixture
def db(pg_dsn):
    """Per-test isolation: truncate the app tables the integration tests
    write to, then yield the DSN. Tests seed the rows they need."""

    async def _truncate():
        conn = await asyncpg.connect(pg_dsn)
        try:
            await conn.execute(f"TRUNCATE {_TRUNCATE_TABLES} RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(_truncate())
    return pg_dsn
