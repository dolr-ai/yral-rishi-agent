# ---------------------------------------------------------------------------
# dependency.py — the FastAPI dependency that runs BOTH validators.
#
# ⭐ START HERE: one callable, `authenticate_user_dual_validate(request)
# -> AuthenticatedUser`. Day-4B wires this as `Depends(...)` (via the
# `require_authenticated_user` alias in `app/api/dependencies.py`) on
# every authenticated chat + influencer handler. Day-3 ships the rig
# + exercises it via a test-internal endpoint per the agent definition
# scope guardrail.
#
# WHAT THE DEPENDENCY DOES PER REQUEST:
#   1. Extract Authorization: Bearer <token> header. Missing → 401.
#   2. Run LegacyJwtValidator.validate(token).
#   3. Run StrictJwtValidator.validate(token).
#   4. Call emit_dual_validate_result(legacy, strict, request.url.path)
#      — Sentry breadcrumb + (on divergence) WARN event + Langfuse trace.
#   5. Decide which result is AUTHORITATIVE:
#      - If settings.enable_strict_jwt_signature_validation is False (default,
#        production today): LEGACY is authoritative. Return its
#        user_id (or 401 if legacy itself failed).
#      - If settings.enable_strict_jwt_signature_validation is True (post 7-day
#        soak + Rishi YES): STRICT is authoritative. Return its
#        user_id (or 401 if strict failed).
#   6. Wrap user_id + raw token + authoritative ValidationResult into
#      AuthenticatedUser and return it (the dependency value handlers
#      receive).
#
# WHY EXTRACTING THE HEADER MANUALLY (NOT via FastAPI's OAuth2 helper)?
# FastAPI's OAuth2PasswordBearer pre-extracts the token AND issues 401
# on missing header. We can't use it because we need to run BOTH
# validators on the raw token — OAuth2PasswordBearer's auto-401 would
# bypass the shadow rig entirely. Manual extraction is 3 lines + gives
# us the dual-validate guarantee.
#
# WHY 401-WITH-ENVELOPE-DICT INSTEAD OF FastAPI'S DEFAULT 401?
# Per the contract (interface-contracts/00-api-contract.md):
# `unauthorized` is one of the locked error codes mobile pattern-
# matches on. The envelope-aware HTTPException handler in
# app/main.py preserves the dict body verbatim, so this dependency
# raises HTTPException with the envelope-shaped detail.
#
# WHY THE DEPENDENCY RETURNS AuthenticatedUser (not a bare user_id)?
# Day-4C's orchestrator-RPC handler forwards the raw token to the
# orchestrator + uses user_id as the X-User-Id header. Returning the
# minimal-but-sufficient triple (user_id, raw_token, validation_result)
# keeps Day-4C's diff small + gives handlers richer logging hooks
# without exposing the validators themselves. Per A2.1 — minimum useful
# value; expand later if a concrete caller proves the need.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.api.auth.observability import emit_dual_validate_result
from app.api.auth.validators import (
    LegacyJwtValidator,
    StrictJwtValidator,
    ValidationResult,
)
from app.api.errors import HTTP_STATUS_FOR_ERROR_CODE, error_response
from app.config import get_settings


# Module-level validator instances. Constructed once + reused — the
# validators carry no per-request state.
_legacy_validator = LegacyJwtValidator()
_strict_validator = StrictJwtValidator()


@dataclass
class AuthenticatedUser:
    """Output of the auth dependency — what authenticated handlers receive.

    WHAT: dataclass with `user_id` (str — the authoritative subject claim),
          `raw_token` (str — the original Bearer token, kept so Day-4C
          can re-forward it to downstream services if needed), and
          `validation_result` (ValidationResult from the authoritative
          validator — preserves the reason string + ok flag for handlers
          that want richer logging).
    WHEN: produced by `authenticate_user_dual_validate` on every
          authenticated request. Day-4B wires this as a
          FastAPI Depends parameter on every chat + influencer handler.
    WHY:  per the Day-4B directive: "AuthenticatedUser.user_id is what
          Day-4C forwards as X-User-Id to orchestrator; design the
          dataclass so 4C's diff stays small." Three fields are the
          minimum useful set; expand if a concrete caller proves the
          need (per A2.1).
    """

    user_id: str
    raw_token: str
    validation_result: ValidationResult


