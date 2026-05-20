# ---------------------------------------------------------------------------
# a10_adult_content_filter.py — Day-3 A10 NSFW output-side filter middleware.
#
# ⭐ START HERE: this module exports `A10AdultContentFilterMiddleware`, the
# INNERMOST safety layer in the H5 → H4 → A10 → handler chain. Unlike
# H5 + H4 (which inspect the request body BEFORE the handler runs),
# A10 inspects the RESPONSE body AFTER the handler returns. If the
# response's `content` field matches the NSFW rule set, A10 rewrites
# the response with a canned safety reply (per
# `app/safety/canned_responses.py::adult_content_blocked`) + flips
# `count_toward_paywall` to False.
#
# WHY A10 IS OUTPUT-SIDE
# Per the Day-3 directive verbatim: "A10 — NSFW filter (output-side).
# For Day-3 stub: runs on the handler's RETURN value (the stub
# MessageResponse.content). Checks against an NSFW keyword list."
# Day-5+ real LLM enablement feeds actual model output through this
# layer unchanged — same dispatch path, same canned reply, just with
# real content as the inspected payload instead of the Day-2 stub.
#
# WHY A10 ALSO RECORDS THE "handler" MARKER
# Per the Day-3 directive's order-verification spec:
#   assert order is [H5_entry, H4_entry, A10_entry, handler,
#                    A10_exit, H4_exit, H5_exit]
# AND per the directive's scope guardrail: "ONLY new middleware files +
# main.py wiring. Do NOT modify run_turn.py or models/turn.py." The
# run_turn handler itself is out-of-scope to modify, so it cannot
# append "handler" to the audit trail directly. Instead, A10 — which
# wraps the handler call inside its own `call_next` — appends the
# synthetic "handler" marker immediately after `call_next` returns.
# By construction, the handler executed (or H5/H4 short-circuited)
# during that `call_next`, so the marker accurately signals
# "downstream chain completed" at that point in the audit trail.
#
# WHY USE BaseHTTPMiddleware EVEN THOUGH WE INSPECT RESPONSE BODY
# Modifying a Starlette response body inside BaseHTTPMiddleware
# requires draining `response.body_iterator` (the inner ASGI stream)
# and reconstructing a fresh Response. This is more verbose than the
# request-side pattern in H5/H4 but stays within the same
# middleware base class — uniform interface across the three layers.
# Drop to pure-ASGI middleware ONLY if a future safety layer needs
# real streaming semantics (Day-5+ if the LLM streams responses,
# which the agent def explicitly defers to /v2 path).
#
# THE GATE-RESPECT PATTERN
# Same as H5 + H4 — when EITHER `environment == "production"` or
# `enable_run_turn_stub` is false, A10 passes through without
# inspecting the response so the handler's 503-emission fires
# unchanged. Avoids "leak via safety bypass".
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib `json` — parses the response body so the A10 keyword set
# can scan the handler's reply text + rebuild the body when the
# rewrite path fires.
import json

# stdlib logger — emits structured fields the H6 redactor in
# `app/logging.py` knows about (safety_layer / reason /
# conversation_id). NEVER the handler reply content itself.
import logging

# stdlib regex — compiled-once keyword-pattern set. Phase-1 keyword-
# list approach; classifier swap is a later phase per the agent
# definition.
import re

# `Final` marks the pattern set + the guarded-path constant as
# immutable.
from typing import Final

# Starlette's `BaseHTTPMiddleware` is the request/response wrapping
# base class we extend.
from starlette.middleware.base import BaseHTTPMiddleware

# `Request` — the typed inbound request wrapper. Used for
# `request.url.path` (route gating). A10 doesn't read the request
# body (it's an OUTPUT-side filter).
from starlette.requests import Request

# `JSONResponse` builds the A10 canned reply when a match fires.
# `Response` is the typed return shape of dispatch().
from starlette.responses import JSONResponse, Response

