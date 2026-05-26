# ---------------------------------------------------------------------------
# database.py — asyncpg connection pool wiring for the user-memory-service.
#
# ⭐ START HERE: this module exposes ONE async lifecycle pair —
# `init_pool()` + `close_pool()` — and ONE accessor — `get_pool()` — for
# everything in the codebase that needs to talk to Postgres. The
# repository layer (`app/repository/conversation_repository.py`, added in
# Deliverable 2) is the primary consumer; FastAPI's lifespan in
# `app/main.py` calls `init_pool()` at startup + `close_pool()` at shutdown.
#
# WHY asyncpg POOL + NOT SQLAlchemy ORM?
# Per CONSTRAINTS F12 / directive verbatim: "no SQLAlchemy ORM — direct
# asyncpg + Pydantic keeps the dep tree thin per A2.1." The pool gives
# connection reuse + safe concurrency without the ORM abstraction.
#
# WHY MODULE-LEVEL SINGLETON?
# `asyncpg.create_pool(...)` is async — it must run inside an event loop.
# Storing the pool in a module-level variable populated by `init_pool()`
# (called from FastAPI's lifespan) gives every callsite a synchronous
# `get_pool()` accessor without each callsite needing its own awaitable.
#
# WHY statement_cache_size=0?
# pgBouncer in transaction-mode (which the cluster's shared bouncer runs)
# multiplexes server connections across client connections. asyncpg's
# default prepared-statement cache reuses statements across connections,
# which breaks under transaction-mode multiplexing. Setting the cache to 0
# disables that behaviour so the same code works in both local
# (session-mode bouncer) and production (transaction-mode bouncer).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import logging
from typing import Final

import asyncpg

from app.config import get_settings


# Module-level pool singleton — populated by `init_pool()` at app startup
# and consumed everywhere else via `get_pool()`. `None` before init +
# after close so any out-of-lifecycle access raises a clear error.
_pool: asyncpg.Pool | None = None


# Min + max connection counts. Min held open even when idle so the
# hot-path conversation + message reads don't pay TCP-connect latency.
_DEFAULT_MIN_POOL_SIZE: Final[int] = 2
_DEFAULT_MAX_POOL_SIZE: Final[int] = 10


_log = logging.getLogger("app.database")


async def init_pool() -> None:
    """Open the asyncpg connection pool. Idempotent — safe to call once.

    WHAT: builds an `asyncpg.Pool` using the connection string from
          settings + the connection-count bounds defined above; stores
          it in the module-level `_pool` variable.
    WHEN: called from the FastAPI lifespan startup hook in `app/main.py`
          BEFORE any request handler runs.
    WHY:  centralised init means every callsite sees the same pool +
          we can teardown cleanly via `close_pool()` on SIGTERM (per
          the lifespan shutdown hook).

    Raises:
        RuntimeError when the connection string is missing — pydantic-
        settings parsing already enforces this at config-load so a
        missing value usually surfaces before this function runs.
    """
    global _pool

    # Already initialised — idempotent no-op. Helpful for tests that
    # spin the lifespan up + down multiple times.
    if _pool is not None:
        _log.debug("init_pool called but pool already initialised; skipping")
        return

    # Read the connection string from the typed Settings singleton so
    # the source of truth is config.py, not scattered os.environ calls.
    settings = get_settings()
    connection_string = settings.postgres_connection_string

    if not connection_string:
        raise RuntimeError(
            "POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE is empty — "
            "set it in `.env.local` (sourced from your Keychain or local "
            "copy of secrets) before starting the user-memory-service."
        )

    # Open the pool. `dsn=` is asyncpg's kwarg name (external API
    # contract — kept verbatim per B2's external-name carve-out).
    _pool = await asyncpg.create_pool(
        dsn=connection_string,
        min_size=_DEFAULT_MIN_POOL_SIZE,
        max_size=_DEFAULT_MAX_POOL_SIZE,
        # Disable prepared-statement cache — required for pgBouncer
        # transaction-mode (cluster default). See file header rationale.
        statement_cache_size=0,
    )
    _log.info(
        "asyncpg pool initialised",
        extra={"min_size": _DEFAULT_MIN_POOL_SIZE, "max_size": _DEFAULT_MAX_POOL_SIZE},
    )


async def close_pool() -> None:
    """Close the asyncpg connection pool cleanly.

    WHAT: awaits `_pool.close()` to flush any in-flight queries + close
          all underlying TCP connections, then sets `_pool = None`.
    WHEN: called from the FastAPI lifespan shutdown hook on SIGTERM
          (Swarm rolling update, scale-down, manual stop).
    WHY:  un-closed pools can leave Postgres backend processes alive for
          the duration of `idle_in_transaction_session_timeout`. Clean
          shutdown == faster Swarm rolling updates.
    """
    global _pool

    # Nothing to close — safe no-op (e.g. shutdown before init completes).
    if _pool is None:
        return

    # Flush in-flight queries + close all TCP connections.
    await _pool.close()
    _pool = None
    _log.info("asyncpg pool closed")


def get_pool() -> asyncpg.Pool:
    """Return the initialised asyncpg pool.

    WHAT: returns the module-level `_pool`. Raises if init hasn't run.
    WHEN: called from the repository layer + health-deep endpoint when
          it lands.
    WHY:  central accessor lets a future refactor swap the implementation
          (e.g. add a wrapper for query-timing instrumentation) without
          touching every callsite.
    """
    # Raise clearly if called before init — a crash here means the
    # caller forgot to wire the lifespan or is calling outside request
    # context. Better than a NoneType AttributeError deeper in the stack.
    if _pool is None:
        raise RuntimeError(
            "asyncpg pool is not initialised — call `init_pool()` in the "
            "FastAPI lifespan startup hook before any request handler runs."
        )
    return _pool


# ===========================================================================
# RELATED FILES:
#   config.py                      — declares postgres_connection_string setting
#   main.py                        — calls init_pool() / close_pool() in lifespan
#   repository/conversation_repository.py
#                                  — primary consumer of get_pool() (Deliverable 2)
#   migrations/env.py              — separate SQLAlchemy+asyncpg engine used by
#                                    Alembic ONLY; runtime service path uses THIS pool
#   docker-compose.yml             — local Postgres + pgbouncer the pool connects to
#   tests/conftest.py              — test fixture opens its own pool to the
#                                    testcontainers-postgres instance
# ===========================================================================
