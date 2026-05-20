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

# contextlib.asynccontextmanager — wraps the lifespan generator below
# in the FastAPI-expected async-context-manager shape (startup before
# yield, shutdown after).
from contextlib import asynccontextmanager

# fastapi — FastAPI builds the ASGI app; HTTPException lets dependencies
# raise envelope-shaped errors; Request gives exception handlers access
# to the original request object for logging.
from fastapi import FastAPI, HTTPException, Request

# fastapi.exceptions.RequestValidationError — raised by FastAPI when
# Pydantic input validation fails. We catch it in a custom handler so
# every validation error returns the ApiResponse envelope per A8 +
# Codex PR #97 BLOCKER 2 (mobile parses the envelope shape; raw
# {"detail": [...]} breaks the parser).
from fastapi.exceptions import RequestValidationError

# fastapi.responses.JSONResponse — emit the envelope dict directly as
# the response body without FastAPI's default wrapping.
from fastapi.responses import JSONResponse

# fastapi.encoders.jsonable_encoder — coerces arbitrary Python objects
# (incl. Pydantic v2 ValueError instances inside `exc.errors()`'s
# `ctx` field) into JSON-serializable shapes. Used by the
# RequestValidationError handler so the per-field error detail
# survives serialization in the envelope's data.errors field.
from fastapi.encoders import jsonable_encoder

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
from app.api.chat_routes import chat_v1_router, chat_v1_ws_router, chat_v2_router
from app.api.health_routes import health_router
from app.api.influencer_routes import admin_influencer_router, influencer_router

# Error-helper + HTTP-status map — used by the RequestValidationError
# handler below to build the envelope-shaped 400 body per Codex
# BLOCKER 2 + the locked error-codes table.
from app.api.errors import HTTP_STATUS_FOR_ERROR_CODE, error_response

