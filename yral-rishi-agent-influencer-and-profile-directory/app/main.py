# ---------------------------------------------------------------------------
# main.py — the FastAPI app entry point. uvicorn loads `app.main:app`.
#
# ⭐ START HERE: top-of-module calls `init_sentry()` FIRST (must run before
# the FastAPI object exists so the integration hooks in). The lifespan
# hook opens the asyncpg pool at startup + closes it on SIGTERM + flushes
# Langfuse on shutdown. The `/v1/influencers` + `/v1/influencers/{id}`
# catalog routes mount via `include_router(influencer_router)` (added in
# Chunk B per PR #148 round-8 header instruction). Health endpoints
# (live + ready) are minimal stubs today; full F9 deep-check wiring
# lands when the Redis cache layer ships.
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

from fastapi import FastAPI

# Sentry MUST init before the FastAPI object exists. See the file header
# rationale above and sentry_middleware.py's own header. This is the one
# import side-effect the template tolerates by design.
from app.sentry_middleware import init_sentry

# Langfuse follows the same module-load init pattern as Sentry for
# uniformity. The flush helper is called from the lifespan shutdown
# (below) so SIGTERM doesn't drop in-flight LLM traces.
from app.langfuse_middleware import flush_langfuse, init_langfuse

# Structured logging with PII allowlist redaction per H6. Configured
# at module-load so any startup log lines emit through the same
# pipeline as request logs.
from app.logging import configure_logging

# Per-request correlation ID middleware. Mounted on the FastAPI app
# below so it runs OUTERMOST in the request chain.
from app.request_id_middleware import RequestIdMiddleware

# Chunk B asyncpg pool lifecycle. The lifespan startup hook opens the
# pool; the shutdown hook closes it. The repository layer + every route
# handler reaches Postgres via `app.database.get_pool()` per F12
# (asyncpg, no ORM).
from app.database import close_pool, init_pool

# Chunk B HTTP routes — `GET /v1/influencers` + `GET /v1/influencers/{id}`.
# Mounted on the app below via `include_router`.
from app.api.influencer_routes import router as influencer_router


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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup + shutdown hooks for the FastAPI app.

    WHAT: code before `yield` runs at startup; code after runs on
          SIGTERM. Startup opens the asyncpg connection pool; shutdown
          closes it then flushes Langfuse.
    WHEN: invoked exactly once per process lifetime by FastAPI itself.
    WHY:  the pool MUST exist before the first request handler runs +
          MUST close cleanly on SIGTERM so Postgres backend processes
          terminate promptly during Swarm rolling-update / scale-down.
          Pool init lives in `app.database.init_pool()`; close in
          `app.database.close_pool()`. The repository layer + every
          route handler reaches Postgres via `get_pool()`.
    """
    # --- startup --------------------------------------------------------
    # Open the asyncpg connection pool. The repository layer +
    # every route handler call `app.database.get_pool()` to grab
    # connections from it; init must complete before the first request
    # handler runs.
    await init_pool()
    yield
    # --- shutdown -------------------------------------------------------
    # Close the pool cleanly so Postgres backend processes terminate
    # promptly on SIGTERM (Swarm rolling update, scale-down).
    await close_pool()
    # Drain pending Langfuse traces so SIGTERM doesn't lose seconds of
    # in-flight LLM trace data. No-op when Langfuse is disabled.
    flush_langfuse()


# The module-level FastAPI instance uvicorn loads via `app.main:app`.
app = FastAPI(
    title="yral-rishi-agent-influencer-and-profile-directory",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount the Chunk B influencer router. Adds `GET /v1/influencers` +
# `GET /v1/influencers/{id}` to the app — the public catalog endpoints
# that public-api's directory_client calls per the contract at
# `interface-contracts/01-internal-rpc-contracts.md`.
app.include_router(influencer_router)


# Day-7 deploy stubs: Swarm's compose healthcheck hits /health/ready
# every 10s. Public-api wired the full F9 contract (live + ready + deep
# with Sentinel-aware Redis ping) in app/api/health_routes.py; this
# service ships minimal stubs here so deploy converges. Real F9 deep-
# check wiring (Patroni connection ping for ready, end-to-end round-
# trip including Redis cache for deep) lands when the Redis cache
# layer ships in a follow-up PR (the API contract's `GET
# /api/v1/influencers` Cache-Control 300s note tracks that work).
@app.get("/health/live", include_in_schema=False)
async def _health_live() -> dict[str, str]:
    return {"status": "ok", "service": "yral-rishi-agent-influencer-and-profile-directory"}


@app.get("/health/ready", include_in_schema=False)
async def _health_ready() -> dict[str, str]:
    return {"status": "ok", "service": "yral-rishi-agent-influencer-and-profile-directory"}


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
#   database.py              — init_pool() / close_pool() called in lifespan
#   api/influencer_routes.py — `GET /v1/influencers` + `/v1/influencers/{id}`
#                              router mounted above
#   models/influencer_response.py
#                            — wire-shape Pydantic the routes return
#   models/influencer_metadata.py
#                            — internal persistence Pydantic (round-8
#                              header instructed Chunk B to add the
#                              separate response model now wired above)
#   repository/influencer_metadata_repository.py
#                            — data-access layer the routes call
#   pyproject.toml           — fastapi + asyncpg + sentry-sdk + langfuse
#   Dockerfile               — CMD ["uvicorn", "app.main:app", ...]
#   docker-compose.yml       — local-dev runner with --reload
# ===========================================================================
