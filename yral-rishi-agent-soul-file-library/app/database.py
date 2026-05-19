# ---------------------------------------------------------------------------
# database.py — asyncpg connection pool wiring for the soul-file-library.
#
# ⭐ START HERE: this module exposes ONE async lifecycle pair —
# `init_pool()` + `close_pool()` — and ONE accessor — `get_pool()` — for
# everything in the codebase that needs to talk to Postgres. The
# repository layer (`app/repository/soul_file_repository.py`) is the
# primary consumer; FastAPI's lifespan in `app/main.py` calls
# `init_pool()` at startup + `close_pool()` at shutdown.
#
# WHY asyncpg POOL + NOT SQLAlchemy ORM
# Per the Day-4 directive verbatim: "Python 3.12 + FastAPI + asyncpg (no
# SQLAlchemy ORM — direct asyncpg + Pydantic models keeps the dep tree
# thin per A2.1)." The pool gives us connection reuse + safe concurrency
# without the layered abstraction the ORM brings.
#
# WHY MODULE-LEVEL SINGLETON
# `asyncpg.create_pool(...)` is async — must run inside an event loop.
# Storing the pool in a module-level variable populated by `init_pool()`
# (called from the FastAPI lifespan) gives every callsite a synchronous
# `get_pool()` accessor without each callsite needing its own awaitable
# initialiser.
#
# WHY connection limit FROM project.config (NOT shared-config)
# Per C7 — per-service values live in project.config (read here via the
# `Settings` model). Day-4 keeps the cap at `POSTGRES_CONNECTION_LIMIT`
# from project.config (default 15); raise via env override only when the
# composer's hot-path load actually demands it.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import logging
from typing import Final

import asyncpg

from app.config import get_settings


# Module-level pool singleton — populated by `init_pool()` at app startup
# and consumed everywhere else via `get_pool()`. `None` before init +
# after close so any out-of-lifecycle access raises a clear error rather
# than silently using a stale handle.
_pool: asyncpg.Pool | None = None


# Min + max connection counts. Min held open even when idle so the
# composer's hot path doesn't pay TCP-connect latency on cold first call.
_DEFAULT_MIN_POOL_SIZE: Final[int] = 2
_DEFAULT_MAX_POOL_SIZE: Final[int] = 10


_log = logging.getLogger("app.database")


async def init_pool() -> None:
    """Open the asyncpg connection pool. Idempotent — safe to call once.

    WHAT: builds an `asyncpg.Pool` using the DSN from settings + the
          connection-count bounds defined above; stores it in the
          module-level `_pool` variable.
    WHEN: called from the FastAPI lifespan startup hook in `app/main.py`
          BEFORE any request handler runs.
    WHY:  centralised init means every callsite sees the same pool +
          we can teardown cleanly via `close_pool()` on SIGTERM (per
          the lifespan shutdown hook).

    Raises:
        RuntimeError when `POSTGRES_DSN_SOUL_FILE_LIBRARY` is missing —
        Pydantic-settings parsing already enforces this at config-load,
        so a missing value usually surfaces before this function runs.
    """
    global _pool

    if _pool is not None:
        # Already initialised — idempotent no-op. Helpful for tests that
        # spin the lifespan up + down multiple times.
        _log.debug("init_pool called but pool already initialised; skipping")
        return

    settings = get_settings()
    dsn = settings.postgres_dsn

    if not dsn:
        raise RuntimeError(
            "POSTGRES_DSN_SOUL_FILE_LIBRARY is empty — set it in "
            "`.env.local` (sourced from your Keychain or local copy of "
            "secrets) before starting the soul-file-library service."
        )

    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=_DEFAULT_MIN_POOL_SIZE,
        max_size=_DEFAULT_MAX_POOL_SIZE,
        # `statement_cache_size=0` is the safe default behind pgBouncer
        # transaction-pooling mode. asyncpg's default cache reuses
        # prepared statements across connections, which breaks under
        # transaction-mode multiplexing. Day-4 dev uses session-mode
        # bouncer locally so this isn't strictly required today, but
        # setting it now means the same code works in prod where the
        # cluster bouncer runs transaction-mode.
        statement_cache_size=0,
    )
    _log.info(
        "asyncpg pool initialised",
        extra={"min_size": _DEFAULT_MIN_POOL_SIZE, "max_size": _DEFAULT_MAX_POOL_SIZE},
    )


async def close_pool() -> None:
    """Close the asyncpg connection pool cleanly.

    WHAT: awaits `_pool.close()` to flush any in-flight queries + close
          all the underlying TCP connections, then sets `_pool = None`.
    WHEN: called from the FastAPI lifespan shutdown hook on SIGTERM
          (Swarm rolling update, scale-down, manual stop).
    WHY:  un-closed pools can leave Postgres backend processes alive
          for the duration of `idle_in_transaction_session_timeout`.
          Cleaner shutdown == faster Swarm rolling updates.
    """
    global _pool

    if _pool is None:
        return

    await _pool.close()
    _pool = None
    _log.info("asyncpg pool closed")


def get_pool() -> asyncpg.Pool:
    """Return the initialised asyncpg pool.

    WHAT: returns the module-level `_pool`. Raises if init hasn't run.
    WHEN: called from the repository layer + anywhere else that needs
          a connection (e.g. health-deep endpoint when that lands).
    WHY:  central accessor lets a future refactor swap the
          implementation (e.g. add a wrapper for query-timing
          instrumentation) without touching every callsite.
    """
    if _pool is None:
        raise RuntimeError(
            "asyncpg pool is not initialised — call `init_pool()` in the "
            "FastAPI lifespan startup hook before any request handler."
        )
    return _pool


# ===========================================================================
# RELATED FILES:
#   config.py                        — declares `postgres_dsn` setting
#   main.py                          — calls init_pool() / close_pool() in
#                                       the FastAPI lifespan
#   repository/soul_file_repository.py
#                                    — consumes get_pool() for every read
#   migrations/env.py                — separate SQLAlchemy+asyncpg engine
#                                       used by Alembic ONLY; runtime
#                                       service path uses THIS pool
#   docker-compose.yml               — local Postgres + pgbouncer the pool
#                                       connects to during dev / pytest
# ===========================================================================