# Day-4C orchestrator client — lifespan-managed singleton httpx.AsyncClient.
# init / close run from the FastAPI lifespan below so the pool is
# allocated once per worker + drained gracefully on SIGTERM.
from app.orchestrator_client import close_orchestrator_client, init_orchestrator_client


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
          SIGTERM. Day-4C added orchestrator-client allocation
          + teardown.
    WHEN: invoked exactly once per process lifetime by FastAPI itself.
    WHY:  reserve the structure for subsequent PRs (database pool,
          Redis client wrapper, langfuse worker) so they plug in
          without renaming anything.
    """
    # --- startup --------------------------------------------------------
    # Day-4C: allocate the orchestrator httpx.AsyncClient singleton.
    # One pool per worker, reused across requests, drained on shutdown.
    init_orchestrator_client()
    yield
    # --- shutdown -------------------------------------------------------
    # Day-4C: drain pending orchestrator-bound connections gracefully
    # before SIGTERM ends the process.
    await close_orchestrator_client()
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
# WebSocket inbox stub lives on a separate router (see chat_routes.py
# comment) so router-level HTTP-Request-typed auth deps don't bleed
# into the WS resolution path. The WS stub does its own auth check
# inline on the WebSocket's headers.
app.include_router(chat_v1_ws_router)
app.include_router(chat_v2_router)
app.include_router(influencer_router)
# Codex PR #97 BLOCKER 4 — admin influencer stubs separate router so
# OpenAPI groups them + future PR can wire a different auth path.
app.include_router(admin_influencer_router)
app.include_router(health_router)


# Codex PR #97 round-5 ITEM 6 — refuse to start in production when
# the C11 Sentinel flag is OFF. Mirrors Session 4's PR #96 round-4
# pattern. Logs CRITICAL + sys.exit(1) on violation; no-op in local /
# staging where single-primary fallback is allowed for laptop dev.
# Called HERE (after routers, before app starts serving) so it runs
# at module load (uvicorn worker startup) BUT only after the import
# graph is settled — keeps the failure mode loud + early.
from app.api.health_routes import verify_production_sentinel_or_die  # noqa: E402

verify_production_sentinel_or_die()


# Map common HTTP status codes to the locked ErrorCode strings the
# contract whitelists (per `app/api/errors.py` ErrorCode + the
# error-codes table in interface-contracts/00-api-contract.md). Used
# by the HTTPException handler below to wrap non-dict details — a raw
# `HTTPException(404, "string")` would otherwise serialize as
# `{"detail": "string"}` which breaks A8/A16 (mobile's parser hard-
# requires the envelope shape on EVERY response).
#
# Added per Codex PR #97 round-5 ITEM 3. Statuses not in this map fall
# back to "service_unavailable" — the closest locked code for an
# unknown server-side failure.
#
# NOTE on 409 Conflict (Codex PR #97 round-6 finding, 2026-05-20):
# 409 is NOT in this map. Round-5 mistakenly added `409: "conflict"`
# but "conflict" isn't in the locked 8-code ErrorCode list (per
# errors.py's Literal + the contract's error-codes table). Mobile
# clients would have received an unknown error code. The fallback
# below (`.get(status, "service_unavailable")`) now picks up 409
# → "service_unavailable" automatically — closest locked match per
# the coordinator directive's fallback path.
#
# If a future PR concludes 409 deserves its own dedicated locked
# code, the path is: (a) add "conflict" to errors.py's ErrorCode
# Literal, (b) add the entry to the contract's error-codes table,
# (c) re-add `409: "conflict"` here. Per A8 + the errors.py header,
# locked-code additions go through the contract first.
_STATUS_TO_LOCKED_ERROR_CODE: dict[int, str] = {
    400: "validation_failed",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    422: "validation_failed",
    503: "service_unavailable",
}


# When a handler's dependency raises HTTPException with a dict-shaped
# detail (the feature_flag dependency does this — it builds an
# ApiResponse-shaped body), FastAPI's default behavior would wrap it
# as {"detail": <dict>} which breaks mobile's envelope parser. This
# handler emits the dict verbatim if it's already envelope-shaped,
# OR wraps non-dict details into a fresh envelope per the locked
# contract (Codex PR #97 round-5 ITEM 3).
@app.exception_handler(HTTPException)
async def envelope_aware_http_exception_handler(
    _request: Request,
    exc: HTTPException,
) -> JSONResponse:
    """Wrap every HTTPException in the locked ApiResponse envelope shape.

    WHAT: when an HTTPException's detail is a dict that already has the
          envelope's 4 keys (`success`, `msg`, `error`, `data`), emit
          it verbatim. Otherwise wrap the detail in a fresh envelope
          with `success=False`, the locked error code from
          `_STATUS_TO_LOCKED_ERROR_CODE`, and the original detail as
          the user-facing `msg`.
    WHEN: every time a dependency or handler raises HTTPException —
          whether it built the envelope itself (feature_flag dep,
          auth dep) or just raised a bare `HTTPException(404, "...")`.
    WHY:  A8 + A16 require EVERY error response to use the envelope
          shape mobile parses. Pre-round-5 the fallback path emitted
          the FastAPI default `{"detail": <str>}` which silently
          broke parity for any handler that raised a bare
          HTTPException. This handler now guarantees the envelope
          contract uniformly.
    """
    if isinstance(exc.detail, dict) and {"success", "msg", "error", "data"} <= exc.detail.keys():
        # Already envelope-shaped — emit verbatim. The 4-key subset
        # check guards against accidentally emitting an unrelated
        # dict as if it were an envelope.
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    # Wrap into the locked envelope shape (Codex PR #97 round-5 ITEM 3).
    # Pick the error code from the locked map; fall back to
    # `service_unavailable` for unknown statuses.
    locked_error_code = _STATUS_TO_LOCKED_ERROR_CODE.get(
        exc.status_code, "service_unavailable",
    )
    # The `msg` field carries the original detail (string) so a
    # debug build / on-call dashboard still sees the underlying
    # message; mobile-facing tooling reads `error` for the locked
    # code mobile pattern-matches on.
    msg_text = str(exc.detail) if exc.detail is not None else "HTTP error"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "msg": msg_text,
            "error": locked_error_code,
            "data": None,
        },
    )


# FastAPI's default RequestValidationError response is
# {"detail": [...]} with HTTP 422 — fine for a generic JSON API but
# fatal for THIS service because the locked contract (per A8 + A16)
# requires every endpoint INCLUDING validation errors to return the
# ApiResponse envelope. Without this handler, mobile's parser would
# crash on any malformed request body. Per the contract's locked
# error-codes table, validation_failed → HTTP 400 (not 422).
#
# Added per Codex PR #97 BLOCKER 2 (Rishi 2026-05-19).
@app.exception_handler(RequestValidationError)
async def envelope_validation_error_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Convert Pydantic validation failures into envelope-shaped 400 responses.

    WHAT: builds ApiResponse(success=False, msg=<human-readable>,
          error="validation_failed", data={"errors": <Pydantic detail>})
          and returns HTTP 400 (per the contract error-codes table —
          NOT FastAPI's default 422; chat-ai uses 400 for validation
          failures and mobile expects that status per A8).
    WHEN: invoked automatically whenever FastAPI-side Pydantic
          validation fails (request body shape mismatch, query-param
          coercion failure, missing required field, Literal value out
          of range, etc.).
    WHY:  keeps the envelope contract uniform across every endpoint;
          mobile's parser pattern-matches on `error="validation_failed"`
          to surface user-facing input-correction prompts. The Pydantic
          `errors()` detail flows through inside `data.errors` so a
          debug build / dev tool can still surface the per-field cause
          without breaking the wire shape.
    """
    # jsonable_encoder coerces Pydantic's per-field error dicts —
    # which include raw ValueError instances inside `ctx` when a
    # model_validator raises — into JSON-serializable plain shapes.
    # Without it, the json encoder crashes on the ValueError object
    # and the response itself fails to serialize.
    body = error_response(
        "validation_failed",
        "Request validation failed; see data.errors for per-field detail.",
        data={"errors": jsonable_encoder(exc.errors())},
    ).model_dump()
    return JSONResponse(
        status_code=HTTP_STATUS_FOR_ERROR_CODE["validation_failed"],
        content=body,
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
#   api/response_models.py              — MessageResponse / ConversationResponse / InfluencerResponse / ChatAccessDataResponse
#   api/errors.py            — error code Literal + error_response() helper
#   pyproject.toml           — fastapi + sentry-sdk + langfuse + structlog
#   Dockerfile               — CMD ["uvicorn", "app.main:app", ...]
#   docker-compose.yml       — local-dev runner with --reload
# ===========================================================================
