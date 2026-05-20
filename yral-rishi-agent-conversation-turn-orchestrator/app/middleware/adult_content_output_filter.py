# ---------------------------------------------------------------------------
# adult_content_output_filter.py — Day-3 adult_content adult_content output-side filter middleware.
#
# ⭐ START HERE: this module exports `AdultContentOutputFilterMiddleware`, the
# INNERMOST safety layer in the H5 → H4 → adult_content → handler chain. Unlike
# H5 + H4 (which inspect the request body BEFORE the handler runs),
# adult_content inspects the RESPONSE body AFTER the handler returns. If the
# response's `content` field matches the adult_content rule set, adult_content rewrites
# the response with a canned safety reply (per
# `app/safety/canned_responses.py::adult_content_blocked`) + flips
# `count_toward_paywall` to False.
#
# WHY adult_content IS OUTPUT-SIDE
# Per the Day-3 directive verbatim: "adult_content — adult_content filter (output-side).
# For Day-3 stub: runs on the handler's RETURN value (the stub
# MessageResponse.content). Checks against an adult_content keyword list."
# Day-5+ real LLM enablement feeds actual model output through this
# layer unchanged — same dispatch path, same canned reply, just with
# real content as the inspected payload instead of the Day-2 stub.
#
# WHY adult_content ALSO RECORDS THE "handler" MARKER
# Per the Day-3 directive's order-verification spec:
#   assert order is [H5_entry, H4_entry, adult_content_entry, handler,
#                    adult_content_exit, H4_exit, H5_exit]
# AND per the directive's scope guardrail: "ONLY new middleware files +
# main.py wiring. Do NOT modify run_turn.py or models/turn.py." The
# run_turn handler itself is out-of-scope to modify, so it cannot
# append "handler" to the audit trail directly. Instead, adult_content — which
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
# `enable_run_turn_stub` is false, adult_content passes through without
# inspecting the response so the handler's 503-emission fires
# unchanged. Avoids "leak via safety bypass".
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib `json` — parses the response body so the adult_content keyword set
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
# `request.url.path` (route gating). adult_content doesn't read the request
# body (it's an OUTPUT-side filter).
from starlette.requests import Request

# `JSONResponse` builds the adult_content canned reply when a match fires.
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

# `adult_content_blocked(conversation_id, idempotency_key)` returns
# the canned MessageResponse-shaped dict adult_content rewrites the body with.
# `count_toward_paywall=False` per E4. The idempotency_key arg makes
# the canned `id` deterministic across retries (Codex PR-#112
# round-4 BLOCKER 2).
from app.safety.canned_responses import adult_content_blocked

# F10 idempotency helpers — Codex PR-#112 round-4 BLOCKER 1 closure:
# adult_content calls `mark_complete` AFTER rewriting the response body so the
# cached payload in Redis matches the client-visible canned reply.
# Without this, the handler's earlier `mark_complete` left the
# unfiltered LLM output in the F10 cache; a retry's `replay_done`
# would return the unfiltered version (adult_content would rewrite again on
# the way out, but other readers of the cache — log dumps, future
# audit features — would see the unfiltered cached payload).
from app.idempotency import (
    compute_idempotency_key,
    compute_request_fingerprint,
    mark_complete,
)

# stdlib JSON — needed to parse the request body for fingerprint
# recomputation. H5 already read+replayed the body, so this is a
# cached read (no new I/O).
import json as _json_for_fingerprint


GUARDED_PATH: Final[str] = "/v1/turn"


# ===========================================================================
# Rule set — Phase-1 adult_content keyword list (intentionally minimal)
# ===========================================================================

# Phase-1 keyword list. Day-5+ replaces this with the real
# `yral-rishi-agent-content-safety-and-moderation` RPC + the
# `influencer.is_nsfw` routing decision per A10's full text. The
# Day-3 list is deliberately SHORT — enough to test the dispatch
# path + catch obviously-adult_content outputs, but not exhaustive (that's
# Phase-2's content-safety service's job).
#
# Note: the `adult_content test marker` entry exists so the test suite can
# rig the handler's stub content to a flagged string without
# requiring crude content in the test file itself.
_ADULT_CONTENT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bexplicit\s+sexual\s+content\b", re.IGNORECASE),
    re.compile(r"\bgraphic\s+violence\b", re.IGNORECASE),
    re.compile(r"\badult-content\s+test\s+marker\b", re.IGNORECASE),
)


