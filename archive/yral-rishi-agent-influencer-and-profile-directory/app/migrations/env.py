# ---------------------------------------------------------------------------
# env.py — Alembic environment script. Invoked by `alembic upgrade` /
# `alembic downgrade` before any migration runs.
#
# ⭐ START HERE: this file builds the SQLAlchemy AsyncEngine from the
# `POSTGRES_CONNECTION_STRING_INFLUENCER_AND_PROFILE_DIRECTORY` env var
# (the per-service secret per D8) and dispatches to either offline-mode
# (emits SQL to stdout — useful for review) or online-mode (executes
# migrations against the live DB).
#
# WHY asyncpg + AsyncEngine?
# Per F12 the runtime stack is Python 3.12 + asyncio + asyncpg. Alembic
# 1.13+ ships first-class async support — env.py runs the online
# migrations inside an `asyncio.run(...)` block against an `AsyncEngine`,
# so we don't need to add `psycopg2-binary` (a sync-only driver) just for
# migrations. Keeps the dep tree thin per A2.1.
#
# WHY NO ORM METADATA?
# Per the directive "no SQLAlchemy ORM — direct asyncpg + Pydantic".
# Migration files use raw `op.execute(...)` / `op.create_table(...)`
# calls; there's no declarative-base `metadata` to register here.
# `target_metadata = None`.
#
# WHY MIRROR THE SOUL-FILE-LIBRARY env.py SHAPE?
# Same v2-service template, same conventions. Diff is the env var name +
# the migration-tree contents. Keeping the env.py structurally identical
# makes a future template-rot cleanup (e.g. extracting the env.py into
# the new-service-template) trivial.
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


# Environment variable name the per-service connection string lives
# under. Declared in `secrets.yaml`, mounted as `/run/secrets/...` in
# production + bridged to env via the docker-compose secret-bridge
# wrapper. Local dev uses `.env.local`. The CONSTRUCTION of the
# environment-variable name is a literal constant — no string-templating
# from the service name — so a grep for this exact string finds every
# consumer.
#
# Identifier renamed from `_DATABASE_CONNECTION_STRING_ENV_VAR` per Codex
# PR #142 round-1 B2 BLOCKER (`env` + `var` not on the B2 allowlist).
# Same Session-4-coined identifier, no cross-service precedent, so the
# rename to the fully-spelled-out form is accepted in round-2.
_DATABASE_CONNECTION_STRING_ENVIRONMENT_VARIABLE: str = (
    "POSTGRES_CONNECTION_STRING_INFLUENCER_AND_PROFILE_DIRECTORY"
)


def _resolve_database_url() -> str:
    """Read the connection-string environment variable + adapt to the async driver.

    WHAT: returns the SQLAlchemy URL string the AsyncEngine connects with.
    WHEN: called once at env.py module-load.
    WHY:  secrets never live in alembic.ini per D1+D8; reading at runtime
          fails fast if the variable is missing rather than silently
          using a default localhost connection string.
    """
    # Identifier renamed from `raw` per Codex PR #142 round-1 B2
    # CONCERN — `raw` is technically a full English word but
    # `raw_database_connection_string` is more descriptive in context
    # (also a B7 clarity win). Same Session-4-coined identifier, no
    # cross-service precedent, so the rename is accepted in round-2.
    raw_database_connection_string = os.environ.get(
        _DATABASE_CONNECTION_STRING_ENVIRONMENT_VARIABLE
    )
    if not raw_database_connection_string:
        raise RuntimeError(
            f"{_DATABASE_CONNECTION_STRING_ENVIRONMENT_VARIABLE} "
            "environment variable is required to run Alembic migrations. "
            "Source it from secrets.yaml or set it in your shell before "
            "running `alembic upgrade head`."
        )

    # The connection string as committed in `.env.example` uses the
    # `postgresql://` scheme so it works with both psycopg2 (sync) and
    # asyncpg (async) consumers without modification. Alembic's
    # AsyncEngine needs the explicit driver suffix
    # `postgresql+asyncpg://` — we rewrite at this boundary so the rest
    # of the codebase keeps the simpler form.
    if raw_database_connection_string.startswith("postgresql://"):
        return raw_database_connection_string.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
    return raw_database_connection_string


# No ORM models in this service (per directive — "no SQLAlchemy ORM").
# Alembic uses `target_metadata = None` to disable auto-generation of
# migrations from model diffs; we author every migration by hand using
# `op.execute(...)` / `op.create_table(...)` with raw SQL.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL to stdout, never execute.

    WHAT: configures Alembic with the connection string but no live
          connection; migrations get rendered to SQL strings instead
          of running.
    WHEN: invoked by `alembic upgrade head --sql` for review-before-deploy.
    WHY:  lets reviewers (Rishi, Codex) see exactly what SQL hits the
          DB before the migration actually runs in CI.
    """
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

    WHAT: called from inside the async block by `run_async_migrations`;
          configures the Alembic context with the live connection and
          executes whichever migration scripts are pending.
    WHEN: per-`alembic upgrade` / `alembic downgrade` invocation.
    WHY:  separated out so the sync `context.configure` + `run_migrations`
          calls can be invoked from inside `connection.run_sync(...)`
          (Alembic's standard async pattern).
    """
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
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolve_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entrypoint Alembic invokes when running migrations against a live DB.

    WHAT: kicks off `run_async_migrations` via `asyncio.run`.
    WHEN: per `alembic upgrade` / `alembic downgrade` invocation that
          isn't in `--sql` (offline) mode.
    WHY:  the standard Alembic async-online pattern; nothing exotic.
    """
    asyncio.run(run_async_migrations())


# Dispatch on the offline/online flag set by Alembic before importing
# this file. Offline mode is the `--sql` flavor; online is normal.
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()


# ===========================================================================
# RELATED FILES:
#   ../../alembic.ini      — points here via `script_location`
#   versions/              — per-revision migration files this env
#                              dispatches to
#   ../database.py         — runtime asyncpg pool (separate from
#                              Alembic's SQLAlchemy engine; the two talk
#                              to the same DB but via different drivers
#                              — asyncpg vs SQLAlchemy+asyncpg)
#   ../config.py           — declares the `postgres_connection_string`
#                              setting that the runtime pool reads
# ===========================================================================
