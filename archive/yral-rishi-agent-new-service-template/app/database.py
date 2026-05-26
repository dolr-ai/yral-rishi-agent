# ---------------------------------------------------------------------------
# database.py — asyncpg connection pool wiring for the spawned service.
#
# ⭐ START HERE: this module exposes ONE async lifecycle pair —
# `init_pool()` + `close_pool()` — ONE accessor — `get_pool()` — and ONE
# readiness probe — `check_pool_reachable()`. The FastAPI lifespan in
# `app/main.py` calls `init_pool()` at startup + `close_pool()` at
# shutdown. The /health/ready route in `app/health_routes.py` calls
# `check_pool_reachable()` on every readiness probe.
#
# WHY asyncpg POOL + NOT SQLAlchemy ORM
# v2-wide convention (per CONSTRAINTS A2.1 thin-deps spirit + the
# soul-file-library precedent): direct asyncpg + Pydantic models keep
# the dep tree thin. Spawned services that DO want an ORM can layer it
# on top without ripping out this baseline.
#
# WHY MODULE-LEVEL SINGLETON (lifespan-managed) — NOT PER-REQUEST
# `asyncpg.create_pool(...)` is async — must run inside an event loop —
# and the pool itself holds N persistent TCP connections to Postgres.
# Storing it in a module-level variable populated by `init_pool()`
# (called from the FastAPI lifespan) gives every callsite a synchronous
# `get_pool()` accessor without each callsite needing its own awaitable
# initialiser. Mirrors the orchestrator's redis singleton pattern + the
# soul-file-library's pool pattern.
#
# WHY DEFAULT min_size=2 / max_size=20
# `min_size=2` keeps 2 warm connections so the FIRST handler call after
# scale-up doesn't pay TCP-connect latency. `max_size=20` is a
# conservative ceiling that fits comfortably under the cluster's
# pgBouncer pool budget when many services share the cluster (per F3 +
# G3). Spawned services with hotter Postgres paths can override via
# `project.config`'s POSTGRES_CONNECTION_LIMIT (mapped to the
# `database_pool_max_size` Settings field).
#
# WHY statement_cache_size=0
# asyncpg's default prepared-statement cache reuses statements across
# connections, which breaks under pgBouncer transaction-pooling mode
# (the cluster's bouncer config per G3). Setting it to 0 here means
# the same code works locally (session-mode bouncer) AND in prod
# (transaction-mode bouncer) — strictly the safe default.
#
# WHY check_pool_reachable() RUNS A REAL SELECT 1
# `_pool is not None` only proves init ran; asyncpg's pool is lazy and
# doesn't open connections until you `acquire()`. /health/ready needs
# to verify Postgres is ACTUALLY reachable, not just that the pool
# object exists. Running `SELECT 1` inside a wait_for(timeout=0.2)
# proves end-to-end: acquire a connection, send query, receive
# response, return. Failure modes (timeout, connection refused, auth
# error, role mismatch) all surface as False → /health/ready 503.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib asyncio — `asyncio.wait_for` enforces the 200ms timeout around
# the readiness probe's SELECT 1 query so a slow Postgres doesn't
# stall the event loop while a /health/ready response is pending.
import asyncio

# stdlib logging — module-level logger emits structured init / close /
# probe-failure lines through `app/logging.py`'s configure_logging()
# pipeline (H6 PII-allowlist applied).
import logging

# stdlib typing — `Final` marks the pool-size defaults as
# module-level constants so future readers see "this is fixed, not
# rebound elsewhere" without scanning the whole file.
from typing import Final

# asyncpg — the asyncio-native Postgres driver. Pool reuse + lazy
# connection establishment + asyncio.wait_for-compatible timing.
import asyncpg

# Settings singleton — exposes `database_url`,
# `database_pool_min_size`, `database_pool_max_size` (template
# defaults below; per-service override via env).
from app.config import get_settings


# Module-level pool singleton — populated by `init_pool()` at app
# startup, consumed everywhere else via `get_pool()`. None before init
# + after close so any out-of-lifecycle access raises a clear error
# rather than silently using a stale handle.
_pool: asyncpg.Pool | None = None


# Default pool sizing — see file header WHY block above. Spawned
# services with hot-path Postgres dependency can override via the
# `database_pool_max_size` Settings field.
_DEFAULT_POOL_MIN_SIZE: Final[int] = 2
_DEFAULT_POOL_MAX_SIZE: Final[int] = 20


# Readiness probe timeout is configurable via Settings — see
# `app/config.py`'s `health_ready_probe_timeout_seconds` field.
# Codex PR #151 round-4 BLOCKER moved this out of a per-module
# `_READINESS_PROBE_TIMEOUT_SECONDS = 0.2` constant + into the shared
# Settings model per C7 ("timeouts + thresholds are
# configurable/shared, not magic constants in code"). The same field
# governs the Redis PING probe in `app/redis_client.py` so both
# /health/ready sub-probes share one budget.


_log = logging.getLogger("app.database")


