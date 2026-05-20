# ---------------------------------------------------------------------------
# h4_crisis_detection.py — Day-3 H4 crisis-detection middleware.
#
# ⭐ START HERE: this module exports `H4CrisisDetectionMiddleware`, the
# MIDDLE safety layer in the H5 → H4 → A10 → handler chain. When the
# user-message body contains self-harm / suicide / mental-health-crisis
# language, the middleware short-circuits the route with HTTP 200 + a
# canned crisis-response (per
# `app/safety/canned_responses.py::crisis_response`) and the run_turn
# handler is never called.
#
# WHY OVER-ROUTING IS THE EXPLICIT BIAS HERE
# Per the Session-4 agent definition Day-3 plan verbatim: "Tune crisis-
# detection thresholds (H4) using real Langfuse traces — false-positive
# rate target < 5%, false-negative rate target ~0% (lean toward over-
# routing to Claude on uncertain cases)." Day-3 ships a deliberately
# WIDE keyword net — better to surface a helpline placeholder to
# someone who didn't strictly need one than to MISS someone who did.
# Phase-2 hardening narrows the false-positive rate while keeping
# false-negatives at zero.
#
# WHY KEYWORDS NOT EMBEDDINGS FOR PHASE 1
# Same A2.1 argument as H5's regex choice: a keyword list is one file
# to edit + zero infra. Day-5+ promotion to embeddings or a fine-tuned
# classifier replaces `_CRISIS_PATTERNS` here without touching dispatch.
#
# WHY THE STUB COPY IS OBVIOUSLY-PLACEHOLDER
# Per the Day-3 directive verbatim: "must be obviously a stub, not a
# wrong helpline number." A wrong helpline number is more harmful than
# a bracketed-string marker for someone in crisis. Product (Day-3.5)
# owns the real copy + locale-aware helpline routing.
#
# THE GATE-RESPECT PATTERN
# Same as H5 (see `h5_prompt_injection.py` file header) — when EITHER
# `environment == "production"` or `enable_run_turn_stub` is false,
# this middleware passes through so the handler's 503-emission fires
# unchanged. Avoids the "leak via safety bypass" attack vector.
#
# WHY H4 SITS INSIDE H5
# Order matters: H5 handles ADVERSARIAL input (deliberate jailbreaks).
# H4 handles VULNERABLE input (genuine crisis signals). A user could
# include BOTH (a jailbreak phrase combined with self-harm language);
# in that case we treat them as adversarial first per H5's heuristic
# bias, which Day-5 product review may reverse if the data argues for
# crisis-routing all such cases.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib `json` — parses the request body so the H4 crisis-pattern
# set can scan the user_message text.
import json

# stdlib logger — emits structured fields the H6 redactor in
# `app/logging.py` knows about (safety_layer / reason /
# conversation_id / user_message_length). NEVER the user_message
# text itself.
import logging

# stdlib regex — compiled-once `re.Pattern` objects backing the H4
# crisis-language pattern set. Pre-compiled so per-request matching
# is hot-path cheap.
import re

# `Final` marks the crisis-pattern set + the guarded-path constant
# as immutable. A future pattern tuning bump shows up clearly in
# `git blame`.
from typing import Final

# Starlette's `BaseHTTPMiddleware` is the base class for the
# request/response wrapping middleware shape we use.
from starlette.middleware.base import BaseHTTPMiddleware

# `Request` is the typed wrapper Starlette hands to dispatch().
# Used for `request.url.path` (route gating) + `await request.body()`
# (the user message we scan; H5 already read+replayed it upstream).
from starlette.requests import Request

# `JSONResponse` builds the H4 canned-crisis-response reply.
# `Response` is the typed return shape of dispatch() either way.
from starlette.responses import JSONResponse, Response

# `ASGIApp` is the type the constructor receives. Annotation only.
from starlette.types import ASGIApp

# `get_settings()` reads the typed Settings singleton — needed for
# the gate-respect check.
from app.config import get_settings

# `record(...)` appends to the `SAFETY_AUDIT_TRAIL` ContextVar so
# the order-verification test can assert chain execution.
from app.middleware._safety_audit import record

# `validate_required_headers(request)` enforces the round-4 X-User-Id
# + X-Idempotency-Key + UUID-format gate (Codex PR-#112 round-3
# BLOCKER 1 closure). H5 (outer) already runs this gate; H4 runs it
# again for defence-in-depth — same check, same envelope shape, so
# a future reorder of `add_middleware` calls in main.py can't
# silently regress the gate.
from app.middleware._header_validation import validate_required_headers