_REASON_ADULT_CONTENT_KEYWORD: Final[str] = "adult_content_keyword"


_log = logging.getLogger("app.middleware.adult_content_output_filter")


def _match_adult_content(content: str) -> str | None:
    """Return a reason code if `content` matches any adult_content pattern, else None.

    WHAT: walks the adult_content pattern list; returns the reason code on
          FIRST match.
    WHEN: called once per /v1/turn response inside `dispatch()` AFTER
          `call_next` returns.
    WHY:  isolated so unit tests can exercise the matcher directly.
    """
    # Walk every compiled adult_content pattern; first-hit short-
    # circuit (cheap micro-opt + Day-5+ real LLM output paths benefit
    # from any saved regex scans on the hot path).
    for pattern in _ADULT_CONTENT_PATTERNS:
        # `re.search` is truthy on match, None on no-match. We don't
        # capture groups — just need the boolean.
        if pattern.search(content):
            # Single shared reason code; matches the X-Safety-Reason
            # header value the response carries.
            return _REASON_ADULT_CONTENT_KEYWORD

    return None


# ===========================================================================
# Middleware
# ===========================================================================


class AdultContentOutputFilterMiddleware(BaseHTTPMiddleware):
    """Innermost safety layer — inspects the handler's response and
    rewrites adult_content content with a canned safety reply.

    WHAT: BaseHTTPMiddleware whose `dispatch()` calls `call_next`,
          drains the response body, parses + inspects the `content`
          field, and either passes through unchanged or rewrites to
          a canned safety reply with `count_toward_paywall=False`.
    WHEN: invoked once per request by the FastAPI middleware chain,
          AFTER H5 and H4 (request side).
    WHY:  output-side safety — even with clean user input, the LLM
          (Day-5+) can drift into adult_content territory; adult_content is the last
          gate before the response leaves the orchestrator.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # Path filter — only POST /v1/turn is in scope.
        if request.url.path != GUARDED_PATH:
            return await call_next(request)

        # Gate-respect — see h5_prompt_injection.py for the full
        # rationale + the round-6 production-bypass fix.
        settings = get_settings()
        gate_closed = not (
            settings.enable_run_turn_real_llm
            or settings.enable_run_turn_stub
        )
        if gate_closed:
            return await call_next(request)

        record("adult_content_entry")

        # Run the inner chain (H5/H4 already passed since we're here;
        # this call hits the handler unless those layers short-circuit).
        response = await call_next(request)

        # Synthetic "handler" marker — see file header for rationale.
        record("handler")

        # Only inspect 200 JSON responses. 422 (Pydantic), 503 (gate),
        # 500 (unhandled exception) all pass through unmodified —
        # those are different failure surfaces, not adult_content's concern.
        if response.status_code != 200:
            record("adult_content_exit")
            return response

        # Codex PR-#112 round-3 CONCERN — content-type guard. adult_content's
        # drain-and-rebuild approach assumes the response is a small
        # non-streaming JSON body (the contract on the v1 path per
        # the agent definition's "JSON, NOT SSE" rule). If a future
        # route reuses this middleware against a StreamingResponse /
        # SSE path / non-JSON body, the buffering would either break
        # streaming semantics or fail-open silently. Explicit guard:
        # only inspect bodies whose Content-Type starts with
        # `application/json`; anything else passes through unmodified
        # so SSE / file-stream / etc. paths are NOT silently
        # buffered + rebuilt. The streaming-safe moderation design
        # for `/v2/turn-stream` (when it lands) is a separate piece.
        content_type = response.headers.get("content-type", "")
        if not content_type.lower().startswith("application/json"):
            record("adult_content_exit")
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
            record("adult_content_exit")
            return self._rebuild_response(response, body)

        # Run the matcher on the content field. No match → return the
        # response as-is (rebuilt from the drained body bytes).
        reason = _match_adult_content(content)
        if reason is None:
            record("adult_content_exit")
            return self._rebuild_response(response, body)

        # Match — emit the canned adult_content reply. conversation_id comes
        # from the inspected payload so the rewritten response stays
        # correlated with the request the handler was answering.
        conversation_id = payload.get("conversation_id", "") if isinstance(payload, dict) else ""

        _log.warning(
            "a10_blocked",
            extra={
                "safety_layer": "adult_content",
                "reason": reason,
                "conversation_id": conversation_id,
                "original_content_length": len(content),
            },
        )

        # Read the validated X-User-Id + X-Idempotency-Key from the
        # request. The handler already validated these (adult_content's call_next
        # only returned 200 if the handler accepted the request); the
        # KeyError-on-missing pattern would only fire on a future
        # regression where adult_content sees a 200 from a handler that
        # bypassed the gate. Defensive `.get(..., "")` would mask
        # such a regression — better to surface as a 500.
        user_id = request.headers["x-user-id"]
        idempotency_key = request.headers["x-idempotency-key"]

        # Build the canned reply with the idempotency_key threaded
        # through so `id` is deterministic on retry.
        canned_payload = adult_content_blocked(
            conversation_id, idempotency_key=idempotency_key,
        )

        # Codex PR-#112 round-4 BLOCKER 1 — overwrite the F10 cache
        # with the canned reply so the stored payload matches the
        # client-visible body. Without this, the handler's earlier
        # `mark_complete` cached the unfiltered LLM output; adult_content would
        # rewrite on every retry (correct user-visible behaviour) but
        # the cached payload would remain unfiltered (operator-side
        # leak surface via direct Redis reads / log dumps).
        #
        # Read body bytes from `request.state.cached_request_body_bytes`
        # — Starlette's BaseHTTPMiddleware builds a NEW Request
        # instance per layer (each with its own `_body` cache); after
        # the handler consumed the body via its Pydantic parse,
        # `await request.body()` on adult_content's Request raises
        # "Stream consumed". H5's `read_and_replay_body` helper stashes
        # the bytes on `request.state` (scope-shared) precisely so
        # this post-call_next read works.
        try:
            request_body_bytes = request.state.cached_request_body_bytes
            request_payload = _json_for_fingerprint.loads(request_body_bytes)
            redis_key = compute_idempotency_key(
                user_id=user_id, idempotency_key=idempotency_key,
            )
            fingerprint = compute_request_fingerprint(request_payload)
            await mark_complete(
                redis_key=redis_key,
                fingerprint=fingerprint,
                response_payload=canned_payload,
            )
        except Exception as cache_overwrite_failure:
            # Best-effort — if the cache overwrite fails, the client
            # still sees the canned reply (adult_content's primary job). The
            # operator-side concern (cached unfiltered payload) is
            # logged so a Sentry alert can spot the gap.
            # `exc_info=True` surfaces the traceback into the log
            # record so triage doesn't need a code-side reproduce.
            _log.error(
                "a10_cache_overwrite_failed: %s",
                cache_overwrite_failure,
                extra={
                    "conversation_id": conversation_id,
                    "reason": reason,
                    "failure_type": type(cache_overwrite_failure).__name__,
                },
                exc_info=True,
            )

        new_response = JSONResponse(
            content=canned_payload,
            status_code=200,
        )
        new_response.headers["X-Safety-Decision"] = "adult_content"
        new_response.headers["X-Safety-Reason"] = reason

        record("adult_content_exit")
        return new_response

    @staticmethod
    def _rebuild_response(original: Response, body: bytes) -> Response:
        """Rebuild a Response object after draining its body iterator.

        WHAT: returns a new `Response` carrying the drained body bytes,
              the original status code, the original media type, and
              the original headers MINUS `content-length` (Starlette
              recomputes content-length on construction from the new
              body).
        WHEN: called when adult_content inspected the body but decided NOT to
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
#                                (adult_content also records the synthetic "handler"
#                                marker between its entry + exit)
#   h5_prompt_injection.py     — outer safety layer (request-side)
#   h4_crisis_detection.py     — middle safety layer (request-side)
#   ../safety/canned_responses.py
#                              — `adult_content_blocked()` returns the canned
#                                MessageResponse when adult_content rewrites the response
#   ../config.py               — `environment` + `enable_run_turn_stub`
#                                settings the gate-respect check reads
#   ../run_turn.py             — Day-2 handler whose response this layer
#                                inspects; Day-5+ LLM swap flows through
#                                this same layer unchanged
#   ../main.py                 — mounts this middleware via `add_middleware()`
#   ../../tests/test_safety_stack.py
#                              — adult_content-blocked path (monkeypatches STUB_CONTENT
#                                to "adult_content test marker") + order-verification
# ===========================================================================
