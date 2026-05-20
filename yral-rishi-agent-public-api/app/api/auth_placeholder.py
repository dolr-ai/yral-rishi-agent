# ---------------------------------------------------------------------------
# auth_placeholder.py — Day-2 placeholder auth dependency.
#
# ⭐ THIS IS A PLACEHOLDER. The real JWT-validation dependency
# (`require_authenticated_user` with full JWKS + shadow-rollout) lands
# in PR #102 (Day 4B) which stacks on top of PR #97. This file exists
# ONLY so PR #97 isn't shipped with chat + influencer endpoints that
# accept unauthenticated traffic.
#
# WHAT THE PLACEHOLDER DOES:
# - Checks for the `Authorization: Bearer <anything>` header.
# - Rejects with envelope-shaped 401 when absent / malformed.
# - Returns a stub `AuthenticatedRequest` dataclass holding just the
#   raw Bearer token. NO JWT decoding, NO signature verification,
#   NO user_id extraction — all of that is PR #102's job.
#
# WHY A PLACEHOLDER, NOT THE REAL DEPENDENCY?
# Codex PR #97 round-5 ITEM 4: chat endpoints currently allow success
# without an Authorization header. The real fix is PR #102 (full JWT
# shadow rig). But PR #97 has to MERGE before PR #102's stack
# rebases, and merging without ANY auth gate means a half-built v2
# could serve responses to unauthenticated traffic. Placeholder
# closes that gap with minimal code, swappable in PR #102's rebase.
#
# WHAT PR #102's REBASE WILL DO:
# 1. Replace the `Authorization: Bearer <anything>` check with the
#    full dual-validate dependency that runs `LegacyJwtValidator` +
#    `StrictJwtValidator` (per E9 + Day-3 work shipped in PR #99).
# 2. Replace `AuthenticatedRequest` with `AuthenticatedUser`
#    (`user_id` + `raw_token` + `validation_result`) — same
#    `raw_token` field name so handlers that read it don't need to
#    change.
# 3. Delete this file. The rebase makes it dead code.
#
# SCOPE GUARDRAIL — DAY 2 (PR #97):
# - Health endpoints stay auth-free per F9 + C10 + I2 (Caddy
#   `health_uri /health/ready`, Swarm rolling-update health gate,
#   Uptime Kuma — none of these send auth headers).
# - Stub endpoints registered in BLOCKER 4 (influencer write set +
#   admin + WS inbox) ALSO require auth so an unauthenticated request
#   hits 401 (locked code) before the 503 stub fires. Codex round-5
#   ITEM 4: "Add this dependency to every chat + influencer endpoint
#   that currently has none."
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib dataclass — defines the AuthenticatedRequest return type that
# handlers receive via `Depends(require_authorization_header)`.
from dataclasses import dataclass

# fastapi — HTTPException raised on missing/malformed Authorization;
# Request param lets the dependency read headers without a full
# OAuth2 helper class.
from fastapi import HTTPException, Request

# Error helper + status map — used to build the 401 envelope body so
# the locked error-codes table governs both shape AND status code.
from app.api.errors import HTTP_STATUS_FOR_ERROR_CODE, error_response


@dataclass
class AuthenticatedRequest:
    """What handlers receive via `Depends(require_authorization_header)`.

    WHAT: dataclass with a single field `raw_token` (the string after
          "Bearer " in the Authorization header). PR #102's rebase
          REPLACES this dataclass with `AuthenticatedUser` (`user_id`
          + `raw_token` + `validation_result`) — handlers reading
          `.raw_token` keep working without change.
    WHEN: produced by `require_authorization_header()` on every
          authenticated request.
    WHY:  centralizes the auth-output shape so PR #102's rebase has
          a clean swap point. Handlers depend on the field name
          `raw_token`, NOT on the class identity.
    """

    raw_token: str


def require_authorization_header(request: Request) -> AuthenticatedRequest:
    """Reject the request unless an `Authorization: Bearer <...>` header is present.

    WHAT: extracts the Authorization header from the request; if
          missing or doesn't start with `Bearer `, raises
          HTTPException(401, envelope-dict-detail) which the main.py
          envelope-aware handler returns as the locked 401 envelope
          shape. On success, returns `AuthenticatedRequest(raw_token=...)`
          with the string after "Bearer ".
    WHEN: applied as `Depends(require_authorization_header)` on every
          chat + influencer endpoint (incl. the BLOCKER-4 stubs).
          Health endpoints DO NOT depend on this — per F9 + C10 + I2,
          health probes must answer without auth.
    WHY:  Codex PR #97 round-5 ITEM 4 — chat endpoints currently
          allow success without an Authorization header; this
          placeholder closes that gap until PR #102 swaps it for the
          real JWT-validating dependency.

    NEVER: does NOT decode the JWT, does NOT verify the signature,
    does NOT extract the user_id. All of that is PR #102's job.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        body = error_response(
            "unauthorized",
            "Authentication required: Authorization header missing or malformed.",
        ).model_dump()
        raise HTTPException(
            status_code=HTTP_STATUS_FOR_ERROR_CODE["unauthorized"],
            detail=body,
        )
    raw_token = auth_header[len("Bearer ") :].strip()
    if not raw_token:
        body = error_response(
            "unauthorized",
            "Authentication required: empty Bearer token.",
        ).model_dump()
        raise HTTPException(
            status_code=HTTP_STATUS_FOR_ERROR_CODE["unauthorized"],
            detail=body,
        )
    return AuthenticatedRequest(raw_token=raw_token)


# ===========================================================================
# RELATED FILES:
#   chat_routes.py           — every chat handler depends on
#                              require_authorization_header
#   influencer_routes.py     — every influencer handler (read +
#                              BLOCKER-4 stubs + admin stubs) likewise
#   health_routes.py         — does NOT depend on this (per F9 + C10 + I2)
#   ../main.py               — envelope-aware HTTPException handler
#                              that emits the 401 envelope-dict verbatim
#   errors.py                — error_response + HTTP_STATUS_FOR_ERROR_CODE
#   ../../tests/contract/conftest.py
#                            — adds a default "Bearer ..." Authorization
#                              header to the `client` + `client_flag_off`
#                              fixtures so Day-2 test bodies stay
#                              unchanged + provides `client_no_auth`
#                              + `client_no_auth_flag_off` for the
#                              ITEM-4 missing-auth tests
#   ../../tests/contract/test_handler_auth_placeholder.py
#                            — the 2 ITEM-4 tests asserting 401
#                              envelope on missing / malformed header
#
# WHEN PR #102 REBASES:
#   - Delete this file.
#   - Replace `from app.api.auth_placeholder import require_authorization_header`
#     callsites with `from app.api.dependencies import require_authenticated_user`.
#   - Replace `AuthenticatedRequest` type annotation with `AuthenticatedUser`.
# ===========================================================================
