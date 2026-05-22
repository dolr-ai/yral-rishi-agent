# ---------------------------------------------------------------------------
# conftest.py — shared pytest fixtures for the user-memory-service tests.
#
# ⭐ START HERE: this file boots ONE ephemeral Postgres container per
# pytest session via `testcontainers-postgres`, runs Alembic
# `upgrade head` against it (both migrations 001 + 002), exports the
# connection string as POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE
# so the app's pool and Alembic can both find it, yields the connection
# details, then tears the container down on session end.
#
# FIXTURE HIERARCHY:
#   postgres_container (scope=session)
#     └── postgres_connection_string (scope=session)
#           └── run_alembic_upgrade (scope=session, autouse=True)
#                 ├── database_pool (scope=function)
#                 │     └── clean_app_settings_cache (scope=function)
#                 └── test_client (scope=function)   ← Deliverable 2 addition
#
# WHY testcontainers + NOT docker-compose?
# The compose Postgres is for `uvicorn`-running development sessions.
# Tests need a FRESH DB per pytest run (no carry-over state from a
# previous run + no port-clash with a dev's already-running compose
# stack). `testcontainers-postgres` spins up Postgres on a random free
# port + tears down on exit — clean isolation, zero assumptions about
# what's running on the developer's machine.
#
# WHY ALEMBIC RUNS ONCE PER SESSION (not per test)?
# Migrations are slow-ish (creating tables, indices). The session-scope
# fixture runs `upgrade head` ONCE, then per-function fixtures truncate
# + re-insert for isolation. Round-trip up/down per-test would be ~100×
# slower for no extra coverage — the round-trip itself is covered by
# `test_schema_migrations.py` once per session.
#
# WHY test_client INJECTS THE POOL BEFORE THE LIFESPAN RUNS?
# When httpx's AsyncClient enters its context manager, FastAPI's lifespan
# fires. The lifespan calls `init_pool()`, which is idempotent:
#   `if _pool is not None: return`
# By pre-setting `app.database._pool` to our test pool BEFORE creating
# the client, we ensure `init_pool()` skips the real DB connect and uses
# the testcontainers pool instead. On lifespan shutdown, `close_pool()`
# closes the injected pool and sets `_pool = None`. The test_client
# fixture resets `_pool = None` at cleanup for a clean slate.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from testcontainers.postgres import PostgresContainer


# Path to the service folder root — used as CWD for `alembic upgrade head`
# so the command resolves `alembic.ini` + `app/migrations/` correctly
# regardless of where pytest was launched from.
SERVICE_ROOT: Path = Path(__file__).resolve().parent.parent


