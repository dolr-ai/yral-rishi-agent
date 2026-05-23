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
# WHY test_client USES LifespanManager + POOL INJECTION?
# httpx.ASGITransport alone does NOT drive ASGI lifespan events —
# FastAPI's startup + shutdown hooks do NOT fire with plain ASGITransport.
# Without explicit lifespan management the injected pool is never closed,
# leaking connections and causing flaky tests under parallel runs.
#
# `asgi-lifespan`'s LifespanManager is the canonical fix: it explicitly
# sends `lifespan.startup` + `lifespan.shutdown` ASGI messages around
# the AsyncClient context, so init_pool() + close_pool() both run.
#
# Pool injection pattern: pre-set `app.database._pool = pool` BEFORE
# LifespanManager fires startup. init_pool() is idempotent
# (`if _pool is not None: return`), so the testcontainers pool is used
# instead of opening a new connection to a production cluster. On
# lifespan shutdown, `close_pool()` closes the injected pool and sets
# `_pool = None`. The fixture asserts `_pool is None` at cleanup to
# verify shutdown ran correctly.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from asgi_lifespan import LifespanManager
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
          app.database._pool BEFORE the ASGI lifespan fires, then yields
          an httpx.AsyncClient backed by ASGITransport. The client sends
          real HTTP-shaped requests to the app entirely in-process (no
          network socket, no uvicorn process).
    WHEN: per test that explicitly requests `test_client` (opt-in).
    WHY:  tests the full HTTP contract — path params, query params, JSON
          response shapes, status codes — without running a real server.

    ASGI LIFESPAN (Codex PR #132 CONCERN fix):
    httpx.ASGITransport alone does NOT drive ASGI lifespan events
    (startup / shutdown). Without explicit lifespan management, the
    FastAPI `lifespan` context never runs, so `init_pool()` is never
    called from startup AND `close_pool()` never runs at teardown —
    the injected pool is never closed, leaking connections.

    The fix: `asgi-lifespan`'s `LifespanManager` wraps the FastAPI app
    and explicitly sends `lifespan.startup` and `lifespan.shutdown`
    ASGI messages. AsyncClient lives INSIDE the LifespanManager so
    requests are only made after startup completes.

    Pool lifecycle (corrected):
      1. Fixture creates pool + sets app.database._pool = pool.
      2. LifespanManager __aenter__ fires lifespan.startup →
         init_pool() sees `_pool is not None` → no-op (injection wins).
      3. TRUNCATE clears both tables for a clean per-test state.
      4. Tests run via the AsyncClient.
      5. AsyncClient __aexit__ finishes all in-flight requests.
      6. LifespanManager __aexit__ fires lifespan.shutdown →
         close_pool() closes the injected pool + sets _pool = None.
      7. Cleanup assertion confirms shutdown ran (pool reference gone).
      8. Fixture restores _pool = original_pool (None in normal flow).
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

    # Inject the pool BEFORE the LifespanManager fires startup.
    # init_pool() is idempotent: `if _pool is not None: return`.
    # Pre-injection means startup's init_pool() call is a no-op; the
    # app uses our testcontainers pool rather than connecting to a
    # production cluster.
    original_pool = db_module._pool
    db_module._pool = pool

    # LifespanManager drives lifespan.startup on __aenter__ and
    # lifespan.shutdown on __aexit__. AsyncClient is nested INSIDE so
    # all requests happen after startup completes and before shutdown fires.
    # raise_app_exceptions=True on ASGITransport propagates unhandled app
    # exceptions as Python exceptions so test failures show tracebacks.
    async with LifespanManager(fastapi_app):
        transport = ASGITransport(app=fastapi_app, raise_app_exceptions=True)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    # After LifespanManager __aexit__: close_pool() has run, the injected
    # pool is closed, and _pool is None. Assert this to catch any future
    # regression where lifespan shutdown stops calling close_pool().
    assert db_module._pool is None, (
        "Pool not closed at test teardown — lifespan shutdown did not call "
        "close_pool(). Check that app/main.py's lifespan still calls "
        "close_pool() in its shutdown block."
    )

    # Restore the original _pool value (None in normal flow) so the next
    # test fixture starts from a clean slate.
    db_module._pool = original_pool


# ===========================================================================
# RELATED FILES:
#   test_schema_migrations.py    — uses database_pool + alembic round-trip
#   test_conversation_routes.py  — uses test_client (Deliverable 2)
#   ../app/database.py           — _pool singleton that test_client injects
#   ../app/main.py               — FastAPI app + lifespan imported by test_client
#   ../app/migrations/env.py     — Alembic env reading the same connection string
#   ../alembic.ini               — points Alembic at app/migrations/
#   ../pyproject.toml            — declares testcontainers[postgres] + asgi-lifespan
#                                  dev deps
# ===========================================================================