# `ASGIApp` is the constructor parameter type. Annotation only.
from starlette.types import ASGIApp

# `get_settings()` reads the Settings singleton for the gate-respect
# check (env + the Day-5 path flags).
from app.config import get_settings

# `record(...)` appends to the audit-trail ContextVar so the order-
# verification test can pin chain execution.
from app.middleware._safety_audit import record

# `adult_content_blocked(conversation_id)` returns the canned
# MessageResponse-shaped dict A10 rewrites the body with.
# `count_toward_paywall=False` per E4.
from app.safety.canned_responses import adult_content_blocked


GUARDED_PATH: Final[str] = "/v1/turn"


# ===========================================================================
# Rule set — Phase-1 NSFW keyword list (intentionally minimal)
# ===========================================================================

# Phase-1 keyword list. Day-5+ replaces this with the real
# `yral-rishi-agent-content-safety-and-moderation` RPC + the
# `influencer.is_nsfw` routing decision per A10's full text. The
# Day-3 list is deliberately SHORT — enough to test the dispatch
# path + catch obviously-NSFW outputs, but not exhaustive (that's
# Phase-2's content-safety service's job).
#
# Note: the `nsfw test marker` entry exists so the test suite can
# rig the handler's stub content to a flagged string without
# requiring crude content in the test file itself.
_ADULT_CONTENT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bexplicit\s+sexual\s+content\b", re.IGNORECASE),
    re.compile(r"\bgraphic\s+violence\b", re.IGNORECASE),
    re.compile(r"\bnsfw\s+test\s+marker\b", re.IGNORECASE),
)


_REASON_NSFW_KEYWORD: Final[str] = "a10_adult_content_keyword"


_log = logging.getLogger("app.middleware.a10_adult_content_filter")


def _match_nsfw(content: str) -> str | None:
    """Return a reason code if `content` matches any A10 pattern, else None.

    WHAT: walks the NSFW pattern list; returns the reason code on
          FIRST match.
    WHEN: called once per /v1/turn response inside `dispatch()` AFTER
          `call_next` returns.
    WHY:  isolated so unit tests can exercise the matcher directly.
    """
    for pattern in _ADULT_CONTENT_PATTERNS:
        if pattern.search(content):
            return _REASON_NSFW_KEYWORD

    return None


# ===========================================================================
# Middleware
# ===========================================================================