def _unauthorized_response(reason: str) -> HTTPException:
    """Build the contract-shaped 401 HTTPException for a failed auth.

    WHAT: returns HTTPException(401, detail=<envelope dict>) so
          app/main.py's envelope-aware handler emits the dict verbatim.
    WHEN: called by `authenticate_user_dual_validate` when the
          authoritative validator returns ok=False (or when the
          Authorization header is missing entirely).
    WHY:  centralizes the 401 body shape so all auth failures emit
          the same envelope mobile can parse.
    """
    body = error_response(
        "unauthorized",
        f"Authentication failed: {reason}",
    ).model_dump()
    return HTTPException(
        status_code=HTTP_STATUS_FOR_ERROR_CODE["unauthorized"],
        detail=body,
    )


def authenticate_user_dual_validate(request: Request) -> AuthenticatedUser:
    """Run both JWT validators + return the authoritative AuthenticatedUser.

    WHAT: extracts the Bearer token; runs LegacyJwtValidator +
          StrictJwtValidator in sequence; emits the divergence metric;
          returns the user_id + raw token + authoritative validation
          result wrapped in AuthenticatedUser (per
          settings.enable_strict_jwt_signature_validation).
    WHEN: applied as `Depends(require_authenticated_user)` (the public
          alias defined in `app/api/dependencies.py`) on every
          authenticated chat + influencer handler. Day-4B wires this.
          Day-3 exercised it via a test-internal endpoint; Day-4B keeps
          that endpoint working too.
    WHY:  single point of auth — handlers receive AuthenticatedUser
          and never see the token / validators / shadow rig.
          AuthenticatedUser.user_id is what Day-4C forwards as
          X-User-Id to the orchestrator (per directive).
    """
    # Step 1: extract the Bearer token. Missing or malformed header → 401.
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise _unauthorized_response("missing or malformed Authorization header")

    token = auth_header[len("Bearer "):].strip()
    if not token:
        raise _unauthorized_response("empty bearer token")

    # Step 2 + 3: run both validators in sequence. They share no state;
    # sequence vs parallel doesn't matter at this call rate. Sequential
    # keeps the call stack readable.
    legacy_result: ValidationResult = _legacy_validator.validate(token)
    strict_result: ValidationResult = _strict_validator.validate(token)

    # Step 4: emit the divergence metric. Returns the boolean for
    # potential use here (currently unused — observability is fire-and-
    # forget — but kept in case Day-4 wants to add a header).
    _ = emit_dual_validate_result(
        legacy=legacy_result,
        strict=strict_result,
        request_path=request.url.path,
    )

    # Step 5: pick the authoritative result.
    settings = get_settings()
    authoritative = strict_result if settings.enable_strict_jwt_signature_validation else legacy_result

    if not authoritative.ok:
        raise _unauthorized_response(authoritative.reason)

    # Step 6: package into AuthenticatedUser. user_id is non-None on
    # the ok path (validators contract). raw_token + validation_result
    # carried for downstream forwarding (Day 4C) + richer handler logging.
    assert authoritative.user_id is not None, "validator returned ok=True without user_id"
    return AuthenticatedUser(
        user_id=authoritative.user_id,
        raw_token=token,
        validation_result=authoritative,
    )


# ===========================================================================
# RELATED FILES:
#   validators.py            — LegacyJwtValidator + StrictJwtValidator
#   jwks_client.py           — get_signing_keys() the strict path uses
#   observability.py         — emit_dual_validate_result()
#   ../dependencies.py       — `require_authenticated_user` alias the
#                              chat + influencer handlers import (Day-4B)
#   ../errors.py             — error_response() helper + HTTP status map
#   ../../config.py          — enable_strict_jwt_signature_validation (the gate)
#   ../../main.py            — envelope-aware HTTPException handler that
#                              preserves the dict body verbatim
#   ../../../tests/contract/test_jwt_shadow.py
#                            — registers a test-internal endpoint that
#                              applies this dependency to exercise it
#   ../../../tests/contract/test_handler_auth.py
#                            — Day-4B auth-edge tests against real chat
#                              + influencer handlers (uses client_no_auth)
# ===========================================================================
