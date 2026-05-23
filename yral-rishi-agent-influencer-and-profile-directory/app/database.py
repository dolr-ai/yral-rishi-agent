# ---------------------------------------------------------------------------
# database.py — asyncpg connection pool wiring for the
# influencer-and-profile-directory service.
#
# ⭐ START HERE: this module exposes ONE async lifecycle pair —
# `init_pool()` + `close_pool()` — and ONE accessor — `get_pool()` — for
# everything in the codebase that needs to talk to Postgres. The
# repository layer (`app/repository/influencer_metadata_repository.py`)
# is the primary consumer; FastAPI's lifespan in `app/main.py` calls
# `init_pool()` at startup + `close_pool()` at shutdown.
#
# WHY asyncpg POOL + NOT SQLAlchemy ORM
# Per F12 + the v2 service-template directive: "no SQLAlchemy ORM —
# direct asyncpg + Pydantic models keeps the dep tree thin per A2.1."
# The pool gives us connection reuse + safe concurrency without the
# layered abstraction the ORM brings.
#
# WHY MIRROR THE SOUL-FILE-LIBRARY database.py SHAPE?
# Same v2-service template, same conventions. Diff is the env var name
# the connection string lives under (the per-service postgres secret).
# Keeping database.py structurally identical across services makes a
# future template-rot cleanup trivial.
#
# WHY MODULE-LEVEL SINGLETON
# `asyncpg.create_pool(...)` is async — must run inside an event loop.
# Storing the pool in a module-level variable populated by `init_pool()`
# (called from the FastAPI lifespan) gives every callsite a synchronous
# `get_pool()` accessor without each callsite needing its own awaitable
# initialiser.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import logging
from typing import Final

import asyncpg

from app.config import get_settings


# Module-level pool singleton — populated by `init_pool()` at app
# startup and consumed everywhere else via `get_pool()`. `None` before
# init + after close so any out-of-lifecycle access raises a clear
# error rather than silently using a stale handle.
_pool: asyncpg.Pool | None = None


# Min + max connection counts. Min held open even when idle so the
# read-heavy hot path doesn't pay TCP-connect latency on cold first
# call. Tuned the same as soul-file-library; revisit if the catalog
# read load profile diverges.
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
        RuntimeError when the per-service connection-string setting is
        empty — Pydantic-settings parsing already enforces this at
        config-load, so a missing value usually surfaces before this
        function runs.
    """
    global _pool

    if _pool is not None:
        # Already initialised — idempotent no-op. Helpful for tests
        # that spin the lifespan up + down multiple times.
        _log.debug("init_pool called but pool already initialised; skipping")
        return

    settings = get_settings()
    connection_string = settings.postgres_connection_string

    if not connection_string:
        raise RuntimeError(
            "POSTGRES_CONNECTION_STRING_INFLUENCER_AND_PROFILE_DIRECTORY "
            "is empty — set it in `.env.local` (sourced from your "
            "Keychain or local copy of secrets) before starting the "
            "influencer-and-profile-directory service."
        )

    # `dsn=` is asyncpg's parameter name (external API contract — kept
    # verbatim per B2's external-name carve-out); the IDENTIFIER we
    # pass is `connection_string` per the Codex PR-#104 round-4 B2
    # rename precedent.
    _pool = await asyncpg.create_pool(
        dsn=connection_string,
        min_size=_DEFAULT_MIN_POOL_SIZE,
        max_size=_DEFAULT_MAX_POOL_SIZE,
        # `statement_cache_size=0` is the safe default behind pgBouncer
        # transaction-pooling mode. asyncpg's default cache reuses
        # prepared statements across connections, which breaks under
        # transaction-mode multiplexing. The same caveat applies in the
        # v2 cluster's pgbouncer deployment.
        statement_cache_size=0,
    )
    _log.info(
        "asyncpg pool initialised",
        extra={
            "min_size": _DEFAULT_MIN_POOL_SIZE,
            "max_size": _DEFAULT_MAX_POOL_SIZE,
        },
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
            "asyncpg pool is not initialised — call `init_pool()` in "
            "the FastAPI lifespan startup hook before any request "
            "handler."
        )
    return _pool


# ===========================================================================
# RELATED FILES:
#   config.py                        — declares the
#                                        `postgres_connection_string` setting
#   main.py                          — calls `init_pool()` / `close_pool()`
#                                        in the FastAPI lifespan (Chunk B)
#   repository/influencer_metadata_repository.py
#                                    — consumes `get_pool()` for every read
#   migrations/env.py                — separate SQLAlchemy+asyncpg engine
#                                        used by Alembic ONLY; runtime
#                                        service path uses THIS pool
# ===========================================================================
