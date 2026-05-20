# ---------------------------------------------------------------------------
# h5_prompt_injection.py — Day-3 H5 prompt-injection defense middleware.
#
# ⭐ START HERE: this module exports `H5PromptInjectionMiddleware`, a
# BaseHTTPMiddleware subclass mounted in `app/main.py` as the OUTERMOST
# safety layer in front of `POST /v1/turn`. When the user-message body
# matches a known jailbreak / role-override / base64-blob pattern, the
# middleware short-circuits the route with HTTP 200 + a canned safety
# response (per `app/safety/canned_responses.py::prompt_injection_blocked`)
# and the run_turn handler is never called.
#
# WHY H5 LIVES IN MIDDLEWARE NOT IN THE HANDLER
# Per the Session-4 agent definition's Day-3 plan + the verbatim Day-3
# directive: "safety stack BEFORE any real LLM call." Putting H5 (and
# H4, A10) in middleware means Day-5's real-LLM swap inside the handler
# automatically inherits the safety stack — the LLM never sees a
# jailbreak input because the middleware short-circuited it first.
#
# WHY RULE-BASED REGEX FOR PHASE 1
# Per the agent definition Day-3 plan verbatim: "Classifier can be a
# small fine-tuned model OR a rule-based regex matcher for Phase 1;
# upgrade to ML classifier in Phase 2." Day 3's rule set is
# intentionally conservative — common jailbreak phrases + base64-blob
# size cap. False-positive rate is acceptable here (a refused turn is
# safer than a leaked turn) since the user can rephrase. Phase 2's
# ML classifier replaces this file's `_INJECTION_PATTERNS` constant
# without touching the dispatch logic.
#
# THE GATE-RESPECT PATTERN (see file header of `a10_adult_content_filter.py` for
# the same pattern in the output-side layer)
# Per the Day-3 directive verbatim: "Flag-off behaviour unchanged:
# env=production OR enable_run_turn_stub=false still 503s before
# middleware fires (no leak via safety bypass)." We respect the same
# two-gate logic the handler uses (env != production AND
# enable_run_turn_stub=true). When EITHER gate is closed, this
# middleware passes through without inspecting the body so the
# handler's own 503-emission fires. A jailbreaker sending bad input to
# a production environment sees the same 503 a clean message would
# see — no information leakage about which inputs trigger the safety
# stack.
#
# WHY THE PATH FILTER
# The handler at `POST /v1/turn` is the only route the safety stack
# protects today. Other routes (`/docs`, `/openapi.json`, future
# health endpoints) bypass H5 entirely. Path filtering is the
# cheapest possible early-out for "not for me" requests.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib `json` — parses the request body so the H5 regex set can
# scan the user_message text without echoing the bytes through the
# middleware chain twice.
import json

# stdlib logger — emits structured fields the H6 PII-allowlist
# redactor in `app/logging.py` knows about (safety_layer / reason /
# conversation_id / user_message_length). NEVER logs the
# user_message content itself.
import logging

# stdlib regex — compiled-once `re.Pattern` objects backing the H5
# injection-pattern set. Pre-compilation puts the regex tree on the
# hot path's read-side (per-request matching) instead of paying
# compile cost every turn.
import re

# `Final` marks the regex set + the guarded-path constant as
# immutable. A future pattern bump shows up clearly in `git blame`.
from typing import Final

# `sentry_sdk` — Codex PR-#112 BLOCKER 2 closure: H5 verbatim
# requires every prompt-injection block to log a Sentry event
# with `type=prompt_injection`. The stdlib logger above is
# operator-side; this SDK call is the H5-spec'd Sentry-side event.
import sentry_sdk

# Starlette's `BaseHTTPMiddleware` is the base class for ASGI
# middleware that wraps the request/response pair. Subclasses
# override `dispatch()` to inspect the request + decide
# whether to call_next(request) or short-circuit.
from starlette.middleware.base import BaseHTTPMiddleware

# `Request` is the typed wrapper Starlette hands to dispatch().
# Used for `request.url.path` (route gating) + `await request.body()`
# (the user message we scan).
from starlette.requests import Request

# `JSONResponse` builds the H5 canned-block reply with the right
# Content-Type + the X-Safety-* headers. `Response` is the parent
# type our dispatch returns from EITHER the short-circuit path
# or the propagated `call_next` path.
from starlette.responses import JSONResponse, Response