# `crisis_response(conversation_id, idempotency_key)` returns the
# canned helpline response dict; obviously-stub placeholder per
# the directive. `count_toward_paywall=False` per E4. idempotency_key
# threaded for UUID5-deterministic `id` per key+layer.
from app.safety.canned_responses import crisis_response

# F10 idempotency helpers — Codex PR-#112 round-5 BLOCKER 2 closure
# (same shape as the H5 wiring). H4 short-circuits MUST honour F10's
# fingerprint-mismatch + byte-identical-replay contracts.
from app.idempotency import (
    acquire_or_check,
    compute_idempotency_key,
    compute_request_fingerprint,
    mark_complete,
)


GUARDED_PATH: Final[str] = "/v1/turn"


# ===========================================================================
# Rule set — Phase-1 keyword-based crisis detection (false-positive bias)
# ===========================================================================

# Patterns are word-boundary anchored so "I'm reading a book about
# suicide prevention" still matches (the standalone token "suicide"
# is enough) — over-routing is intentional per the agent definition's
# false-negative-target-~0% bias.
#
# Phase-2 follow-up: replace this list with embeddings + similarity
# threshold, or a fine-tuned crisis-detection classifier. The dispatch
# path keeps the same `_match_crisis()` return contract.
_CRISIS_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bsuicid(e|al)\b", re.IGNORECASE),
    re.compile(r"\bkill\s+(myself|me)\b", re.IGNORECASE),
    re.compile(r"\bend\s+my\s+life\b", re.IGNORECASE),
    re.compile(r"\bself[\s-]?harm(ing)?\b", re.IGNORECASE),
    re.compile(r"\bhurt(ing)?\s+myself\b", re.IGNORECASE),
    re.compile(r"\bwant\s+to\s+die\b", re.IGNORECASE),
    re.compile(r"\bno\s+reason\s+to\s+live\b", re.IGNORECASE),
    re.compile(r"\bcut(ting)?\s+myself\b", re.IGNORECASE),
)


# One reason code — Day-3 doesn't distinguish sub-patterns. Phase-2
# classifier may return a finer-grained category (suicidal-ideation
# vs self-harm vs general-distress, etc.).
_REASON_CRISIS_LANGUAGE: Final[str] = "h4_crisis_language"


# Structured logger. Per H6 we log MATCH-OCCURRED + length, never the
# raw user message (especially important for crisis content).
_log = logging.getLogger("app.middleware.h4_crisis_detection")


def _match_crisis(user_message: str) -> str | None:
    """Return a reason code if `user_message` matches any H4 pattern, else None.

    WHAT: walks the crisis keyword regex list; returns the reason code
          on FIRST match.
    WHEN: called once per /v1/turn request inside `dispatch()`.
    WHY:  isolated so unit tests can exercise the matcher without
          spinning up the full middleware chain.
    """
    for pattern in _CRISIS_PATTERNS:
        if pattern.search(user_message):
            return _REASON_CRISIS_LANGUAGE

    return None


# ===========================================================================
# Middleware
# ===========================================================================