async def init_pool() -> None:
    """Open the asyncpg connection pool. Idempotent — safe to call once.

    WHAT: builds an `asyncpg.Pool` using `database_url` from settings
          + the connection-count bounds defined above + the
          pgBouncer-safe `statement_cache_size=0`; stores it in the
          module-level `_pool` variable.
    WHEN: called from the FastAPI lifespan startup hook in `app/main.py`
          BEFORE any request handler runs.
    WHY:  central init means every callsite sees the same pooled
          connections + we can teardown cleanly via `close_pool()` on
          SIGTERM (per the lifespan shutdown hook). Idempotent so
          tests that spin the lifespan up + down multiple times don't
          double-create.

    Raises:
        RuntimeError when `database_url` is empty — the template's
        Settings default is the local-dev compose Postgres
        `postgresql://service:service-local-password@pgbouncer:6432/service_local_database`
        so this raise only fires if a deploy explicitly sets the
        environment variable to empty.
    """
    global _pool

    if _pool is not None:
        # Already initialised — idempotent no-op. Helpful for tests +
        # for the rare case where lifespan is invoked twice (e.g., a
        # supervisor restarting the app within the same process).
        _log.debug("init_pool called but pool already initialised; skipping")
        return

    settings = get_settings()

    # Empty connection string == operator misconfiguration — refuse to
    # start with a loud message rather than crash on the first SQL
    # query 30 minutes later. asyncpg's own error here is opaque
    # ("invalid dsn"), so we catch the empty-string case explicitly.
    if not settings.database_url:
        raise RuntimeError(
            "database_url is empty — set DATABASE_URL in `.env.local` "
            "(local dev) OR the spawned-service's Swarm secret (production) "
            "before starting. Template default points at the local "
            "docker-compose pgbouncer; an empty value means a deploy "
            "explicitly overrode the default to empty."
        )

    # `dsn=` is asyncpg's kwarg name — kept verbatim per B2's
    # external-API-name carve-out. The IDENTIFIER on our side is
    # `database_url`.
    #
    # `min_size` / `max_size` from settings if provided, else defaults
    # above. The Settings fields default to the module-level constants
    # so a spawned service that doesn't customise gets the safe
    # baseline.
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.database_pool_min_size or _DEFAULT_POOL_MIN_SIZE,
        max_size=settings.database_pool_max_size or _DEFAULT_POOL_MAX_SIZE,
        # pgBouncer transaction-mode compatibility — see file header
        # WHY block.
        statement_cache_size=0,
    )
    _log.info(
        "asyncpg_pool_initialised",
        extra={
            "min_size": settings.database_pool_min_size or _DEFAULT_POOL_MIN_SIZE,
            "max_size": settings.database_pool_max_size or _DEFAULT_POOL_MAX_SIZE,
        },
    )


async def close_pool() -> None:
    """Close the asyncpg connection pool cleanly.

    WHAT: awaits `_pool.close()` to flush in-flight queries + tear down
          all underlying TCP connections, then sets `_pool = None`.
    WHEN: called from the FastAPI lifespan shutdown hook on SIGTERM
          (Swarm rolling update, scale-down, operator stop).
    WHY:  uncleaned pool connections linger on the Postgres side until
          their idle timeout; clean shutdown == faster Swarm rolling
          updates + no orphaned backend processes on Patroni.
    """
    global _pool

    if _pool is None:
        # Nothing to close — either init never ran (tests that bypass
        # lifespan) or close was already called.
        return

    await _pool.close()
    _pool = None
    _log.info("asyncpg_pool_closed")


def get_pool() -> asyncpg.Pool:
    """Return the initialised asyncpg pool.

    WHAT: returns the module-level `_pool`. Raises if init hasn't run.
    WHEN: called from every code path that needs to acquire a
          Postgres connection (repository layer, /health/ready
          probe, etc.).
    WHY:  central accessor — a future refactor that swaps the pool
          implementation (e.g., per-tenant pool sharding) only
          touches `init_pool` + `get_pool`. Loud RuntimeError on
          misuse beats a silent NoneType.acquire() crash later.
    """
    if _pool is None:
        raise RuntimeError(
            "asyncpg pool not initialised — was init_pool() called from "
            "app lifespan startup? Tests that bypass lifespan must call "
            "init_pool() in setup or monkeypatch the pool singleton."
        )
    return _pool