# ===========================================================================
# Session-scoped fixtures — run ONCE for the entire pytest session
# ===========================================================================


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Spin up a Postgres 17 container for the entire test session.

    WHAT: starts `postgres:17-alpine` on a random available port; yields
          the container handle so other session fixtures can build a
          connection string from it; stops + removes the container on
          session end.
    WHEN: once per pytest run (scope=session — NOT per test).
    WHY:  test isolation from any existing local Postgres instance.
          Random port means parallel pytest runs don't collide on 5432.
    """
    # postgres:17-alpine matches docker-compose.yml + the cluster's
    # Patroni version so local tests exercise the same behaviour as prod.
    with PostgresContainer("postgres:17-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_connection_string(postgres_container: PostgresContainer) -> str:
    """Build an asyncpg-compatible connection string from the testcontainer.

    WHAT: extracts host/port/user/password/dbname from the running
          container + assembles as `postgresql://user:pass@host:port/db`.
    WHEN: derived once from the session-scoped container.
    WHY:  testcontainers' `get_connection_url()` returns a SQLAlchemy-
          shaped URL (`postgresql+psycopg2://...`); we strip the driver
          suffix so asyncpg + Alembic's env.py (which re-adds
          `+asyncpg` for the AsyncEngine) both see the plain form.
    """
    # testcontainers returns a SQLAlchemy URL with a psycopg2 driver suffix.
    sqlalchemy_url = postgres_container.get_connection_url()

    # Strip the driver suffix. asyncpg accepts `postgresql://...` directly;
    # env.py rewrites it to `postgresql+asyncpg://` for Alembic's engine.
    if sqlalchemy_url.startswith("postgresql+psycopg2://"):
        return sqlalchemy_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    if sqlalchemy_url.startswith("postgresql+psycopg://"):
        return sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)
    # Already in plain form (some testcontainers versions skip the suffix).
    return sqlalchemy_url


@pytest.fixture(scope="session", autouse=True)
def run_alembic_upgrade(postgres_connection_string: str) -> Iterator[None]:
    """Run `alembic upgrade head` against the testcontainer Postgres.

    WHAT: sets POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE in the
          process environment + shells out to `alembic upgrade head`
          from the service root (so alembic.ini is found).
    WHEN: once per pytest session, BEFORE any test function runs
          (autouse=True + session scope ensures this).
    WHY:  bootstraps the schema — conversations + messages tables +
          indices — that every subsequent test depends on. Shelling out
          (not importing alembic internals) matches the production code
          path exactly: we test the SAME command the operator runs.
    """
    # Export the connection string into the process environment so
    # env.py reads it via os.environ.get(...). The env var name matches
    # the D8-required name in secrets.yaml.
    os.environ["POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE"] = postgres_connection_string

    # Run `alembic upgrade head` from the service root.
    # capture_output=True so failures print alembic's stdout/stderr.
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=SERVICE_ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed (exit {result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}\n"
        )

    # Yield control to the tests. Container + schema stay alive for the
    # entire session; cleanup happens after the last test exits.
    yield


# ===========================================================================
# Per-function fixtures — run ONCE PER TEST FUNCTION
# ===========================================================================


@pytest.fixture()
def clean_app_settings_cache() -> Iterator[None]:
    """Clear the `get_settings()` lru_cache before AND after each test.

    WHAT: invalidates the cached Settings instance so env-var mutations
          (monkeypatch.setenv) within a test actually take effect.
    WHEN: pytest invokes this per-test (autouse=False — opt-in so tests
          that WANT stable settings across sub-calls can skip it).
    WHY:  `app/config.py::get_settings` is `@lru_cache(maxsize=1)`.
          Without this fixture, env-var mutations between tests leak
          into subsequent tests via the stale cache.
    """
    # Clear before the test so a previous test's settings don't bleed in.
    from app.config import get_settings
    get_settings.cache_clear()

    yield

    # Clear after the test so this test's settings don't bleed out.
    get_settings.cache_clear()


@pytest.fixture()
async def database_pool(postgres_connection_string: str) -> AsyncIterator[asyncpg.Pool]:
    """Yield an asyncpg pool pointed at the testcontainer.

    WHAT: creates a fresh asyncpg.Pool; truncates both tables so each
          test starts with an empty (but migrated) database; yields the
          pool; closes on test exit.
    WHEN: per test that explicitly requests `database_pool`.
    WHY:  per-function isolation without re-running the slow migration.
          TRUNCATE-based reset is faster than alembic downgrade+upgrade
          per test. H11 spirit — schema round-trip stays in its own
          dedicated test (test_schema_migrations.py).
    """
    # Open a fresh pool for this test. Separate from the app's pool so
    # the test can inspect the DB independently.
    pool = await asyncpg.create_pool(
        dsn=postgres_connection_string,
        min_size=1,
        max_size=4,
        # Disable prepared-statement cache — required when pgBouncer
        # transaction-mode is in use (cluster default). Consistent with
        # the app's own pool config in database.py.
        statement_cache_size=0,
    )

    # Empty both tables before yielding. TRUNCATE CASCADE clears
    # messages first (FK child) then conversations automatically.
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE conversations CASCADE;")

    yield pool

    # Close the pool cleanly when the test exits.
    await pool.close()


@pytest.fixture()
async def test_client(postgres_connection_string: str) -> AsyncIterator[AsyncClient]:
    """Yield an httpx AsyncClient wired to the FastAPI app + testcontainers pool.

    WHAT: creates a fresh asyncpg pool for this test, injects it into
          app.database._pool BEFORE the FastAPI lifespan runs, then yields
          an httpx.AsyncClient backed by the ASGI transport. The client can
          make real HTTP-shaped requests to the app without a network socket.
    WHEN: per test that explicitly requests `test_client` (opt-in).
    WHY:  tests the full HTTP contract (path params, query params, status
          codes, JSON response shapes) without spinning up a real uvicorn
          server. The injection pattern avoids a second pool creation in
          the lifespan — see the file-header "WHY test_client INJECTS"
          explanation.

    Pool lifecycle:
      1. Fixture creates pool + sets `app.database._pool = pool`.
      2. AsyncClient __aenter__ fires the FastAPI lifespan startup;
         `init_pool()` sees `_pool is not None` and returns immediately
         (no second connection to the DB).
      3. TRUNCATE clears both tables so each test starts with an empty DB.
      4. Tests run.
      5. AsyncClient __aexit__ fires the lifespan shutdown; `close_pool()`
         closes the pool and sets `_pool = None`.
      6. Fixture cleanup confirms `_pool = None` for the next test.
    """
    import app.database as db_module
    from app.main import app as fastapi_app

    # Create a dedicated pool for this test (not shared with database_pool).
    # statement_cache_size=0 is required for pgBouncer transaction-mode
    # compatibility — same setting as the production pool in database.py.
    pool = await asyncpg.create_pool(
        dsn=postgres_connection_string,
        min_size=1,
        max_size=4,
        statement_cache_size=0,
    )

    # Truncate all tables so this test starts with clean state.
    # TRUNCATE CASCADE clears messages first (FK child), then conversations.
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE conversations CASCADE;")

    # Inject the pool BEFORE the AsyncClient context enters (which triggers
    # the lifespan). init_pool() checks `if _pool is not None: return`, so
    # this injection prevents a second pool from being created.
    original_pool = db_module._pool
    db_module._pool = pool

    # ASGITransport runs the ASGI app directly in-process — no socket, no
    # network, no port binding. FastAPI lifespan fires on __aenter__/__aexit__
    # of the AsyncClient context. raise_app_exceptions=True propagates
    # unhandled app errors as Python exceptions (rather than 500 responses)
    # so test failures are debuggable without reading the response body.
    transport = ASGITransport(app=fastapi_app, raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # After __aexit__, close_pool() has already closed the pool and set
    # _pool = None. Reset to original value (None in normal flow) for a
    # clean slate before the next test.
    db_module._pool = original_pool


# ===========================================================================
# RELATED FILES:
#   test_schema_migrations.py    — uses database_pool + alembic round-trip
#   test_conversation_routes.py  — uses test_client (Deliverable 2)
#   ../app/database.py           — _pool singleton that test_client injects
#   ../app/main.py               — FastAPI app + lifespan imported by test_client
#   ../app/migrations/env.py     — Alembic env reading the same connection string
#   ../alembic.ini               — points Alembic at app/migrations/
#   ../pyproject.toml            — declares testcontainers[postgres] + httpx devdeps
# ===========================================================================
