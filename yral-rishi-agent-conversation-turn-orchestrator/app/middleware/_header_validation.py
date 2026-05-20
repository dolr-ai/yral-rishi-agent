# ---------------------------------------------------------------------------
# _header_validation.py — shared X-User-Id + X-Idempotency-Key gate.
#
# ⭐ START HERE: exports ONE function — `validate_required_headers(request)`.
# Returns `None` if the request's `X-User-Id` + `X-Idempotency-Key`
# headers are present and well-formed (UUID); returns a 400-status
# `JSONResponse` carrying the ApiResponse envelope when either is
# missing or malformed.
#
# WHY THIS LIVES IN A SHARED HELPER (not duplicated in middleware + handler)
# Per CONSTRAINTS F10 ("Idempotency-key default-on on all non-GET
# endpoints") + the round-4 X-User-Id REQUIRED gate (Codex PR-#96
# BLOCKER 2) + the round-4 X-Idempotency-Key UUID-format gate (Codex
# PR-#96 BLOCKER 3): EVERY response on `POST /v1/turn` — including
# safety-canned 200s from H5 / H4 — must honour the same header
# contract. Codex PR-#112 round-3 correctly flagged that the H5 / H4
# short-circuits in `app/middleware/h5_prompt_injection.py` +
# `app/middleware/h4_crisis_detection.py` were skipping this gate,
# letting a malicious caller learn the safety stack's existence by
# omitting headers (clean input → 400; jailbreak input → 200 canned).
#
# This helper centralises the check so:
#   1. The run_turn handler (in `app/run_turn.py`) keeps its inline
#      validation — kept inline today because handler test coverage
#      already pins it. A future refactor MAY swap the handler over
#      to this helper, but doing both today would expand PR scope.
#   2. The safety middlewares (H5 + H4) call this helper BEFORE
#      pattern-matching, so a header-malformed request gets the same
#      400 envelope shape the handler would emit.
#   3. The error envelope shape stays byte-identical across surfaces
#      (one place to bump the schema if a future contract update lands).
#
# WHY NOT BLOCK SAFETY-CANNED REPLIES FROM REACHING REDIS DEDUP?
# F10 says "Per-endpoint opt-out for truly stateless." Safety-canned
# replies don't touch Redis from the safety middleware's perspective
# (no SET, no DEL); the handler's `acquire_or_check` flow runs only
# on the `call_next` path. So the safety reply is "opted out" of the
# Redis dedup write — but it MUST still honour the header gate (the
# header gate is universal; the dedup write is per-endpoint stateless).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# `uuid.UUID(...)` raises ValueError on a non-UUID string; we use that
# as the boolean check for X-Idempotency-Key validity.
import uuid

# stdlib SHA-256 — invalid X-Idempotency-Key values are logged as
# their first-16-chars-of-sha256 hash, NEVER as the raw value
# (H6 defence-in-depth even on the reject path; same pattern as
# `app/run_turn.py`).
import hashlib

# stdlib logger — emits structured fields the H6 allowlist redactor
# in `app/logging.py` knows about. NEVER raw header values.
import logging

# Starlette's typed Request wrapper. The helper reads `request.headers`
# (already case-insensitive lookup) so no extra import for header parsing.
from starlette.requests import Request

# `JSONResponse` builds the 400 envelope when validation fails.
from starlette.responses import JSONResponse


_log = logging.getLogger("app.middleware._header_validation")


def _api_response_envelope(error_code: str, message_text: str) -> dict:
    """Return the ApiResponse envelope (same shape as run_turn.py's).

    WHAT: builds `{"success": False, "msg": message_text,
          "error": error_code, "data": None}` per the contract at
          `interface-contracts/00-api-contract.md`.
    WHEN: called by the three header-validation failure paths below.
    WHY:  mirrors `app/run_turn.py::_api_response_envelope` byte-for-byte
          so middleware + handler emit identical error shapes. Centralised
          to one helper here so a future schema bump edits one location.
    """
    # `success=False` marks the envelope as the error variant;
    # mobile clients branch on this flag before inspecting `data`.
    # `msg` carries human-readable diagnostic for log capture.
    # `error` is the machine-readable code mobile clients dispatch
    # on (per the API contract). `data=None` keeps the shape
    # identical to the success envelope so JSON parsers don't
    # need conditional logic.
    return {
        "success": False,
        "msg": message_text,
        "error": error_code,
        "data": None,
    }