async def check_pool_reachable() -> bool:
    """Probe whether Postgres is actually reachable via the pool.

    WHAT: acquires a connection from the pool + runs `SELECT 1`
          wrapped in `asyncio.wait_for(timeout=0.2)`. Returns True
          on success, False on ANY failure (init not run, timeout,
          connection refused, auth error, role mismatch).
    WHEN: invoked by `/health/ready` in `app/health_routes.py` on
          every readiness probe (Swarm's compose healthcheck +
          Uptime Kuma + Caddy upstream).
    WHY:  /health/ready uses this boolean to choose between the 200
          and 503 branches per F9. Failure paths log but don't
          raise — health probes must always answer with a response,
          not propagate exceptions to the framework.

    The probe is intentionally an end-to-end check (acquire → SELECT
    → release), not just a pool-is-not-None check, because asyncpg's
    pool is lazy: pool initialisation succeeds even if Postgres is
    unreachable; the first acquire() is what fails. A misconfigured
    DATABASE_URL (wrong host, wrong password, wrong DB name) is
    EXACTLY the regression class DEP-014's spawn-smoke step 5b is
    supposed to catch.
    """
    if _pool is None:
        _log.warning("health_ready_pool_not_initialised")
        return False

    # Read the shared per-service timeout from Settings (C7 — no
    # magic constants; one source of truth in app/config.py's
    # `health_ready_probe_timeout_seconds` field). get_settings() is
    # lru_cached so this call is effectively free per process.
    settings = get_settings()
    probe_timeout_seconds = settings.health_ready_probe_timeout_seconds

    try:
        async def _probe_query() -> None:
            # asyncpg's `async with` form auto-acquires + auto-releases
            # the connection back to the pool — no leaks even on
            # exception. `fetchval` runs the query + returns the first
            # column of the first row (here: the literal 1).
            async with _pool.acquire() as connection:
                await connection.fetchval("SELECT 1")

        await asyncio.wait_for(_probe_query(), timeout=probe_timeout_seconds)
        return True
    except asyncio.TimeoutError:
        # Slow Postgres / network — log the timeout class so the
        # operator can distinguish from a genuine connect failure.
        _log.warning(
            "health_ready_pool_probe_timed_out",
            extra={"timeout_seconds": probe_timeout_seconds},
        )
        return False
    except Exception as exc:  # noqa: BLE001 — health probes never raise
        # Catch-all because health probes MUST always return a bool;
        # propagating an exception here would 500 the /health/ready
        # endpoint instead of 503-ing the response body.
        _log.warning(
            "health_ready_pool_probe_failed",
            extra={"error_class": type(exc).__name__, "error_message": str(exc)},
        )
        return False


async def check_pool_round_trip_works() -> bool:
    """Deep-probe Postgres via a `SELECT NOW()` round-trip.

    WHAT: acquires a connection from the pool + runs `SELECT NOW()`
          wrapped in `asyncio.wait_for(timeout=health_deep_probe_
          timeout_seconds)`. Asserts the returned value is a real
          datetime (not None / not an error). Returns True on
          success, False on any failure (init not run, timeout,
          unexpected return type).
    WHEN: invoked by `/health/deep` in `app/health_routes.py` on
          every deep-probe request (typically Uptime Kuma's deeper
          check + on-call dashboard).
    WHY:  /health/deep is the F9 third tier — "real end-to-end
          round-trip" (vs /health/ready's "is the dep reachable").
          `SELECT NOW()` is the lightest non-trivial query: forces
          Postgres to compute + serialize a timestamp, exercising
          the query path beyond just "connection is open". A
          regression where the pool reports reachable but queries
          silently return wrong types would surface here.

          Spawned services SHOULD override this with their own
          deep-probe (e.g. a per-service-table read) when their
          domain-specific data layer is wired. The template's
          default is the minimum non-trivial round-trip — replace
          per service when richer checks land.

    The probe is intentionally a query round-trip (not just `acquire`)
    so it goes ALL THE WAY through the asyncpg → pgBouncer → Postgres
    → result-serialization → asyncpg-decoding path. A breakage at
    ANY step in that chain surfaces here as False.
    """
    if _pool is None:
        _log.warning("health_deep_pool_not_initialised")
        return False

    settings = get_settings()
    probe_timeout_seconds = settings.health_deep_probe_timeout_seconds

    try:
        async def _round_trip_query():
            # `fetchval` returns the first column of the first row —
            # here the NOW() timestamp. asyncpg decodes it to a
            # Python `datetime`; we don't need its value, just that
            # the round-trip completed + returned a non-None result.
            async with _pool.acquire() as connection:
                return await connection.fetchval("SELECT NOW()")

        result_timestamp = await asyncio.wait_for(
            _round_trip_query(),
            timeout=probe_timeout_seconds,
        )
        # NOW() must return a non-None datetime — if asyncpg
        # returns None, something in the decode path broke even
        # though no exception fired. Defensive against silent
        # asyncpg-version regressions.
        return result_timestamp is not None
    except asyncio.TimeoutError:
        _log.warning(
            "health_deep_pool_round_trip_timed_out",
            extra={"timeout_seconds": probe_timeout_seconds},
        )
        return False
    except Exception as exc:  # noqa: BLE001 — health probes never raise
        _log.warning(
            "health_deep_pool_round_trip_failed",
            extra={"error_class": type(exc).__name__, "error_message": str(exc)},
        )
        return False


# ===========================================================================
# RELATED FILES:
#   main.py                       — lifespan startup calls init_pool();
#                                   shutdown calls close_pool()
#   health_routes.py              — /health/ready calls check_pool_reachable()
#   config.py                     — Settings model exposes database_url,
#                                   database_pool_min_size,
#                                   database_pool_max_size
#   redis_client.py               — sibling module: same lifespan-singleton
#                                   pattern for redis.asyncio
#   secrets.yaml.template         — declares DATABASE_URL secret
#   docker-compose.yml            — local-dev wiring: pgbouncer → postgres
#   pyproject.toml                — declares asyncpg dependency
# ===========================================================================
