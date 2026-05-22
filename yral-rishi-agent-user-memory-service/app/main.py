# ---------------------------------------------------------------------------
# main.py — the FastAPI app entry point for the user-memory-service.
#
# ⭐ START HERE: uvicorn loads `app.main:app`. This file:
#   1. Calls `init_sentry()` BEFORE the FastAPI object exists (must be first).
#   2. Calls `init_langfuse()` for the LLM trace client singleton.
#   3. Calls `configure_logging()` for structured PII-redacted output.
#   4. Defines the `lifespan` context (opens/closes the asyncpg pool).
#   5. Creates the `app` with health endpoints + mounts middleware.
#   6. Conversation + message route handlers are added in Deliverable 2.
#
# PHASE 1 SCOPE (this file's current state):
# Deliverable 1 is schema + migration only. This file provides the
# minimal service skeleton that can deploy and respond to health probes.
# Conversation + message RPC routes land in Deliverable 2 (next branch).
#
# WHY HEALTH ROUTES INLINE (not in api/health_routes.py)?
# Per the Session-4 Day-7 deploy pattern: minimal health stubs inline in
# main.py unblock the Swarm healthcheck deploy. A full F9 health contract
# (Patroni ping in /ready, end-to-end round-trip in /deep) lands in a
# follow-up PR once the first deploy is green.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager

from fastapi import FastAPI

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
from app.database import close_pool, init_pool


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
# Health endpoints — Deliverable 1 stubs (full F9 contract in follow-up)
# ===========================================================================
#
# Swarm's compose healthcheck hits `/health/ready` every 10s. These stubs
# return 200 so the rolling-update deploy converges. The full F9 contract
# (live = process-alive, ready = Patroni ping, deep = end-to-end round trip)
# lands in a follow-up PR after the first deploy is green — same pattern
# Session 4 used on Day-7 with the stub-then-wire approach.


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
    """Ready-to-serve probe — stub returning 200.

    WHAT: stub returning {"status": "ok"}. Real implementation (follow-up
          PR) will ping the Postgres pool and return 503 if unreachable.
    WHEN: Swarm healthcheck + Caddy health check + Uptime Kuma hit this
          to confirm the service can serve requests.
    WHY:  Swarm stops routing to replicas that return non-200 here. The
          full Patroni ping lands after the first deploy is verified green
          (same pattern as Session 4 Day-7 health-route-stubs PR).
    """
    # Stub — the full F9 ready probe (Patroni ping) lands in follow-up.
    return {"status": "ok", "service": "yral-rishi-agent-user-memory-service"}


# Route handlers for conversations + messages mount here in Deliverable 2.
# Structure placeholder so the TODO is traceable:
#   from app.api.conversation_routes import router as conversation_router
#   app.include_router(conversation_router)

# Mount RequestIdMiddleware LAST (LIFO → runs OUTERMOST on incoming
# requests). Subsequent middlewares from Deliverable 2+ must be added
# BEFORE this line so they sit INSIDE the request-ID boundary.
app.add_middleware(RequestIdMiddleware)


# ===========================================================================
# RELATED FILES:
#   __init__.py                    — package marker
#   sentry_middleware.py           — init_sentry() called above
#   langfuse_middleware.py         — init_langfuse() + flush_langfuse()
#   logging.py                     — configure_logging() called above
#   request_id_middleware.py       — RequestIdMiddleware mounted above
#   database.py                    — init_pool() / close_pool() in lifespan
#   api/conversation_routes.py     — Deliverable 2 RPC route handlers
#   migrations/versions/001_initial_schema.py
#                                  — Alembic migration for conversations +
#                                    messages tables
#   docker-compose.swarm.yml       — production Swarm stack that runs this app
#   pyproject.toml                 — runtime deps (fastapi, asyncpg, alembic, ...)
# ===========================================================================
