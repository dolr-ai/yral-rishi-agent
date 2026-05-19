# ---------------------------------------------------------------------------
# conftest.py — shared pytest fixtures for the soul-file-library tests.
#
# ⭐ START HERE: this file boots ONE ephemeral Postgres container per
# pytest session via `testcontainers-postgres`, runs Alembic
# `upgrade head` against it, exports the DSN as
# `POSTGRES_DSN_SOUL_FILE_LIBRARY` so the app's pool + Alembic see it,
# yields the connection details, then tears the container down on
# session end. Per-function fixtures give each test a clean slate by
# clearing the asyncpg pool's `lru_cache` + truncating the
# `soul_file_layers` table back to the seeded state.
#
# WHY testcontainers + NOT docker-compose
# The compose Postgres is for `uvicorn`-running development sessions
# (a developer typing `docker compose up`). Tests need a fresh DB per
# pytest run (no carry-over state from a previous run + no port-clash
# with a dev's already-running compose stack). `testcontainers-postgres`
# spins up Postgres on a random free port + tears down on exit — clean
# isolation, zero developer-machine assumptions.
#
# WHY ALEMBIC RUNS ONCE PER SESSION
# Migrations are slow-ish (creating tables, indexes, seeding rows). The
# session-scope fixture runs `upgrade head` ONCE, then per-function
# fixtures TRUNCATE-and-reseed for isolation. Round-trip up/down per-
# test would be ~100× slower for no extra coverage; the round-trip
# itself is covered by `test_schema_migrations.py` once per session.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from testcontainers.postgres import PostgresContainer

# Path to this service folder (so Alembic invocations are CWD-correct
# regardless of where pytest was launched from).
SERVICE_ROOT: Path = Path(__file__).resolve().parent.parent