class A10AdultContentFilterMiddleware(BaseHTTPMiddleware):
    """Innermost safety layer — inspects the handler's response and
    rewrites NSFW content with a canned safety reply.

    WHAT: BaseHTTPMiddleware whose `dispatch()` calls `call_next`,
          drains the response body, parses + inspects the `content`
          field, and either passes through unchanged or rewrites to
          a canned safety reply with `count_toward_paywall=False`.
    WHEN: invoked once per request by the FastAPI middleware chain,
          AFTER H5 and H4 (request side).
    WHY:  output-side safety — even with clean user input, the LLM
          (Day-5+) can drift into NSFW territory; A10 is the last
          gate before the response leaves the orchestrator.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # Path filter — only POST /v1/turn is in scope.
        if request.url.path != GUARDED_PATH:
            return await call_next(request)

        # Gate-respect — see h5_prompt_injection.py file header for
        # the full rationale. Day-6 also reads the Day-5
        # `enable_run_turn_real_llm` flag.
        settings = get_settings()
        gate_closed = (
            settings.environment == "production"
            or not (
                settings.enable_run_turn_real_llm
                or settings.enable_run_turn_stub
            )
        )
        if gate_closed:
            return await call_next(request)

        record("A10_entry")

        # Run the inner chain (H5/H4 already passed since we're here;
        # this call hits the handler unless those layers short-circuit).
        response = await call_next(request)

        # Synthetic "handler" marker — see file header for rationale.
        record("handler")

        # Only inspect 200 JSON responses. 422 (Pydantic), 503 (gate),
        # 500 (unhandled exception) all pass through unmodified —
        # those are different failure surfaces, not A10's concern.
        if response.status_code != 200:
            record("A10_exit")
            return response

        # Drain the response body iterator. BaseHTTPMiddleware wraps
        # the inner response in a streaming iterator, so we collect
        # all chunks here before parsing. For Day-2 stub + Day-3
        # canned replies the body is a single small JSON object —
        # the iteration finishes in one chunk.
        body_chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            body_chunks.append(chunk)
        body = b"".join(body_chunks)

        # Try to parse as JSON. Non-JSON or malformed bodies pass
        # through unchanged (we can't safely inspect them).
        try:
            payload = json.loads(body)
            content = payload.get("content", "") if isinstance(payload, dict) else ""
        except (json.JSONDecodeError, AttributeError):
            record("A10_exit")
            return self._rebuild_response(response, body)

        # Run the matcher on the content field. No match → return the
        # response as-is (rebuilt from the drained body bytes).
        reason = _match_nsfw(content)
        if reason is None:
            record("A10_exit")
            return self._rebuild_response(response, body)

        # Match — emit the canned NSFW reply. conversation_id comes
        # from the inspected payload so the rewritten response stays
        # correlated with the request the handler was answering.
        conversation_id = payload.get("conversation_id", "") if isinstance(payload, dict) else ""

        _log.warning(
            "a10_blocked",
            extra={
                "safety_layer": "A10",
                "reason": reason,
                "conversation_id": conversation_id,
                "original_content_length": len(content),
            },
        )

        new_response = JSONResponse(
            content=adult_content_blocked(conversation_id),
            status_code=200,
        )
        new_response.headers["X-Safety-Decision"] = "A10"
        new_response.headers["X-Safety-Reason"] = reason

        record("A10_exit")
        return new_response

    @staticmethod
    def _rebuild_response(original: Response, body: bytes) -> Response:
        """Rebuild a Response object after draining its body iterator.

        WHAT: returns a new `Response` carrying the drained body bytes,
              the original status code, the original media type, and
              the original headers MINUS `content-length` (Starlette
              recomputes content-length on construction from the new
              body).
        WHEN: called when A10 inspected the body but decided NOT to
              rewrite (pass-through happy path or unparseable body).
        WHY:  the original response's body_iterator is exhausted after
              we drained it; returning the original would yield an
              empty body to the client. Rebuilding with the cached
              bytes preserves the client-visible response shape.
        """
        # Drop content-length so Starlette recomputes it for the new
        # body. Other headers (content-type, X-Request-Id, etc.) carry
        # forward unchanged.
        headers = {
            k: v for k, v in original.headers.items()
            if k.lower() != "content-length"
        }
        return Response(
            content=body,
            status_code=original.status_code,
            headers=headers,
            media_type=original.media_type,
        )


# ===========================================================================
# RELATED FILES:
#   __init__.py                — package marker + visual ASCII chain
#   _safety_audit.py           — audit-trail ContextVar + record() helper
#                                (A10 also records the synthetic "handler"
#                                marker between its entry + exit)
#   h5_prompt_injection.py     — outer safety layer (request-side)
#   h4_crisis_detection.py     — middle safety layer (request-side)
#   ../safety/canned_responses.py
#                              — `adult_content_blocked()` returns the canned
#                                MessageResponse when A10 rewrites the response
#   ../config.py               — `environment` + `enable_run_turn_stub`
#                                settings the gate-respect check reads
#   ../run_turn.py             — Day-2 handler whose response this layer
#                                inspects; Day-5+ LLM swap flows through
#                                this same layer unchanged
#   ../main.py                 — mounts this middleware via `add_middleware()`
#   ../../tests/test_safety_stack.py
#                              — A10-blocked path (monkeypatches STUB_CONTENT
#                                to "nsfw test marker") + order-verification
# ===========================================================================
