# ---------------------------------------------------------------------------
# request_id_middleware.py — per-request correlation ID for logs + traces.
#
# ⭐ START HERE: `RequestIdMiddleware` reads `X-Request-ID` from each
# incoming request (or generates a UUID4 when absent), stashes it in a
# module-level `ContextVar`, tags the Sentry scope, and echoes it back
# on the response header. `get_request_id()` lets log processors + the
# eventual LLM client read the current ID without re-parsing the
# request object.
#
# WHY ContextVar AND NOT request.state?
# `contextvars.ContextVar` is the asyncio-safe primitive that propagates
# through `await` boundaries. `request.state` requires a reference to
# the FastAPI `Request`; our structured logger runs from background
# tasks + exception handlers that don't have it in scope.
#
# WHY MINT A UUID4 WHEN THE HEADER IS ABSENT?
# We don't assume the edge (rishi-1/2 Caddy → rishi-4/5 swarm) sets
# the header. Minting ourselves guarantees every log line + Sentry
# event + trace has a `request_id` field.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import uuid
from contextvars import ContextVar

import sentry_sdk
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# Default "no-request" so log lines emitted outside a request context
# (e.g. during lifespan startup) don't crash on .get().
_request_id_var: ContextVar[str] = ContextVar("request_id", default="no-request")

# Header name we read on the way IN and write on the way OUT. Matches
# the de-facto industry standard.
REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> str:
    """Return the current request's correlation ID, or 'no-request' outside one.

    WHAT: reads the module-level ContextVar set by RequestIdMiddleware.
    WHEN: called from log processors + the LLM client trace builder.
    WHY:  ContextVar propagates across `await`; usable from anywhere
          the FastAPI `Request` object isn't in scope.
    """
    return _request_id_var.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns + propagates a correlation ID for every request.

    WHAT: reads `X-Request-ID` (or mints UUID4), binds it to the
          ContextVar, tags Sentry scope, echoes header on response.
    WHEN: runs on every request, OUTERMOST in the chain so downstream
          code + log lines + Sentry events all see the ID.
    WHY:  one ID threaded across Sentry + Langfuse + structured logs
          lets us trace a single user action without timestamp guessing.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Trust the incoming header if set; the edge Caddy may add it.
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

        # Bind to ContextVar for this request only. `.set()` returns a
        # token used by `.reset()` in the finally so values don't leak
        # between requests served by the same worker.
        token = _request_id_var.set(request_id)
        try:
            # No-op when Sentry is disabled (empty DSN). When enabled,
            # the tag lands on the per-request Sentry scope created by
            # the FastAPI integration (per PR #22 init).
            sentry_sdk.set_tag("request_id", request_id)

            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            _request_id_var.reset(token)


# ===========================================================================
# RELATED FILES:
#   main.py              — mounts RequestIdMiddleware
#   logging.py           — log processor injects request_id via get_request_id()
#   sentry_middleware.py — Sentry scope receives the request_id tag here
# ===========================================================================
