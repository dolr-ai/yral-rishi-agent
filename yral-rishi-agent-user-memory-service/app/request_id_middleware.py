# ---------------------------------------------------------------------------
# request_id_middleware.py — per-request correlation ID injection.
#
# ⭐ START HERE: this file does ONE thing — `RequestIdMiddleware` reads (or
# generates) an `X-Request-Id` header for every incoming request, stores
# the value in a `contextvars.ContextVar`, and echoes it back in the
# response. Downstream code calls `get_request_id()` to read the ID and
# include it in log lines, Sentry breadcrumbs, and outbound RPC headers.
#
# WHY A PER-REQUEST CORRELATION ID?
# When an orchestrator call fails, the public-api log says "request 3e5a…
# got 503 from orchestrator" and the orchestrator log says "request 3e5a…
# got LLM timeout". Without a shared ID, correlating across services means
# guessing by timestamp (fragile on a busy cluster). The ID makes cross-
# service debugging deterministic — one grep, full picture.
#
# WHY ContextVar AND NOT A MODULE-LEVEL VAR?
# asyncio is concurrent — multiple requests run in the same thread. A
# module-level variable would be shared across concurrent requests and
# would clobber itself. `contextvars.ContextVar` is per-task: each asyncio
# Task (= each request) has its own copy.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import contextvars
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


# ContextVar storing the current request's correlation ID. Default = empty
# string so any code that calls get_request_id() outside a request context
# gets a safe, non-crashing empty string rather than raising LookupError.
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


def get_request_id() -> str:
    """Return the correlation ID for the current request.

    WHAT: reads the ContextVar set by RequestIdMiddleware for this
          asyncio Task.
    WHEN: called from log statements, Sentry context setters, and
          outbound RPC headers (e.g. orchestrator call forwarding the
          same ID downstream).
    WHY:  correlates log lines, Sentry events, and RPC traces to the
          same originating mobile request — critical for debugging
          multi-hop failures.
    """
    # Read from the task-local ContextVar. Returns "" if called
    # outside a request (e.g. startup / shutdown hooks).
    return _request_id_var.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that injects a per-request correlation ID.

    WHAT: on every request, reads `X-Request-Id` from the headers (mobile
          can send one) or mints a fresh UUID4 if absent. Stores it in
          `_request_id_var` so every callsite in the request's stack can
          read it via `get_request_id()`. Adds `X-Request-Id` to the
          response headers so mobile can log the same ID.
    WHEN: wraps EVERY HTTP request — mounted OUTERMOST in main.py's
          middleware stack so other middlewares can call get_request_id().
    WHY:  central + early assignment ensures no log line or Sentry event
          is emitted without a correlation ID attached.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Extract or mint the request ID; bind it to the ContextVar.

        WHAT: reads X-Request-Id header or generates a UUID; sets
              _request_id_var; calls the rest of the middleware stack
              + handler; echoes the ID back in the response.
        WHEN: invoked for every HTTP request (Starlette calls this).
        WHY:  binding to ContextVar here ensures every async task
              spawned from this request inherits the same ID.
        """
        # Read client-provided ID or generate a fresh one.
        # Using UUID4 (random, no meaningful prefix) so multiple services
        # can each generate IDs without collision.
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())

        # Bind the ID to the ContextVar for this asyncio Task's scope.
        token = _request_id_var.set(request_id)

        try:
            # Run the rest of the middleware stack + route handler.
            response = await call_next(request)
        finally:
            # Reset the ContextVar after the response is built so the
            # same task re-used by the event loop doesn't carry a stale ID
            # into the next request. ContextVar.reset() is the correct
            # cleanup path (not set("") — that would mask bugs where
            # get_request_id() is called on a reused task before the
            # next request's middleware runs).
            _request_id_var.reset(token)

        # Echo the ID back so the caller can correlate its own logs
        # with the server-side logs.
        response.headers["X-Request-Id"] = request_id
        return response


# ===========================================================================
# RELATED FILES:
#   main.py                          — mounts RequestIdMiddleware last
#                                      (LIFO → runs outermost)
#   api/conversation_routes.py       — calls get_request_id() for RPC
#                                      headers (Deliverable 2)
#   logging.py                       — log lines include request_id via
#                                      structlog bound loggers
# ===========================================================================
