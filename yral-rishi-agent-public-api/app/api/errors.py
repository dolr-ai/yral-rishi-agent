# ---------------------------------------------------------------------------
# errors.py — error-code strings + envelope-shaped error response helper.
#
# ⭐ START HERE: the `ErrorCode` Literal type below lists EVERY error
# string mobile expects in the `error` field. The `error_response()`
# helper builds an ApiResponse with the right shape so handlers don't
# repeat the boilerplate.
#
# WHY A LITERAL TYPE (not a free-form string)?
# Mobile pattern-matches on these strings to drive behavior — e.g.
# `paywall_required` triggers the Google Play IAP sheet client-side per
# E7. A typo at a callsite ("paywall-required" with a hyphen) would
# silently break the IAP flow. The Literal type makes the typo a
# type-check / IDE error before it ships.
#
# WHY THIS LIST + NOT A LARGER ENUM?
# The list is COPIED from interface-contracts/00-api-contract.md
# "Error codes" section. Don't extend without updating the contract +
# coordinating with Sessions 4 and 5 — mobile's switch statement is
# load-bearing.
#
# WHAT HTTP STATUS GOES WITH WHICH ERROR CODE?
# See the `HTTP_STATUS_FOR_ERROR_CODE` map below. The convention follows
# what chat-ai does today (per A8): app-level errors return HTTP 200
# with `success=false`; transport-level errors (auth, rate-limit, server
# faults) return non-200 with the same envelope.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from typing import Literal, Optional

from app.api.envelope import ApiResponse

# The exact 8 strings the contract locks in. Mobile pattern-matches on
# these. NEVER introduce a new code without updating
# interface-contracts/00-api-contract.md + this Literal + the HTTP map below.
ErrorCode = Literal[
    "unauthorized",          # JWT missing or invalid (per E6 + the Day-3 shadow rollout)
    "forbidden",             # JWT valid but the user lacks permission for the resource
    "paywall_required",      # pre-chat access check failed (E7 — NOT a 402 per CURRENT-TRUTH)
    "rate_limited",          # too many requests for the user / endpoint window
    "not_found",             # the resource ID doesn't exist or was deleted
    "validation_failed",     # malformed input (request body / query string / headers)
    "internal_error",        # generic server error — Sentry already captured the trace
    "service_unavailable",   # a downstream dependency is unavailable (orchestrator down, etc.)
]


# HTTP status code each error code returns. Chat-ai's pattern (per A8):
# - app-level concerns (paywall) → HTTP 200 + success=false (mobile
#   reads .success / .error rather than the status code for these)
# - auth + transport-level concerns → non-200 with the same envelope
HTTP_STATUS_FOR_ERROR_CODE: dict[str, int] = {
    "unauthorized": 401,
    "forbidden": 403,
    # 200 — mobile parses the envelope and triggers IAP from .error
    # without ever seeing a 4xx. Matches chat-ai's current behavior.
    "paywall_required": 200,
    "rate_limited": 429,
    "not_found": 404,
    "validation_failed": 400,
    "internal_error": 500,
    "service_unavailable": 503,
}


def error_response(
    code: ErrorCode,
    msg: str,
    data: Optional[object] = None,
) -> ApiResponse[object]:
    """Build a contract-shaped ApiResponse for an error path.

    WHAT: returns ApiResponse(success=False, msg=<user-facing>,
          error=<machine-code>, data=<optional metadata>).
    WHEN: called from any handler that wants to bail with an error.
          The HTTP status code is set separately on the FastAPI Response
          object (or via raise HTTPException) — this helper only builds
          the body shape.
    WHY:  centralizes the 4-field shape so a handler bailing on
          `paywall_required` doesn't accidentally swap field positions.
    """
    return ApiResponse[object](
        success=False,
        msg=msg,
        error=code,
        data=data,
    )


# ===========================================================================
# RELATED FILES:
#   envelope.py              — the ApiResponse[T] shape this helper builds
#   chat_routes.py           — uses error_response("service_unavailable", ...) when feature flag off
#   feature_flag.py          — the dependency that issues the 503 + this error_response
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — locked list of error codes + which HTTP status pairs with each
# ===========================================================================