# ===========================================================================
# Session-scoped Postgres container + Alembic
# ===========================================================================


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Spin up a Postgres 17 container for the entire test session.

    WHAT: starts `postgres:17-alpine` on a random port; yields the
          container handle; stops + removes on session end.
    WHEN: once per pytest run (scope=session).
    WHY:  test isolation from any existing local Postgres. Random port
          means parallel pytest runs don't collide.
    """
    # postgres:17-alpine matches the docker-compose.yml choice + the
    # cluster's Patroni runs the same major.
    with PostgresContainer("postgres:17-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_dsn(postgres_container: PostgresContainer) -> str:
    """Build an asyncpg-compatible DSN from the testcontainer.

    WHAT: extracts host/port/user/password/dbname from the container
          + reassembles as `postgresql://user:pass@host:port/database`.
    WHEN: derived once from the session-scoped container.
    WHY:  testcontainers exposes `get_connection_url()` which returns a
          SQLAlchemy-shaped URL (`postgresql+psycopg2://...`); strip the
          driver suffix for asyncpg + Alembic's URL-rewriting in
          `app/migrations/env.py`.
    """
    sqlalchemy_url = postgres_container.get_connection_url()
    # Convert `postgresql+psycopg2://...` → `postgresql://...`. asyncpg
    # parses the simple form; Alembic's env.py rewrites it back to
    # `postgresql+asyncpg://...` for the AsyncEngine.
    if sqlalchemy_url.startswith("postgresql+psycopg2://"):
        sqlalchemy_url = sqlalchemy_url.replace(
            "postgresql+psycopg2://", "postgresql://", 1
        )
    elif sqlalchemy_url.startswith("postgresql+psycopg://"):
        sqlalchemy_url = sqlalchemy_url.replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
    return sqlalchemy_url


@pytest.fixture(scope="session", autouse=True)
def run_alembic_upgrade(postgres_dsn: str) -> Iterator[None]:
    """Run `alembic upgrade head` against the testcontainer Postgres.

    WHAT: sets POSTGRES_DSN_SOUL_FILE_LIBRARY in the environment +
          shells out to `alembic upgrade head` from the service root.
    WHEN: once per pytest session, before any test runs.
    WHY:  bootstraps the schema + seeds the L1/L2/L4 rows every
          composer test relies on. Shelling out (instead of importing
          alembic-internal helpers) matches the prod-deploy code path —
          we're testing the SAME `alembic upgrade head` operator runs.
    """
    # Export the DSN so Alembic's env.py + the app's pool both see it.
    os.environ["POSTGRES_DSN_SOUL_FILE_LIBRARY"] = postgres_dsn

    # `alembic upgrade head` from the service root. capture_output=True
    # so failures dump alembic stdout/stderr into pytest's report.
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

    yield


# ===========================================================================
# Per-function fixtures
# ===========================================================================


@pytest.fixture()
def clean_app_settings_cache() -> Iterator[None]:
    """Clear the `get_settings()` lru_cache before AND after each test.

    WHAT: invalidates the cached Settings instance so monkeypatched env
          vars take effect.
    WHEN: pytest invokes this per-test (autouse=False on purpose so
          tests that need to keep settings frozen across multiple sub-
          calls can opt out).
    WHY:  `app/config.py::get_settings` is `@lru_cache(maxsize=1)`.
          Without this fixture, env-var mutations between tests would
          leak.
    """
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
async def database_pool(postgres_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    """Yield an asyncpg pool pointed at the testcontainer.

    WHAT: creates a fresh asyncpg.Pool; truncates `soul_file_layers`
          + re-runs the seed inserts so each test starts with the
          migration's known L1/L2/L4 state; yields the pool; closes
          on test exit.
    WHEN: per test that explicitly requests `database_pool`.
    WHY:  per-function isolation without re-running the slow migration.
          The TRUNCATE-and-reseed approach is much faster than a full
          alembic downgrade + upgrade per test.
    """
    pool = await asyncpg.create_pool(
        dsn=postgres_dsn,
        min_size=1,
        max_size=4,
        statement_cache_size=0,
    )

    # Reset to seeded state — truncate then reseed.
    await _truncate_and_reseed(pool)

    yield pool

    await pool.close()


async def _truncate_and_reseed(pool: asyncpg.Pool) -> None:
    """TRUNCATE soul_file_layers + re-run the migration's seeds.

    WHAT: empties the table + re-inserts L1 global + 3 × L2 archetype
          + 3 × L4 user_segment seed rows (matching the migration's
          seed block byte-for-byte). L3 rows NOT seeded by default —
          tests that need L3 add rows explicitly.
    WHEN: called from `database_pool` before yielding to a test.
    WHY:  fast per-test reset; avoids re-running alembic for each test.
    """
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE soul_file_layers;")

        # Layer 1 — global. scope_key='' per directive.
        await conn.execute(
            "INSERT INTO soul_file_layers (layer, scope_key, body) "
            "VALUES (1, '', $1);",
            "[v2 phase-1 day-4 Layer 1 placeholder — real global tone in "
            "day-5+ once product writes it]",
        )

        # Layer 2 — 3 archetypes.
        layer_2_bodies = {
            "companion": (
                "[v2 phase-1 day-4 Layer 2 companion archetype placeholder — "
                "real archetype copy from product on day-5+]"
            ),
            "therapist": (
                "[v2 phase-1 day-4 Layer 2 therapist archetype placeholder — "
                "real archetype copy from product on day-5+]"
            ),
            "coach": (
                "[v2 phase-1 day-4 Layer 2 coach archetype placeholder — "
                "real archetype copy from product on day-5+]"
            ),
        }
        for archetype, body in layer_2_bodies.items():
            await conn.execute(
                "INSERT INTO soul_file_layers (layer, scope_key, body) "
                "VALUES (2, $1, $2);",
                archetype,
                body,
            )

        # Layer 4 — 3 user segments.
        layer_4_bodies = {
            "new": (
                "[v2 phase-1 day-4 Layer 4 user-segment 'new' placeholder — "
                "real segment copy from product on day-5+]"
            ),
            "paying": (
                "[v2 phase-1 day-4 Layer 4 user-segment 'paying' placeholder — "
                "real segment copy from product on day-5+]"
            ),
            "dormant": (
                "[v2 phase-1 day-4 Layer 4 user-segment 'dormant' placeholder — "
                "real segment copy from product on day-5+]"
            ),
        }
        for segment, body in layer_4_bodies.items():
            await conn.execute(
                "INSERT INTO soul_file_layers (layer, scope_key, body) "
                "VALUES (4, $1, $2);",
                segment,
                body,
            )


@pytest.fixture()
def app_pool_bound(database_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Bind the test's asyncpg.Pool to the app's `app.database._pool` module global.

    WHAT: monkeypatches `app.database._pool` to the test fixture's pool so
          the composer + repository + HTTP route all see the same
          pool (instead of trying to call init_pool() which would
          open a SECOND pool).
    WHEN: tests that exercise the HTTP route or the composer's full
          path need both the pool AND the app to share state.
    WHY:  the FastAPI TestClient's lifespan would otherwise call
          init_pool() against the test DSN, but since we already have
          a pool from the test fixture, binding directly avoids the
          duplicate connection.
    """
    import app.database as app_database

    original_pool = app_database._pool
    app_database._pool = database_pool
    yield
    app_database._pool = original_pool


@pytest.fixture()
async def client(
    app_pool_bound: None, clean_app_settings_cache: None
) -> AsyncIterator[httpx.AsyncClient]:
    """httpx.AsyncClient with the test DB pool bound + settings clean.

    WHAT: yields an httpx.AsyncClient driving the FastAPI app in-process
          via ASGITransport. Runs in the test's event loop so the
          shared asyncpg pool's connections aren't crossed between loops
          (the classic TestClient + async-pool gotcha).
    WHEN: HTTP integration tests in `test_api_composed_prompt.py`.
    WHY:  FastAPI TestClient spins its own event loop for lifespan
          handling, which means the test fixture's asyncpg.Pool
          (created in the test's event loop) ends up out-of-loop for
          the TestClient's request handlers. AsyncClient + ASGITransport
          keeps everything on ONE loop. Same Starlette + FastAPI
          dispatch chain runs either way.
    """
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client


# ===========================================================================
# RELATED FILES:
#   __init__.py                      — package marker
#   test_schema_migrations.py        — alembic up/down round-trip test
#   test_repository.py               — uses database_pool fixture
#   test_composer.py                 — uses database_pool fixture + composer
#   test_api_composed_prompt.py      — uses client fixture
#   ../app/database.py                     — the module the app_pool_bound fixture patches
#   ../app/migrations/env.py         — Alembic env reading the same DSN
#   ../alembic.ini                   — points alembic at app/migrations
#   ../pyproject.toml                — declares testcontainers[postgres] dev dep
# ===========================================================================
