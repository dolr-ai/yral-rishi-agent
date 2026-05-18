# ---------------------------------------------------------------------------
# dependency.py — the FastAPI dependency that runs BOTH validators.
#
# ⭐ START HERE: one callable, `authenticate_user_dual_validate(request)
# -> str (user_id)`. Day-4 wires this as `Depends(...)` on every
# authenticated endpoint. Day-3 ships the rig + exercises it via a
# test-internal endpoint per the agent definition scope guardrail.
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
#   6. Return the user_id string (the dependency value handlers receive).
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
# WHY THE DEPENDENCY RETURNS THE user_id STRING (not a dict / object)?
# Handlers downstream only need the user_id — that's the one thing
# that flows into business logic. Returning a richer object would
# tempt callers to extract more (e.g., raw token, all claims) that
# they don't actually need + couples them to the validator's internals.
# Per A2.1 — return the minimum useful value; expand later if a
# concrete caller proves the need.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

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


def authenticate_user_dual_validate(request: Request) -> str:
    """Run both JWT validators + return the authoritative user_id.

    WHAT: extracts the Bearer token; runs LegacyJwtValidator +
          StrictJwtValidator in sequence; emits the divergence metric;
          returns the user_id from whichever validator is authoritative
          (per settings.enable_strict_jwt_signature_validation).
    WHEN: applied as `Depends(authenticate_user_dual_validate)` on every
          authenticated endpoint by Day-4's wiring PR. Day-3 exercises
          it via a test-internal endpoint in tests/contract/test_jwt_shadow.py.
    WHY:  single point of auth — handlers receive a plain user_id string
          and never see the token / validators / shadow rig.
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

    # Step 6: return the user_id. Handlers that depend on this function
    # receive it as a plain string argument.
    assert authoritative.user_id is not None, "validator returned ok=True without user_id"
    return authoritative.user_id


# ===========================================================================
# RELATED FILES:
#   validators.py            — LegacyJwtValidator + StrictJwtValidator
#   jwks_client.py           — get_signing_keys() the strict path uses
#   observability.py         — emit_dual_validate_result()
#   ../errors.py             — error_response() helper + HTTP status map
#   ../../config.py          — enable_strict_jwt_signature_validation (the gate)
#   ../../main.py            — envelope-aware HTTPException handler that
#                              preserves the dict body verbatim
#   ../../../tests/contract/test_jwt_shadow.py
#                            — registers a test-internal endpoint that
#                              applies this dependency to exercise it
# ===========================================================================
