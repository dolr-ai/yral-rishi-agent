# ---------------------------------------------------------------------------
# env.py — Alembic environment script. Invoked by `alembic upgrade` /
# `alembic downgrade` before any migration runs.
#
# ⭐ START HERE: this file builds the SQLAlchemy engine from the
# POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE env var (the per-service
# secret declared in `secrets.yaml` per D8) and dispatches to either
# offline-mode (emits SQL to stdout for review) or online-mode (executes
# migrations against the live database).
#
# WHY asyncpg + AsyncEngine?
# Per CONSTRAINTS F12: the runtime stack is Python 3.12 + asyncio + asyncpg.
# Alembic 1.13+ ships first-class async support — env.py runs migrations
# inside `asyncio.run(...)` against an `AsyncEngine`. This keeps the dep
# tree thin (no psycopg2-binary needed just for migrations) per A2.1.
#
# WHY NO ORM METADATA?
# Per the directive verbatim: "no SQLAlchemy ORM — direct asyncpg + Pydantic."
# Migration files use raw `op.execute(...)` / `op.create_table(...)` with
# SQL; there's no declarative-base `metadata` to register here.
# `target_metadata = None`.
#
# WHY THE URL REWRITE (postgresql:// → postgresql+asyncpg://)?
# asyncpg uses `postgresql://` at runtime. Alembic's AsyncEngine needs the
# explicit driver suffix `postgresql+asyncpg://`. We rewrite at this boundary
# so the rest of the codebase keeps the simpler form and doesn't need to
# know about Alembic's driver-suffix convention.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config


# Alembic's context.config object (parsed from alembic.ini).
config = context.config


def _resolve_database_url() -> str:
    """Read POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE and adapt to async driver.

    WHAT: returns the SQLAlchemy URL string the AsyncEngine connects with.
    WHEN: called once at env.py module-load.
    WHY:  secrets never live in alembic.ini per D1+D8; reading at runtime
          fails fast if the var is missing rather than silently using a
          default localhost connection string.
    """
    # Read the per-service connection string from the environment.
    # Must be set before running `alembic upgrade head` (see RUNBOOK).
    raw = os.environ.get("POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE")

    if not raw:
        raise RuntimeError(
            "POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE env var is required to "
            "run Alembic migrations. Source it from secrets.yaml or set it "
            "in your shell before running `alembic upgrade head`.\n\n"
            "Local dev: copy from .env.example → .env.local and fill in the value.\n"
            "Cluster: export from the Swarm secret before running the one-off task."
        )

    # Rewrite `postgresql://` → `postgresql+asyncpg://` for Alembic's
    # AsyncEngine. The rest of the codebase (asyncpg at runtime) keeps
    # the plain `postgresql://` form.
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    # Already has a driver suffix (e.g. testcontainers returns asyncpg URL).
    return raw


# No ORM models — migrations use raw op.create_table / op.execute SQL.
# Alembic disables auto-generation of migrations when target_metadata is None.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to stdout, never execute.

    WHAT: configures Alembic with the connection string but no live
          connection; migrations are rendered to SQL strings for review.
    WHEN: invoked by `alembic upgrade head --sql` for review-before-deploy.
    WHY:  lets reviewers (Rishi, Codex) see exactly what SQL hits the DB
          before the migration actually runs in CI or production.
    """
    # Build the URL from the env var (same as online mode — just not
    # opening a live connection).
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations against an already-acquired connection.

    WHAT: called from inside the async block; configures the Alembic
          context with the live connection and executes pending migrations.
    WHEN: per-`alembic upgrade` / `alembic downgrade` invocation.
    WHY:  separated from run_async_migrations so the sync
          `context.configure` + `run_migrations` calls can be invoked
          via `connection.run_sync(...)` (Alembic's standard async pattern).
    """
    # Configure context with the live connection then run the
    # pending up/down migration scripts in order.
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against an AsyncEngine + asyncpg driver.

    WHAT: builds the AsyncEngine, opens a connection, hands it to
          `do_run_migrations` via `run_sync`, then disposes.
    WHEN: invoked by `run_migrations_online` when not in offline mode.
    WHY:  Alembic's `context.run_migrations` is sync internally; the
          `run_sync` bridge lets us pass an async-acquired connection
          into the sync migration logic without rewriting Alembic.
    """
    # Build the configuration dict Alembic expects, overriding the URL
    # with the one we resolved from the env (alembic.ini ships empty).
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolve_database_url()

    # NullPool — one connection per `alembic upgrade` invocation.
    # Not the asyncpg runtime pool — migrations are run as one-off tasks.
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # run_sync bridges Alembic's sync context.run_migrations into
        # the async connection we've opened.
        await connection.run_sync(do_run_migrations)

    # Dispose the engine so no background threads linger after migration.
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entrypoint Alembic invokes when running against a live DB.

    WHAT: kicks off `run_async_migrations` via `asyncio.run`.
    WHEN: per `alembic upgrade` / `alembic downgrade` invocation that
          isn't in `--sql` (offline) mode.
    WHY:  the standard Alembic async-online pattern; nothing exotic.
    """
    # asyncio.run spins an event loop just for this migration batch.
    asyncio.run(run_async_migrations())


# Dispatch: Alembic sets the offline/online flag before importing this
# file. Offline = `--sql` flag; online = normal interactive run.
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()


# ===========================================================================
# RELATED FILES:
#   ../../alembic.ini                     — points here via `script_location`
#   versions/001_initial_schema.py        — first migration this env dispatches to
#   ../database.py                        — runtime asyncpg pool (separate engine)
#   ../config.py                          — declares the connection-string setting
#   ../../tests/conftest.py               — sets env var for testcontainers
#   ../../tests/test_schema_migrations.py — round-trip gate uses _run_alembic helper
# ===========================================================================
