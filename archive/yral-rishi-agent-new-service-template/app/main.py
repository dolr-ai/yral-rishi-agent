# ---------------------------------------------------------------------------
# main.py — the FastAPI app entry point. uvicorn loads `app.main:app`.
#
# ⭐ START HERE: top-of-module calls `init_sentry()` FIRST (must run before
# the FastAPI object exists so the integration hooks in). Then we build a
# minimal `app` with a no-op lifespan. Health endpoints, real routes, and
# the rest of the middleware land in subsequent Day-2 PRs — this PR ships
# the skeleton + Sentry only.
#
# WHAT IS THE LIFESPAN HOOK?
# A FastAPI feature that runs code at startup and shutdown. Code before
# `yield` runs once when the first request arrives; code after `yield`
# runs once on SIGTERM. We use it for things like opening database pools
# (Day-2 PR 5) and closing them cleanly. Today it's a no-op placeholder
# so future PRs can fill it in without touching this file's structure.
#
# WHY ONE FastAPI INSTANCE, NOT A FACTORY?
# `uvicorn app.main:app` expects a module-level `app` variable, not a
# factory function. Keeping it module-level (not wrapped in get_app())
# matches the rest of the FastAPI ecosystem and avoids the "is this a
# function or an instance?" papercut.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager

# stdlib logging — module-level logger used by the shutdown
# try/except chain to surface dep-close failures without aborting
# the rest of the shutdown sequence (PR #151 round-8 CONCERN 1).
import logging

from fastapi import FastAPI

# Sentry MUST init before the FastAPI object exists. See the file header
# rationale above and sentry_middleware.py's own header. This is the one
# import side-effect the template tolerates by design.
from app.sentry_middleware import init_sentry

# Langfuse follows the same module-load init pattern as Sentry for
# uniformity. The `flush_langfuse` function is called from the
# lifespan shutdown (below) so SIGTERM doesn't drop in-flight LLM
# traces.
from app.langfuse_middleware import flush_langfuse, init_langfuse

# Structured logging with PII allowlist redaction per H6. Configured
# at module-load so any startup log lines emit through the same
# pipeline as request logs.
from app.logging import configure_logging

# Per-request correlation ID middleware. Mounted on the FastAPI app
# below so it runs OUTERMOST in the request chain.
from app.request_id_middleware import RequestIdMiddleware

# DEP-014 — asyncpg pool lifespan-singleton. `init_pool()` opens the
# pool at lifespan startup; `close_pool()` flushes + closes on
# SIGTERM. The pool is accessed everywhere else via `get_pool()`;
# /health/ready probes it via `check_pool_reachable()` (see
# app/health_routes.py).
from app.database import close_pool, init_pool

# DEP-014 — async Redis lifespan-singleton with C11-aware dual-path
# (Sentinel-aware for production / single-primary for local-dev).
# `init_redis()` opens at startup AFTER the production fail-closed
# gate fires; `close_redis()` flushes on SIGTERM.
# `verify_deployed_environment_sentinel_or_die()` is the C11 safety gate —
# refuses to boot a production deploy with `redis_sentinel_enabled=
# False` (Codex PR #97 round-5 ITEM 6 + Session 4's PR #96 round-4
# pattern).
from app.redis_client import (
    close_redis,
    init_redis,
    verify_deployed_environment_sentinel_or_die,
)

# DEP-014 — /health/{live,ready} probes per F9. /health/live is a
# cheap raw 200; /health/ready dual-probes asyncpg + redis in
# parallel and returns 200 only when both are reachable. The spawn-
# smoke gate (test_spawn_smoke.sh step 5b) is the load-bearing CI
# check on this probe.
from app.health_routes import health_router


# Run Sentry init now, at module import time. After this line, every
# unhandled exception below is shipped to sentry.rishi.yral.com (per A7).
init_sentry()

# Init Langfuse the same way. No-ops when LANGFUSE_TRACING_ENABLED is
# false (the default in docker-compose.yml for local dev).
init_langfuse()

# Configure structlog + stdlib logging. Order: AFTER Sentry/Langfuse
# init (so their startup messages can land through this pipeline) but
# BEFORE app creation so any startup log line is structured.
configure_logging()