class H4CrisisDetectionMiddleware(BaseHTTPMiddleware):
    """Middle safety layer — routes crisis-signal inputs to a helpline
    placeholder reply before the handler is reached.

    WHAT: BaseHTTPMiddleware whose `dispatch()` inspects POST /v1/turn
          request bodies for crisis-language keywords + short-circuits
          with a canned 200 response on match.
    WHEN: invoked once per request by the FastAPI middleware chain,
          AFTER H5 and BEFORE A10 (request side).
    WHY:  protects vulnerable users from receiving an unrelated stub
          / LLM reply when they're signalling a mental-health crisis.
          Default helpline-placeholder is intentionally a stub —
          Product (Day-3.5) replaces with real copy + locale routing.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # Path filter — only POST /v1/turn is in scope.
        if request.url.path != GUARDED_PATH:
            return await call_next(request)

        # Gate-respect — see h5_prompt_injection.py file header for
        # the full rationale. Day-6 also reads the Day-5
        # `enable_run_turn_real_llm` flag (both flags allow the
        # handler to run; only ALL-off triggers the gate close).
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

        # Codex PR-#112 round-3 BLOCKER 1 — same header-gate as H5
        # (defence-in-depth; H5 should already have validated, but
        # symmetric check survives any future middleware reorder).
        header_error_response = validate_required_headers(request)
        if header_error_response is not None:
            return header_error_response

        record("H4_entry")

        # Read the body. H5 (outer) already read + replayed it, so
        # `request.body()` returns the cached `_body` for free. Without
        # H5 first having replayed receive, this call would still
        # work (returns cached `_body` if H5 didn't run; new read if
        # we're somehow alone in the chain) — defence-in-depth.
        body_bytes = await request.body()

        try:
            payload = json.loads(body_bytes)
            user_message = payload.get("user_message", "")
        except (json.JSONDecodeError, AttributeError):
            payload = None
            user_message = ""

        # Codex PR-#112 round-2 CONCERN: non-string user_message would
        # crash the matcher (re.search on int → TypeError → 500 before
        # Pydantic emits its 422). Mirror of the H5 guard above. Drop
        # to "" so the matcher passes through harmlessly + downstream
        # Pydantic returns the documented validation envelope.
        if not isinstance(user_message, str):
            user_message = ""

        reason = _match_crisis(user_message)
        if reason is not None:
            conversation_id = (
                payload.get("conversation_id", "") if payload else ""
            )

            _log.warning(
                "h4_blocked",
                extra={
                    "safety_layer": "H4",
                    "reason": reason,
                    "conversation_id": conversation_id,
                    "user_message_length": len(user_message),
                },
            )

            # Codex PR-#112 round-5 BLOCKER 2 — full F10 on the safety
            # short-circuit path. Same shape as H5's wiring above.
            user_id = request.headers["x-user-id"]
            idempotency_key = request.headers["x-idempotency-key"]

            redis_key = compute_idempotency_key(
                user_id=user_id, idempotency_key=idempotency_key,
            )
            fingerprint = compute_request_fingerprint(payload or {})

            decision = await acquire_or_check(
                redis_key=redis_key, fingerprint=fingerprint,
            )

            if decision.state == "replay_done":
                return JSONResponse(
                    content=decision.cached_response,
                    status_code=200,
                    headers={
                        "X-Safety-Decision": "H4",
                        "X-Safety-Reason": reason,
                    },
                )

            if decision.state == "fingerprint_mismatch":
                return JSONResponse(
                    status_code=409,
                    content={
                        "success": False,
                        "msg": (
                            "X-Idempotency-Key was reused with a different "
                            "request body; mobile clients must generate a "
                            "fresh key per distinct request payload."
                        ),
                        "error": "idempotency_key_reused_with_different_body",
                        "data": None,
                    },
                )

            if decision.state == "in_flight_timeout":
                return JSONResponse(
                    status_code=503,
                    content={
                        "success": False,
                        "msg": (
                            "Another request with this X-Idempotency-Key "
                            "is still in flight; retry after a short backoff."
                        ),
                        "error": "idempotency_in_flight",
                        "data": None,
                    },
                )

            # decision.state == "acquired" — build canned + cache.
            canned_payload = crisis_response(
                conversation_id, idempotency_key=idempotency_key,
            )
            try:
                await mark_complete(
                    redis_key=redis_key,
                    fingerprint=fingerprint,
                    response_payload=canned_payload,
                )
            except Exception as cache_write_failure:
                _log.error(
                    "h4_mark_complete_failed: %s",
                    cache_write_failure,
                    extra={
                        "conversation_id": conversation_id,
                        "reason": reason,
                        "failure_type": type(cache_write_failure).__name__,
                    },
                    exc_info=True,
                )

            response = JSONResponse(
                content=canned_payload,
                status_code=200,
            )
            response.headers["X-Safety-Decision"] = "H4"
            response.headers["X-Safety-Reason"] = reason

            record("H4_exit")
            return response

        # No match — propagate to A10.
        response = await call_next(request)

        record("H4_exit")
        return response


# ===========================================================================
# RELATED FILES:
#   __init__.py                — package marker + visual ASCII chain
#   _body_replay.py            — used by H5 (outer); H4 reads cached _body
#   _safety_audit.py           — audit-trail ContextVar + record() helper
#   h5_prompt_injection.py     — outer safety layer this layer sits inside
#   a10_adult_content_filter.py         — inner safety layer this layer passes to
#   ../safety/canned_responses.py
#                              — `crisis_response()` returns the canned
#                                MessageResponse when H4 short-circuits
#   ../config.py               — `environment` + `enable_run_turn_stub`
#                                settings the gate-respect check reads
#   ../run_turn.py             — Day-2 handler this layer protects
#   ../main.py                 — mounts this middleware via `add_middleware()`
#   ../../tests/test_safety_stack.py
#                              — H4-blocked path + order-verification tests
# ===========================================================================
