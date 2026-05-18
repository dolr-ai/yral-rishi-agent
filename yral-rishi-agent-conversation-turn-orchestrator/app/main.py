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

# Day-3 safety stack — three BaseHTTPMiddleware classes that sit IN
# FRONT OF the run_turn handler. Order of `add_middleware()` calls
# below carefully managed so request flow is H5 → H4 → A10 → handler;
# see the explanatory block before the add_middleware() calls.
from app.middleware.a10_nsfw_filter import A10NsfwFilterMiddleware
from app.middleware.h4_crisis_detection import H4CrisisDetectionMiddleware
from app.middleware.h5_prompt_injection import H5PromptInjectionMiddleware


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
# Title + version stay generic in the template — new-service.sh
# overwrites them at spawn time with the service-specific values.
app = FastAPI(
    title="yral-rishi-agent service template",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount the run_turn router (Day 2). Adds `POST /v1/turn` to the app.
# Routes always mount BEFORE middleware so the request flows through
# the middleware chain into a known handler. Day 3 adds the safety
# stack (H5/H4/A10) as middleware that wraps this route without
# touching the route signature.
app.include_router(run_turn_router)

# -----------------------------------------------------------------------
# Day-3 safety stack — LIFO ordering carefully managed.
# -----------------------------------------------------------------------
# Starlette/FastAPI `add_middleware()` is LIFO for the REQUEST direction:
# the LAST middleware added is the FIRST to see an incoming request.
# Combined with the existing `RequestIdMiddleware` (added at the very
# bottom of this block) the request flow we want is:
#
#     incoming request
#         → RequestIdMiddleware     (outermost — assigns request id)
#         → H5 prompt-injection     (outermost safety layer)
#         → H4 crisis-detection     (middle safety layer)
#         → A10 NSFW output-filter  (innermost safety layer)
#         → run_turn handler        (innermost)
#
# On the response direction the chain unwinds in reverse — A10 inspects
# the response first, then H4, then H5, then RequestIdMiddleware.
#
# To achieve that request flow, we add the middlewares in the OPPOSITE
# order — A10 first (innermost), then H4, then H5, then
# RequestIdMiddleware last (outermost). This matches the CLAUDE.md
# guidance at `yral-rishi-agent-new-service-template/CLAUDE.md` which
# explicitly warns that `add_middleware` is LIFO and that new
# middleware must go BEFORE the `RequestIdMiddleware` line so it sits
# inside that layer.
#
# WHY ORDER MATTERS FOR THE SAFETY STACK
# - H5 is outermost so adversarial inputs (jailbreaks) get blocked
#   BEFORE H4/A10 see them, reducing the safety surface inner layers
#   must consider.
# - H4 is middle so vulnerable-user inputs (crisis signals) get routed
#   to a helpline placeholder BEFORE A10 inspects the response.
# - A10 is innermost / output-side so it sees the handler's RETURN
#   value (Day-2 stub today; Day-5+ real LLM output later) and rewrites
#   NSFW content with a canned safety reply.
# -----------------------------------------------------------------------

# Add A10 FIRST — becomes the innermost safety layer (last to see the
# request, first to inspect the response).
app.add_middleware(A10NsfwFilterMiddleware)

# Add H4 SECOND — sits between H5 (outer) and A10 (inner).
app.add_middleware(H4CrisisDetectionMiddleware)

# Add H5 THIRD — outermost safety layer; first request-side gate.
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
#   __init__.py                  — marks app/ as a Python package
#   sentry_middleware.py         — init_sentry() called above (per A7)
#   langfuse_middleware.py       — init_langfuse() + flush_langfuse() (per D4)
#   logging.py                   — configure_logging() called above (per H6)
#   request_id_middleware.py     — RequestIdMiddleware mounted above
#   run_turn.py                  — Day-2 POST /v1/turn router mounted above
#   models/turn.py               — Pydantic models the run_turn router consumes
#   middleware/__init__.py       — Day-3 safety-stack package + ASCII chain diagram
#   middleware/h5_prompt_injection.py   — H5 layer mounted above
#   middleware/h4_crisis_detection.py   — H4 layer mounted above
#   middleware/a10_nsfw_filter.py       — A10 layer mounted above
#   safety/canned_responses.py   — canned replies the safety stack returns
#   pyproject.toml               — fastapi + sentry-sdk + langfuse + structlog
#   Dockerfile                   — CMD ["uvicorn", "app.main:app", ...]
#   docker-compose.yml           — local-dev runner with --reload
# ===========================================================================
