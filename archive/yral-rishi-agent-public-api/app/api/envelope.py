# ---------------------------------------------------------------------------
# envelope.py — the ApiResponse[T] wrapper EVERY endpoint returns.
#
# ⭐ START HERE: read this 5-line block and you've got it.
#   - Mobile parses `{ success, msg, error, data }` for every response.
#   - On success: success=True, msg="OK" (or similar), error=None, data=<T>.
#   - On error:   success=False, msg=<user-facing>, error=<machine-code>, data=None.
#   - NEVER skip the envelope, NEVER add fields outside it — mobile is strict.
#
# WHY A GENERIC WRAPPER (not a per-endpoint shape)?
# Mobile (per `reference_yral_mobile_architecture.md`) implements ONE
# parser for ApiResponse<T> and reuses it for every endpoint. Breaking
# the envelope shape on any endpoint breaks every screen on the app.
# Per A8 (feature parity) + A16 (mobile-changes-deferred), v2 must
# return the same envelope chat-ai returns today.
#
# WHY pydantic.Generic[T] INSTEAD OF A PLAIN DICT?
# Three wins:
#   1. FastAPI auto-generates OpenAPI schema entries per concrete type
#      (ApiResponse[MessageResponse] vs ApiResponse[ConversationResponse]) so the
#      docs page is self-describing.
#   2. Pydantic validates the envelope shape on construction — a typo in
#      `success` vs `succes` is caught at code-write time, not at the
#      mobile parser.
#   3. Tests can assert `response.parsed.data.id == "..."` with typed
#      access instead of `response.json()["data"]["id"]` strings.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# typing.Generic / TypeVar — make ApiResponse parametric over the
# payload type T so FastAPI auto-generates per-endpoint OpenAPI
# schemas (ApiResponse[MessageResponse] vs ApiResponse[InfluencerResponse]).
# Optional — `error` + `data` are None on the success / failure paths
# respectively per the locked contract.
from typing import Generic, Optional, TypeVar

# pydantic.BaseModel — gives the envelope free Pydantic validation +
# JSON serialization + OpenAPI schema generation. FastAPI's
# response_model= reads the type annotations off this base.
from pydantic import BaseModel

# T = the payload type the endpoint returns inside `data`. Concrete
# instantiations include ApiResponse[MessageResponse], ApiResponse[list[...]],
# and ApiResponse[dict] (for the read / delete endpoints that return {}).
T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """The single response wrapper every public endpoint returns.

    WHAT: 4-field envelope `{ success, msg, error, data }` matching the
          locked contract at interface-contracts/00-api-contract.md.
    WHEN: instantiated inside every endpoint handler, both for success
          and for error paths (the `errors.py` helper builds the error
          variant so handlers stay one-liners).
    WHY:  mobile parser depends on this exact shape; A8 (parity) +
          A16 (mobile-changes-deferred) make the envelope load-bearing.
    """

    # Did the call succeed? Mobile branches on this flag, NOT on the HTTP
    # status code, because chat-ai today returns 200 with `success=false`
    # for application-level errors (paywall, validation, etc.) and only
    # uses non-200 for transport-level errors (5xx, 401 from gateway).
    # V2 keeps the same convention per A8.
    success: bool

    # User-facing string (shown directly in mobile toast/alert if the UI
    # decides to surface it). "OK" on success; localized error message
    # on failure. Mobile renders verbatim — keep it short + non-technical.
    msg: str

    # Machine-readable error code (from the locked set in
    # interface-contracts/00-api-contract.md error-codes table:
    # unauthorized / forbidden / paywall_required / rate_limited /
    # not_found / validation_failed / internal_error / service_unavailable).
    # Mobile pattern-matches on this for behavior (e.g. paywall_required
    # triggers the IAP sheet). None on success.
    error: Optional[str] = None

    # The endpoint-specific payload. None on error. Typed via the
    # generic T so OpenAPI + IDE + pytest assertions all know the shape.
    data: Optional[T] = None


# ===========================================================================
# RELATED FILES:
#   errors.py                — helper that builds ApiResponse error variants
#   response_models.py                  — the concrete T types (MessageResponse, ConversationResponse, ...)
#   chat_routes.py           — uses ApiResponse[MessageResponse] etc.
#   influencer_routes.py     — uses ApiResponse[list[InfluencerResponse]] etc.
#   health_routes.py         — does NOT use this envelope (per F9 — health
#                              probes return raw {"status": "..."} so
#                              kubelet/docker/uptime-kuma can parse without
#                              the envelope layer)
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
# ===========================================================================
