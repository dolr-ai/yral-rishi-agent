# ---------------------------------------------------------------------------
# main.py — the FastAPI app entry point for the user-memory-service.
#
# ⭐ START HERE: uvicorn loads `app.main:app`. This file:
#   1. Calls `init_sentry()` BEFORE the FastAPI object exists (must be first).
#   2. Calls `init_langfuse()` for the LLM trace client singleton.
#   3. Calls `configure_logging()` for structured PII-redacted output.
#   4. Defines the `lifespan` context (opens/closes the asyncpg pool).
#   5. Creates the `app` with health endpoints + mounts middleware.
#   6. Mounts the conversation + message RPC router (Deliverable 2).
#
# DELIVERABLE 2 CHANGES (this file):
# - Imports + mounts `conversation_router` from app/api/conversation_routes.py.
# - Upgrades /health/ready from a static stub to a live Postgres ping so
#   the Swarm healthcheck correctly detects a broken DB connection.
#
# WHY HEALTH ROUTES INLINE (not in api/health_routes.py)?
# Per the Session-4 Day-7 deploy pattern: health stubs inline in main.py
# unblock the Swarm healthcheck deploy. /health/ready now pings the pool
# (D2 upgrade); /health/live remains unconditional (process-alive signal).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

# Sentry MUST init before the FastAPI object exists — see sentry_middleware.py
# for the rationale. This is the one import side-effect the template tolerates.
from app.sentry_middleware import init_sentry

# Langfuse follows the same module-load init pattern as Sentry. Flush is
# called at SIGTERM so in-flight traces don't disappear on rolling update.
from app.langfuse_middleware import flush_langfuse, init_langfuse

# Structured logging with PII-allowlist redaction per H6.
from app.logging import configure_logging

# Per-request correlation ID middleware — mounted OUTERMOST so every
# other middleware can call get_request_id().
from app.request_id_middleware import RequestIdMiddleware

# asyncpg pool lifecycle — init at startup, close at SIGTERM.
from app.database import close_pool, get_pool, init_pool

# Deliverable 2: conversation + message RPC routes.
from app.api.conversation_routes import router as conversation_router


# Run Sentry init at module import time — BEFORE FastAPI app exists.
init_sentry()

# Init Langfuse the same way. No-ops when LANGFUSE_TRACING_ENABLED=false.
init_langfuse()

