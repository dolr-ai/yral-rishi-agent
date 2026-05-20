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

# Day-4 asyncpg pool lifecycle. The lifespan startup hook opens the
# pool; the shutdown hook closes it. Everything else in the app talks
# to Postgres via `app.database.get_pool()` per F12 (asyncpg, no ORM).
from app.database import close_pool, init_pool

# Day-4 HTTP route — `GET /composed-prompt`. Mounted on the app below.
from app.api.composed_prompt_routes import router as composed_prompt_router


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
          SIGTERM. Today both halves are empty placeholders.
    WHEN: invoked exactly once per process lifetime by FastAPI itself.
    WHY:  reserve the structure now so subsequent Day-2 PRs (database
          pool, redis client, langfuse worker) can plug in without
          renaming anything or touching the signature.
    """
    # --- startup --------------------------------------------------------
    # Open the asyncpg connection pool. The repository layer + composer
    # call `app.database.get_pool()` to grab connections from it; init must
    # complete before the first request handler runs.
    await init_pool()
    yield
    # --- shutdown -------------------------------------------------------
    # Close the pool cleanly so Postgres backend processes terminate
    # promptly on SIGTERM (Swarm rolling update, scale-down).
    await close_pool()
    # Drain pending Langfuse traces so SIGTERM (Swarm rolling update,
    # scale-down) doesn't lose seconds of in-flight LLM trace data.
    # No-op when Langfuse is disabled.
    flush_langfuse()


# The module-level FastAPI instance uvicorn loads via `app.main:app`.
# Title + version stay generic in the template — new-service.sh
# overwrites them at spawn time with the service-specific values.
app = FastAPI(
    title="yral-rishi-agent-soul-file-library",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount the composed-prompt router (Day 4). Adds `GET /composed-prompt`
# to the app — the internal RPC the orchestrator calls per chat turn
# (Day-5+) per `interface-contracts/01-internal-rpc-contracts.md`.
app.include_router(composed_prompt_router)

# Mount RequestIdMiddleware. In Starlette/FastAPI, `add_middleware`
# is LIFO for incoming requests — the LAST added is the FIRST to
# see the request. We want the request ID assigned before anything
# else looks at the request, so this is the LAST middleware we add
# in main.py. Subsequent PRs that add more middleware MUST add them
# BEFORE this line so they sit inside it.
app.add_middleware(RequestIdMiddleware)


# ===========================================================================
# RELATED FILES:
#   __init__.py                  — marks app/ as a Python package
#   sentry_middleware.py         — init_sentry() called above (per A7)
#   langfuse_middleware.py       — init_langfuse() + flush_langfuse() (per D4)
#   logging.py                   — configure_logging() called above (per H6)
#   request_id_middleware.py     — RequestIdMiddleware mounted above
#   database.py                        — init_pool() / close_pool() called in lifespan
#   api/composed_prompt_routes.py
#                                — Day-4 GET /composed-prompt router mounted above
#   composer/four_layer_composer.py
#                                — what the route delegates to
#   repository/soul_file_repository.py
#                                — what the composer fetches its layers from
#   models/soul_file.py          — Pydantic models for both
#   pyproject.toml               — fastapi + asyncpg + alembic + sentry-sdk + ...
#   Dockerfile                   — CMD ["uvicorn", "app.main:app", ...]
#   docker-compose.yml           — local-dev runner with postgres + pgbouncer
# ===========================================================================