# `ASGIApp` is the type the middleware constructor receives + hands
# back to Starlette. Annotation only — no behaviour.
from starlette.types import ASGIApp

# `get_settings()` reads the typed Settings singleton — needed for
# the gate-respect check (env + the two Day-5 path flags).
from app.config import get_settings

# `read_and_replay_body()` reads the request body ONCE + replays it
# into `request._receive` so H4 + A10 + the handler can re-read.
# Without this helper, the first `await request.body()` would drain
# the body for everyone downstream.
from app.middleware._body_replay import read_and_replay_body

# `validate_required_headers(request)` enforces the round-4 X-User-Id
# + X-Idempotency-Key + UUID-format gate (Codex PR-#112 round-3
# BLOCKER 1 closure). Returns the 400 envelope JSONResponse if any
# header is missing/malformed; None on success. We run this BEFORE
# the body-replay + pattern-match so a header-malformed jailbreak
# gets the documented 400 — not the 200 canned reply that would
# leak the safety stack's existence to a header-omitting attacker.
from app.middleware._header_validation import validate_required_headers

# `record(...)` appends to the `SAFETY_AUDIT_TRAIL` ContextVar so
# the order-verification test can assert the chain executed in the
# documented LIFO sequence. No-op in production.
from app.middleware._safety_audit import record

# `prompt_injection_blocked(conversation_id, idempotency_key)` returns
# the canned MessageResponse-shaped dict ("I can't help with that.")
# with `count_toward_paywall=False` per E4. Threaded with the
# idempotency_key so the response `id` is UUID5-deterministic per
# key+layer.
from app.safety.canned_responses import prompt_injection_blocked

# F10 idempotency helpers — Codex PR-#112 round-5 BLOCKER 2 closure:
# safety short-circuits MUST honour F10's fingerprint-mismatch +
# byte-identical-replay contracts. Per the row's "Per-endpoint
# opt-out for truly stateless" affordance: safety-blocked replies
# don't run an LLM call (stateless), but they DO write to Redis
# via mark_complete so retries replay from cache + same-key-
# different-body returns 409 (catches client bugs the handler
# would otherwise catch).
from app.idempotency import (
    acquire_or_check,
    compute_idempotency_key,
    compute_request_fingerprint,
    mark_complete,
)


# Path this middleware guards. Anything else passes through.
GUARDED_PATH: Final[str] = "/v1/turn"


# ===========================================================================
# Rule set — Phase-1 rule-based detection
# ===========================================================================

# Compiled regex patterns for known prompt-injection shapes. Order
# doesn't matter (any match short-circuits). Patterns are anchored on
# word boundaries where appropriate so "I want to discuss the history
# of jailbreaks" does NOT trigger (no role-override verb adjacent).
#
# Phase-2 follow-up: replace this list with a small ML classifier call.
# This module's `_match_injection()` keeps the same return contract so
# the dispatch path doesn't change.
_INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # "ignore previous instructions" + synonyms.
    re.compile(
        r"\bignore\s+(previous|all|prior|earlier|above)\s+"
        r"(instructions?|prompts?|messages?|rules?)",
        re.IGNORECASE,
    ),
    # Direct system-prompt reveal probes (B4 vocab in *input* attack
    # patterns is fine — we are matching what attackers TYPE, not what
    # our code names things).
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
    # Soul-File reveal probes — same shape as system-prompt probes but
    # using YRAL product vocab. Defends against attackers who learn our
    # internal terminology from public commits.
    re.compile(r"\bsoul\s+file\b", re.IGNORECASE),
    # Role-override commands.
    re.compile(r"\byou\s+are\s+now\s+(a|an|the)\s+", re.IGNORECASE),
    re.compile(r"\bpretend\s+(you\s+are|to\s+be)\b", re.IGNORECASE),
    # Known jailbreak personas.
    re.compile(r"\b(dan|jailbreak)\s+mode\b", re.IGNORECASE),
    # Special-token injection (Anthropic/OpenAI/Gemini control tokens).
    re.compile(r"<\s*\|.*?\|\s*>"),
)


# Base64-blob threshold. Any continuous run of base64-charset characters
# longer than this triggers H5. Tuned to be longer than a long URL but
# shorter than a typical embedded payload.
_BASE64_BLOB_THRESHOLD: Final[int] = 200

_BASE64_BLOB_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"[A-Za-z0-9+/=]{{{_BASE64_BLOB_THRESHOLD},}}"
)