# Configure structlog + stdlib logging. After this line every log call
# emits a structured JSON line with PII redaction.
configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup + shutdown hooks for the user-memory-service.

    WHAT: code before `yield` runs at startup; code after runs on SIGTERM.
          Opens the asyncpg pool at startup + closes it at shutdown.
    WHEN: invoked exactly once per process lifetime by FastAPI itself.
    WHY:  the conversation + message handlers (Deliverable 2) need the
          pool to be open before the first request arrives.
    """
    # --- startup ---------------------------------------------------------
    # Open the asyncpg connection pool. The repository layer in
    # Deliverable 2 calls `get_pool()` to acquire connections from it;
    # init must complete before any request handler runs.
    await init_pool()

    # Hand control to FastAPI — requests are processed here.
    yield

    # --- shutdown --------------------------------------------------------
    # Close the pool cleanly so Postgres backend processes terminate
    # promptly on SIGTERM (Swarm rolling update, scale-down).
    await close_pool()

    # Drain pending Langfuse traces before process exits so no in-flight
    # LLM trace data is lost. No-op when Langfuse is disabled.
    flush_langfuse()


# The module-level FastAPI instance uvicorn loads via `app.main:app`.
# Title reflects the service's Phase 1 purpose: conversation history
# persistence (NOT the Phase 2 semantic-memory framing from the original
# agent definition — see disambiguation in the launch message).
app = FastAPI(
    title="yral-rishi-agent-user-memory-service",
    description=(
        "Phase 1: conversation history persistence (transactional, "
        "chronological). Stores + serves all conversations + messages "
        "for the v2 mobile chat surface. Phase 2 (semantic memory + "
        "pgvector embeddings) is out of scope for this PR."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ===========================================================================
# Health endpoints
# ===========================================================================
#
# /health/live  — unconditional process-alive probe (unchanged from D1)
# /health/ready — live Postgres ping (upgraded in D2 from D1 stub)
#
# Swarm's compose healthcheck hits `/health/ready` every 10s. The ready
# probe now exercises the actual DB pool so a broken Postgres connection
# surfaces as 503 instead of silently passing as 200.
# The /health/deep endpoint (end-to-end round-trip) is deferred to a
# later sprint once traffic patterns are established.


@app.get("/health/live", include_in_schema=False)
async def _health_live() -> dict[str, str]:
    """Process-alive probe — returns 200 while the process is running.

    WHAT: always returns {"status": "ok"}. Never checks downstream deps.
    WHEN: Swarm healthcheck + Uptime Kuma hit this to confirm the process
          hasn't crashed.
    WHY:  fast, unconditional probe. If this 404s, the process is dead.
    """
    # Static response — if we can serve this, we're alive.
    return {"status": "ok", "service": "yral-rishi-agent-user-memory-service"}


@app.get("/health/ready", include_in_schema=False)
async def _health_ready() -> dict[str, str]:
    """Ready-to-serve probe — pings Postgres and returns 503 if unreachable.

    WHAT: acquires a pool connection, runs `SELECT 1`, and returns
          {"status": "ok"} on success. Returns HTTP 503 if the pool is
          uninitialised or the DB is unreachable.
    WHEN: Swarm healthcheck + Caddy health check + Uptime Kuma hit this
          every 10s to confirm the service can handle requests.
    WHY:  Swarm stops routing to replicas that return non-200 here. The
          live Patroni ping (Deliverable 2 upgrade from the D1 stub) gives
          the cluster accurate readiness signal: if Postgres is down, new
          traffic is not routed here rather than queuing and timing out.
    """
    try:
        # get_pool() raises RuntimeError if the pool was never initialised
        # (e.g. if the lifespan startup failed). That exception also maps
        # to 503 via the except clause below.
        pool = get_pool()
        async with pool.acquire() as conn:
            # One-row probe — any error here (connection refused, timeout,
            # auth failure) causes Postgres to raise an asyncpg exception
            # that we catch below and map to 503.
            await conn.fetchval("SELECT 1")
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database pool not ready",
        )
    return {"status": "ok", "service": "yral-rishi-agent-user-memory-service"}


# Mount the conversation + message RPC router (Deliverable 2).
# All 4 handlers are prefixed /v1 (from the router's own prefix).
app.include_router(conversation_router)

# Mount RequestIdMiddleware LAST (LIFO → runs OUTERMOST on incoming
# requests). Subsequent middlewares from Deliverable 2+ must be added
# BEFORE this line so they sit INSIDE the request-ID boundary.
app.add_middleware(RequestIdMiddleware)


# ===========================================================================
# RELATED FILES:
#   __init__.py                      — package marker
#   sentry_middleware.py             — init_sentry() called above
#   langfuse_middleware.py           — init_langfuse() + flush_langfuse()
#   logging.py                       — configure_logging() called above
#   request_id_middleware.py         — RequestIdMiddleware mounted above
#   database.py                      — init_pool() / close_pool() / get_pool()
#   api/conversation_routes.py       — Deliverable 2 RPC route handlers
#   api/models.py                    — Pydantic request + response shapes
#   migrations/versions/001_initial_schema.py
#                                    — base schema (conversations + messages)
#   migrations/versions/002_add_message_fields.py
#                                    — adds client_message_id + count_toward_paywall
#   docker-compose.swarm.yml         — production Swarm stack
#   pyproject.toml                   — runtime deps
# ===========================================================================