def validate_required_headers(request: Request) -> JSONResponse | None:
    """Validate X-User-Id + X-Idempotency-Key on a POST /v1/turn request.

    WHAT: checks (1) X-User-Id present, (2) X-Idempotency-Key present,
          (3) X-Idempotency-Key parses as a UUID. Returns the matching
          400 JSONResponse envelope on failure; returns None on success.
    WHEN: called from H5 + H4 middleware `dispatch()` AFTER the gate-
          respect check and BEFORE the body-replay / pattern-match
          stages. (A10 doesn't need it — the handler's existing inline
          check fires on the same path A10 wraps.)
    WHY:  Codex PR-#112 round-3 BLOCKER 1 closure. Universal header
          contract; safety short-circuit must honour it the same way
          the handler does. Run_turn.py keeps its own inline check
          (handler-test surface stays untouched); both paths emit the
          byte-identical envelope shapes per the contract.

    Args:
      request — Starlette `Request` (middleware sees these typed).

    Returns:
      None             — every required header is present + well-formed.
      JSONResponse(400) — at least one header is missing / malformed.
                          The body carries the same envelope shape
                          run_turn.py emits, so callers + Sentry can
                          treat the two surfaces uniformly.
    """
    # X-User-Id REQUIRED (PR #96 round-4 BLOCKER 2).
    user_id = request.headers.get("x-user-id")
    if user_id is None:
        _log.warning(
            "user_id_header_required_but_missing_at_middleware",
            extra={"route": request.url.path},
        )
        return JSONResponse(
            status_code=400,
            content=_api_response_envelope(
                error_code="user_id_header_required",
                message_text=(
                    "X-User-Id header is required for POST /v1/turn "
                    "(per E6 — public-api forwards the JWT-validated user_id "
                    "to every internal RPC; missing here = caller bypassed "
                    "public-api OR public-api has a wiring bug)."
                ),
            ),
        )

    # X-Idempotency-Key REQUIRED (PR #96 round-3 BLOCKER 1a).
    idempotency_key = request.headers.get("x-idempotency-key")
    if idempotency_key is None:
        _log.warning(
            "idempotency_key_required_but_missing_at_middleware",
            extra={"route": request.url.path},
        )
        return JSONResponse(
            status_code=400,
            content=_api_response_envelope(
                error_code="idempotency_key_required",
                message_text=(
                    "X-Idempotency-Key header is required for POST /v1/turn "
                    "(F10 default-on idempotency; contract at "
                    "interface-contracts/01-internal-rpc-contracts.md)."
                ),
            ),
        )

    # X-Idempotency-Key UUID format (PR #96 round-4 BLOCKER 3).
    try:
        uuid.UUID(idempotency_key)
    except ValueError:
        offending_value_hash_prefix = hashlib.sha256(
            idempotency_key.encode("utf-8"),
        ).hexdigest()[:16]
        _log.warning(
            "idempotency_key_invalid_format_at_middleware",
            extra={
                "route": request.url.path,
                "idempotency_key_hash_prefix": offending_value_hash_prefix,
            },
        )
        return JSONResponse(
            status_code=400,
            content=_api_response_envelope(
                error_code="idempotency_key_invalid_format",
                message_text=(
                    "X-Idempotency-Key must be a UUID (RFC 4122). "
                    "Mobile clients should generate it via the standard "
                    "platform UUID API; arbitrary text is rejected so the "
                    "header value cannot leak PII into Redis keys or logs."
                ),
            ),
        )

    # All three checks passed.
    return None


# ===========================================================================
# RELATED FILES:
#   h5_prompt_injection.py     — caller; gate-check → validate_required_headers
#                                → body-replay → pattern-match
#   h4_crisis_detection.py     — caller; same shape as H5
#   ../run_turn.py             — peer: keeps its own inline header gate
#                                today; tests pin that surface. Both
#                                surfaces emit byte-identical envelopes
#                                per the contract.
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                              — ApiResponse envelope shape this helper
#                                mirrors verbatim
#   ../../yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                              — F10 (idempotency default-on) + the
#                                round-4 BLOCKERs 2 + 3 that made the
#                                two headers REQUIRED on every POST
# ===========================================================================
