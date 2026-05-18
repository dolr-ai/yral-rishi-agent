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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

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

# Day-2 API surface: three routers (v1 chat, v2 chat, influencer) +
# the health probes. Each router file documents its own endpoints +
# why those endpoints sit there vs elsewhere.
from app.api.chat_routes import chat_v1_router, chat_v2_router
from app.api.health_routes import health_router
from app.api.influencer_routes import influencer_router


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
    # (filled in by later Day-2 PRs — database pool, redis client, etc.)
    yield
    # --- shutdown -------------------------------------------------------
    # Drain pending Langfuse traces so SIGTERM (Swarm rolling update,
    # scale-down) doesn't lose seconds of in-flight LLM trace data.
    # No-op when Langfuse is disabled.
    flush_langfuse()


# The module-level FastAPI instance uvicorn loads via `app.main:app`.
# Title carries the spawned service's full name so the OpenAPI doc page
# and Swagger UI announce the right service. Version stays at 0.1.0
# until the service ships its first feature.
app = FastAPI(
    title="yral-rishi-agent-public-api",
    version="0.1.0",
    lifespan=lifespan,
)

# Day-2 routers. Order doesn't matter for FastAPI routing (each router
# owns a distinct path prefix); declared in mobile call-frequency
# order so the OpenAPI docs page is sensibly grouped.
app.include_router(chat_v1_router)
app.include_router(chat_v2_router)
app.include_router(influencer_router)
app.include_router(health_router)


# When a handler's dependency raises HTTPException with a dict-shaped
# detail (the feature_flag dependency does this — it builds an
# ApiResponse-shaped body), FastAPI's default behavior would wrap it
# as {"detail": <dict>} which breaks mobile's envelope parser. This
# handler emits the dict verbatim instead, preserving the envelope
# shape for mobile per A8 + the contract.
@app.exception_handler(HTTPException)
async def envelope_aware_http_exception_handler(
    _request: Request,
    exc: HTTPException,
) -> JSONResponse:
    """Preserve dict-shaped HTTPException details verbatim (no wrapping).

    WHAT: when an HTTPException's detail is a dict, emit it as the
          response body unchanged; otherwise emit FastAPI's default
          {"detail": <str>} shape so non-envelope error paths still work.
    WHEN: every time a dependency or handler raises HTTPException.
    WHY:  the feature_flag dependency raises HTTPException(503, detail=<envelope-dict>);
          mobile expects the dict body verbatim per the contract; this
          handler stops FastAPI from re-wrapping it.
    """
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    # Fallback: FastAPI's default {"detail": <str>} for non-envelope
    # callsites (e.g. 422 validation errors from Pydantic).
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


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
#   api/                     — Day-2 route surface (chat / influencer / health)
#   api/chat_routes.py       — chat_v1_router + chat_v2_router (7 endpoints)
#   api/influencer_routes.py — influencer_router (3 read endpoints)
#   api/health_routes.py     — health_router (/health/{live,ready,deep})
#   api/feature_flag.py      — dependency that raises 503 when Day-2 stubs are off
#   api/envelope.py          — ApiResponse[T] every endpoint returns
#   api/dtos.py              — MessageDto / ConversationDto / InfluencerDto / ChatAccessDataDto
#   api/errors.py            — error code Literal + error_response() helper
#   pyproject.toml           — fastapi + sentry-sdk + langfuse + structlog
#   Dockerfile               — CMD ["uvicorn", "app.main:app", ...]
#   docker-compose.yml       — local-dev runner with --reload
# ===========================================================================