# Module-level logger for the shutdown try/except chain (PR #151
# round-8 CONCERN 1). Created AFTER configure_logging() so its emit
# path lands through the H6-aware structured pipeline rather than
# stdlib's default formatter.
_log = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup + shutdown hooks for the FastAPI app.

    WHAT: code before `yield` runs at startup; code after runs on
          SIGTERM. Startup opens the asyncpg pool + the async Redis
          client (after the C11 production-fail-closed gate fires).
          Shutdown closes both in reverse order then drains pending
          Langfuse traces.
    WHEN: invoked exactly once per process lifetime by FastAPI itself.
    WHY:  lifespan-singleton dep clients (per orchestrator's PR #136
          pattern) mean every request handler sees the same pooled
          connections + we tear down cleanly on SIGTERM (faster Swarm
          rolling updates + no orphaned backend processes).
    """
    # --- startup --------------------------------------------------------
    # C11 fail-closed gate FIRST — refuse to boot a deployed service
    # (production OR staging — both share the HA Redis Sentinel
    # infrastructure on rishi-4/5/6 per F4 + C11) that would silently
    # fall back to single-primary Redis. The gate's input check is
    # `environment in {"production", "staging"}` (broadened from
    # production-only in PR #151 round-6 BLOCKER 1). Local-dev + any
    # non-deployed env skip the gate. If this raises RuntimeError,
    # neither init_pool nor init_redis has run yet — nothing to clean
    # up.
    verify_deployed_environment_sentinel_or_die()
    # Open the asyncpg pool. min_size=2 means we hold 2 warm
    # connections so the first request after scale-up doesn't pay
    # TCP-connect latency to pgBouncer. If init_pool raises, the
    # asyncpg.create_pool call left `_pool = None`; nothing to clean.
    await init_pool()
    # Open the async Redis client. The Sentinel-aware path is taken
    # when `redis_sentinel_enabled=True`; the single-primary fallback
    # otherwise. PR #136's `password=settings.redis_password or None`
    # AUTH wiring is inside init_redis.
    #
    # WHY THE try/except (Codex PR #151 round-2 CONCERN 1):
    # If init_redis raises AFTER init_pool succeeded, the asyncpg
    # pool stays open + the yield never runs + the shutdown block
    # below never runs — leaking the open pool connections to
    # pgBouncer/Postgres across tests, supervisor reloads, and
    # failed-startup-loop deploy retries. Standard FastAPI lifespan
    # pattern: catch any startup exception that happens AFTER an
    # earlier resource is open, close the earlier resource, then
    # re-raise so the caller (uvicorn) still aborts startup.
    try:
        await init_redis()
    except Exception:
        # Close the asyncpg pool that init_pool just opened. Don't
        # swallow init_redis's exception — let it propagate so
        # uvicorn fails startup loudly with the original error.
        await close_pool()
        raise
    yield
    # --- shutdown -------------------------------------------------------
    #
    # WHY EACH SHUTDOWN STEP HAS ITS OWN try/except (Codex PR #151
    # round-7 CONCERN 1):
    # This is the TEMPLATE — every spawned v2 service inherits this
    # lifespan pattern. A naked `await close_redis(); await
    # close_pool(); flush_langfuse()` chain has a sharp edge: if
    # `close_redis()` raises (timeout flushing pending commands,
    # network blip, etc.), `close_pool()` + `flush_langfuse()`
    # never run + the asyncpg pool's open TCP connections + the
    # Langfuse SDK's queued traces both leak across SIGTERM. One
    # cleanup failure must NOT cascade to skip the remaining
    # resources.
    #
    # Each step now:
    #   - runs in its own try/except
    #   - catches `Exception` (broad — health-of-shutdown matters
    #     more than discriminating between exception classes)
    #   - logs via _log.error with exc_info=True so the operator
    #     sees the full traceback in the structured log pipeline
    #   - falls through to the NEXT step regardless of the previous
    #     step's outcome
    #
    # Close-order is preserved (Redis → Postgres pool → Langfuse
    # flush). Reverse-init order mirrors orchestrator PR #136
    # (drain Redis before tearing down the pool so in-flight
    # reads/writes complete; flush Langfuse last so the previous
    # cleanup steps' own error logs land in trace data too).
    try:
        await close_redis()
    except Exception as redis_close_error:  # noqa: BLE001 — log + continue
        _log.error(
            "shutdown_close_redis_failed",
            exc_info=redis_close_error,
            extra={"shutdown_step": "close_redis"},
        )
    try:
        await close_pool()
    except Exception as pool_close_error:  # noqa: BLE001 — log + continue
        _log.error(
            "shutdown_close_pool_failed",
            exc_info=pool_close_error,
            extra={"shutdown_step": "close_pool"},
        )
    try:
        # Drain pending Langfuse traces so SIGTERM doesn't lose
        # seconds of in-flight LLM trace data. No-op when Langfuse
        # is disabled.
        flush_langfuse()
    except Exception as langfuse_flush_error:  # noqa: BLE001 — log + continue
        _log.error(
            "shutdown_flush_langfuse_failed",
            exc_info=langfuse_flush_error,
            extra={"shutdown_step": "flush_langfuse"},
        )


# The module-level FastAPI instance uvicorn loads via `app.main:app`.
# Title + version stay generic in the template — new-service.sh
# overwrites them at spawn time with the service-specific values.
app = FastAPI(
    title="yral-rishi-agent service template",
    version="0.1.0",
    lifespan=lifespan,
)

# DEP-014 — mount the F9 health probes (/health/live + /health/ready).
# Routes mount BEFORE middleware so the request flows through the
# middleware chain into a known handler. Other routers (the service's
# real API) mount above this line in spawned services.
app.include_router(health_router)

# Mount RequestIdMiddleware. In Starlette/FastAPI, `add_middleware`
# is LIFO for incoming requests — the LAST added is the FIRST to
# see the request. We want the request ID assigned before anything
# else looks at the request, so this is the LAST middleware we add
# in main.py. Subsequent PRs that add more middleware MUST add them
# BEFORE this line so they sit inside it.
app.add_middleware(RequestIdMiddleware)


# ===========================================================================
# RELATED FILES:
#   __init__.py              — marks app/ as a Python package
#   sentry_middleware.py     — init_sentry() called above (per A7)
#   langfuse_middleware.py   — init_langfuse() + flush_langfuse() (per D4)
#   logging.py               — configure_logging() called above (per H6)
#   request_id_middleware.py — RequestIdMiddleware mounted above
#   database.py              — asyncpg pool lifespan-singleton (DEP-014)
#   redis_client.py          — async Redis lifespan-singleton (DEP-014)
#   health_routes.py         — /health/live + /health/ready router (DEP-014)
#   pyproject.toml           — fastapi + sentry-sdk + langfuse + structlog
#                              + asyncpg + redis
#   Dockerfile               — CMD ["uvicorn", "app.main:app", ...]
#   docker-compose.yml       — local-dev: service + postgres + pgbouncer + redis
# ===========================================================================