# Reason codes — emitted in the response header + log line so triage
# tools (Sentry, Langfuse Day-5+) can categorise blocks by sub-pattern.
_REASON_REGEX_MATCH: Final[str] = "h5_regex_match"
_REASON_BASE64_BLOB: Final[str] = "h5_base64_blob"


# Structured logger. Honours the H6 PII-redaction config in
# `app/logging.py`; we log MATCHED PATTERN INDEX, not the raw input,
# so the log line never carries user PII.
_log = logging.getLogger("app.middleware.h5_prompt_injection")


def _match_injection(user_message: str) -> str | None:
    """Return a reason code if `user_message` matches any H5 pattern, else None.

    WHAT: walks the regex list + base64-blob threshold check; returns
          on FIRST match (we don't categorise multi-trigger inputs
          beyond the first hit — Day-5+ classifier can do scoring).
    WHEN: called once per /v1/turn request inside `dispatch()`.
    WHY:  isolated so unit tests can exercise the matcher without
          spinning up the full FastAPI app + middleware chain.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(user_message):
            return _REASON_REGEX_MATCH

    if _BASE64_BLOB_PATTERN.search(user_message):
        return _REASON_BASE64_BLOB

    return None


# ===========================================================================
# Middleware
# ===========================================================================


class H5PromptInjectionMiddleware(BaseHTTPMiddleware):
    """Outermost safety layer — blocks known prompt-injection shapes
    before the request reaches H4 / A10 / the handler.

    WHAT: BaseHTTPMiddleware whose `dispatch()` inspects POST /v1/turn
          request bodies for jailbreak patterns + short-circuits with
          a canned 200 response on match.
    WHEN: invoked once per request by the FastAPI middleware chain.
    WHY:  defence-in-depth — prevents jailbreak attempts from reaching
          the LLM call (Day-5+); reduces the safety surface other
          layers must consider.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # Path filter — only POST /v1/turn is in scope for the safety
        # stack. Everything else passes through with no overhead beyond
        # the URL comparison.
        if request.url.path != GUARDED_PATH:
            return await call_next(request)

        # Gate-respect — when the handler's own gates would 503, we
        # passthrough without inspecting the body so the handler's
        # 503 propagates back unchanged. See file-header rationale.
        #
        # Codex PR-#112 round-6 BLOCKER fix — gate-respect must NOT
        # include `environment == "production"` standalone. Round-3
        # had `env=production OR not (real_llm or stub)`, which means
        # once the cluster cutover allows real-LLM in production, the
        # safety stack would still pass through (the handler would
        # serve a real LLM call, but H5/H4/A10 wouldn't fire). That
        # bakes in a future production safety bypass.
        #
        # Correct gate: only pass through when NO orchestration path
        # is enabled — production-with-real-LLM gets full safety
        # inspection. The Day-5 production-gate is still in
        # run_turn.py (it 503s today), but THIS middleware's
        # passthrough only kicks in when there's nothing to protect.
        settings = get_settings()
        gate_closed = not (
            settings.enable_run_turn_real_llm
            or settings.enable_run_turn_stub
        )
        if gate_closed:
            return await call_next(request)

        # Codex PR-#112 round-3 BLOCKER 1 — enforce the round-4
        # X-User-Id + X-Idempotency-Key + UUID-format gate BEFORE any
        # safety canned-response can emit. Without this gate, a
        # header-omitting attacker would see a clean message return
        # 400 (handler gate) while a jailbreak returns 200 canned
        # (safety bypass) — fingerprinting the safety stack's
        # existence. Now both paths produce the same 400 envelope.
        header_error_response = validate_required_headers(request)
        if header_error_response is not None:
            return header_error_response

        # Audit-trail entry marker — used by the order-verification
        # test in `tests/test_safety_stack.py`. No-op in production
        # (when SAFETY_AUDIT_TRAIL ContextVar is at its `None` default).
        record("H5_entry")

        # Read + replay the request body. After this call, downstream
        # layers (H4, A10) + the handler can re-read the body via
        # `await request.body()` and get the cached bytes back.
        body_bytes = await read_and_replay_body(request)

        # Parse the JSON. A malformed body is NOT H5's concern — let
        # FastAPI / Pydantic emit 422 downstream. Same for a missing
        # `user_message` field.
        try:
            payload = json.loads(body_bytes)
            user_message = payload.get("user_message", "")
        except (json.JSONDecodeError, AttributeError):
            payload = None
            user_message = ""

        # Codex PR-#112 round-2 CONCERN: a body like
        # `{"user_message": 123}` makes the matcher call `re.search`
        # on a non-string + crash → 500 before FastAPI / Pydantic gets
        # a chance to emit the proper 422 validation response.
        # Guard with isinstance: if the field isn't a string, drop it
        # to "" so the matcher passes through harmlessly and the
        # downstream handler's Pydantic validation returns the
        # documented 422 envelope. Same guard mirrored in H4.
        if not isinstance(user_message, str):
            user_message = ""

        # Run the matcher. On match, short-circuit with the canned
        # response; no `call_next` so H4 / A10 / handler never run.
        reason = _match_injection(user_message)
        if reason is not None:
            # Use the parsed conversation_id when present; fall back
            # to empty string when the body was malformed (rare).
            conversation_id = (
                payload.get("conversation_id", "") if payload else ""
            )

            # Log without echoing the user message (per H6 PII rules).
            # Reason code + match-indicator are non-PII.
            _log.warning(
                "h5_blocked",
                extra={
                    "safety_layer": "H5",
                    "reason": reason,
                    "conversation_id": conversation_id,
                    "user_message_length": len(user_message),
                },
            )

            # H5 Sentry contract (Codex PR-#112 BLOCKER 2) — CONSTRAINTS
            # H5 verbatim: "Prompt injection defense middleware pre-
            # orchestration. Blocks extraction attempts, LOGS TO SENTRY
            # WITH `type=prompt_injection`, returns safe fallback." The
            # stdlib `_log.warning` above is operator-side; this
            # `sentry_sdk.capture_message` is the H5-spec'd
            # Sentry-side event with the typed tag the constraint
            # requires.
            #
            # Per H6 — `extra` carries non-PII metadata only:
            # safety_layer + reason + conversation_id + user_message_length.
            # NEVER the user_message text itself. Sentry's default
            # send_default_pii is False (see `app/sentry_middleware.py`)
            # which catches accidental PII at the SDK layer too.
            sentry_sdk.capture_message(
                "prompt_injection_blocked",
                level="warning",
                extras={
                    "type": "prompt_injection",
                    "safety_layer": "H5",
                    "reason": reason,
                    "conversation_id": conversation_id,
                    "user_message_length": len(user_message),
                },
            )

            # Read validated headers — validate_required_headers ran
            # above so both X-User-Id + X-Idempotency-Key are present
            # + UUID-format-valid.
            user_id = request.headers["x-user-id"]
            idempotency_key = request.headers["x-idempotency-key"]

            # Codex PR-#112 round-5 BLOCKER 2 — full F10 on the
            # safety short-circuit path. acquire_or_check covers:
            #   * replay_done → return cached canned (first-call
            #     timestamp preserved; A8/A16 parity preserved).
            #   * fingerprint_mismatch → 409 envelope (same key +
            #     different body bug detection that handler does).
            #   * in_flight_timeout → 503 envelope.
            #   * acquired → build canned + mark_complete cache write.
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
                        "X-Safety-Decision": "H5",
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
            canned_payload = prompt_injection_blocked(
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
                    "h5_mark_complete_failed: %s",
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
            # Header is informational — Session 3 + Sentry can branch
            # on it without parsing the body. Day-5+ also reflects
            # this into the Langfuse trace metadata.
            response.headers["X-Safety-Decision"] = "H5"
            response.headers["X-Safety-Reason"] = reason

            record("H5_exit")
            return response

        # No match — propagate to H4.
        response = await call_next(request)

        record("H5_exit")
        return response


# ===========================================================================
# RELATED FILES:
#   __init__.py                — package marker + visual ASCII chain
#   _body_replay.py            — body-read + receive-replay helper
#   _safety_audit.py           — audit-trail ContextVar + record() helper
#   h4_crisis_detection.py     — inner safety layer this layer passes to
#   a10_adult_content_filter.py         — innermost safety layer (output-side filter)
#   ../safety/canned_responses.py
#                              — `prompt_injection_blocked()` returns the
#                                canned MessageResponse when H5 short-circuits
#   ../config.py               — `environment` + `enable_run_turn_stub`
#                                settings the gate-respect check reads
#   ../run_turn.py             — Day-2 handler this layer protects
#   ../main.py                 — mounts this middleware via `add_middleware()`
#   ../../tests/test_safety_stack.py
#                              — H5-blocked path + order-verification tests
# ===========================================================================
