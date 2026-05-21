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

# Day-2 run_turn RPC handler. The router defines `POST /v1/turn`; we
# mount it on the FastAPI app below. See `run_turn.py` for the two-
# gate refusal logic that keeps the stub out of production traffic.
from app.run_turn import router as run_turn_router

# Day-2 fixup (Codex PR-#96 BLOCKER 1) — F10 default-on idempotency.
# Lifespan opens the async Redis client at startup + closes it on
# SIGTERM. Every POST /v1/turn does a Redis dedup lookup before any
# work; same X-Idempotency-Key + X-User-Id within 24h replays the
# previously cached MessageResponse byte-for-byte.
from app.idempotency import close_redis, init_redis

# Day-5 — Soul File Library RPC client lifespan pair. The lifespan
# opens the httpx.AsyncClient once at startup so chat-turn calls
# don't pay TCP-handshake cost per turn (E1 budget). Closed cleanly
# on SIGTERM so no socket leaks on Swarm rolling updates.
from app.soul_file_client import close_soul_file_client, init_soul_file_client

# Day-5 — default LLM client lifespan pair. Builds the Gemini client
# once at startup when `enable_run_turn_real_llm=True`; the SDK's
# process-wide `configure(api_key=...)` happens here so the chat-turn
# hot path doesn't pay that side-effect cost.
from app.llm_client import close_default_llm_client, init_default_llm_client

# Day-6 — H5 / H4 / adult_content safety stack middleware. Restored from
# PR #100 (auto-closed when PR #96's base branch was cascade-deleted).
# Per the agent definition's Day-3 + Day-6 plan: ALL request bodies
# flow through prompt-injection → crisis-detection → adult_content-output-filter
# BEFORE reaching the run_turn handler. LIFO `add_middleware` ordering
# below produces request flow H5 → H4 → adult_content → handler (see comment
# above the calls for the LIFO mapping).
from app.middleware.h5_prompt_injection import H5PromptInjectionMiddleware
from app.middleware.h4_crisis_detection import H4CrisisDetectionMiddleware
from app.middleware.adult_content_output_filter import AdultContentOutputFilterMiddleware


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
    # Open the async Redis client — the F10 idempotency layer (Codex
    # PR-#96 BLOCKER 1 fix) needs it ready before any request handler
    # runs. The connection is pooled internally by redis-py; one
    # client serves every concurrent request.
    await init_redis()
    # Day-5 — open the Soul File Library RPC client. Same lifespan-
    # singleton pattern as Redis; warm connection pool means the
    # chat-turn hot path doesn't pay TCP handshake on every call.
    await init_soul_file_client()
    # Day-5 — build the default LLM client (Gemini) when the real-LLM
    # flag is on. No-op when the flag is off (handler routes to the
    # Day-2 stub path instead).
    init_default_llm_client()
    yield
    # --- shutdown -------------------------------------------------------
    # Day-5 — release the LLM client reference (GC handles the SDK's
    # internal pool). Runs before the RPC clients so we stop issuing
    # NEW LLM calls before tearing down the downstream RPC pool.
    close_default_llm_client()
    # Day-5 — close the Soul File client before Redis so any
    # in-flight RPC has a chance to drain cleanly.
    await close_soul_file_client()
    # Close Redis cleanly so connections don't linger on the server
    # side past their idle timeout. Cleaner shutdown == faster Swarm
    # rolling updates.
    await close_redis()
    # Drain pending Langfuse traces so SIGTERM (Swarm rolling update,
    # scale-down) doesn't lose seconds of in-flight LLM trace data.
    # No-op when Langfuse is disabled.
    flush_langfuse()


# The module-level FastAPI instance uvicorn loads via `app.main:app`.
# Title + version stay generic in the template — new-service.sh
# overwrites them at spawn time with the service-specific values.
app = FastAPI(
    title="yral-rishi-agent service template",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount the run_turn router (Day 2). Adds `POST /v1/turn` to the app.
# Routes always mount BEFORE middleware so the request flows through
# the middleware chain into a known handler. The Day-6 safety stack
# (H5/H4/adult_content) wraps this route as middleware without touching the
# route signature.
app.include_router(run_turn_router)


# Day-7 deploy stubs: Swarm's compose healthcheck hits /health/ready
# every 10s. Public-api wired the full F9 contract (live + ready +
# deep with Sentinel-aware Redis ping) in app/api/health_routes.py;
# Session 4 services ship minimal stubs here so deploy converges, and
# the real F9 wiring (Redis Sentinel ping on ready, deep round-trip)
# lands in a follow-up PR. /live = process is alive; /ready = stub 200
# until the F9 ping is wired.
@app.get("/health/live", include_in_schema=False)
async def _health_live() -> dict[str, str]:
    return {"status": "ok", "service": "yral-rishi-agent-conversation-turn-orchestrator"}


@app.get("/health/ready", include_in_schema=False)
async def _health_ready() -> dict[str, str]:
    return {"status": "ok", "service": "yral-rishi-agent-conversation-turn-orchestrator"}

# -------------------------------------------------------------------
# Day-6 safety stack — H5 → H4 → adult_content → handler (REQUEST flow).
# -------------------------------------------------------------------
# Starlette/FastAPI's `add_middleware` is LIFO for the REQUEST
# direction: the LAST middleware added is the FIRST to see an
# incoming request. To produce the directive's REQUEST flow
# `H5 → H4 → adult_content → handler`, add_middleware order is the REVERSE:
#
#   add_middleware(AdultContentOutputFilterMiddleware)        # 1st added → innermost
#   add_middleware(H4CrisisDetectionMiddleware)    # 2nd added → middle
#   add_middleware(H5PromptInjectionMiddleware)    # 3rd added → outermost safety
#   add_middleware(RequestIdMiddleware)            # 4th added → outermost overall
#
# Net wire-level REQUEST flow:
#   RequestId → H5 → H4 → adult_content → run_turn handler
# Net wire-level RESPONSE flow (LIFO unwinds):
#   handler → adult_content → H4 → H5 → RequestId
#
# The order-verification test in `tests/test_safety_stack.py` pins
# this contract so a future accidental reordering surfaces as a
# loud failure rather than a silent safety-bypass regression.
app.add_middleware(AdultContentOutputFilterMiddleware)
app.add_middleware(H4CrisisDetectionMiddleware)
app.add_middleware(H5PromptInjectionMiddleware)

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
#   run_turn.py              — Day-2 POST /v1/turn router mounted above
#   models/turn.py           — Pydantic models the run_turn router consumes
#   idempotency.py           — F10 Redis dedup; init_redis/close_redis above
#   config.py                — redis_url + enable_run_turn_stub settings
#   pyproject.toml           — fastapi + sentry-sdk + langfuse + structlog + redis
#   Dockerfile               — CMD ["uvicorn", "app.main:app", ...]
#   docker-compose.yml       — local-dev runner with --reload (includes redis)
# ===========================================================================
