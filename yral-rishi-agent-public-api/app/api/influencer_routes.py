# ---------------------------------------------------------------------------
# influencer_routes.py — /api/v1/influencers/* read endpoints.
#
# ⭐ START HERE: this file ships ONLY the influencer-read endpoints
# Day 2 needs. The create flow (3-step generate-prompt / validate /
# create) + admin endpoints (ban / unban / system-prompt edit /
# video-prompt) + DELETE land in the Day 6-7 feature-parity sprint per
# the agent definition Day 6-7 section. Per A2.1 — ship the simplest
# set first; expand on the parity-sprint schedule.
#
# THE 3 ENDPOINTS THIS FILE OWNS RIGHT NOW:
#   GET /api/v1/influencers           → list[InfluencerResponse]  (Cache-Control 300s per contract)
#   GET /api/v1/influencers/trending  → list[InfluencerResponse]
#   GET /api/v1/influencers/{id}      → InfluencerResponse
#
# DEFERRED TO DAY 6-7 PARITY SPRINT:
#   POST   /api/v1/influencers/generate-prompt
#   POST   /api/v1/influencers/validate-and-generate-metadata
#   POST   /api/v1/influencers/create
#   PATCH  /api/v1/influencers/{id}/system-prompt
#   POST   /api/v1/influencers/{id}/generate-video-prompt
#   DELETE /api/v1/influencers/{id}
#   POST   /api/v1/admin/influencers/{id}/ban
#   POST   /api/v1/admin/influencers/{id}/unban
#
# WHY READ-FIRST ORDER?
# Mobile loads the influencer catalog when the chat tab opens — the
# read set is the hot path. Creators using the create flow are a smaller
# subset of traffic, and the create flow itself routes through Session
# 4's influencer-directory (the orchestrator already has the persistence
# logic) — so deferring the write set to Day 6-7 avoids a coordination
# round-trip with Session 4 at Day 2 and keeps Day-2 scope tight.
#
# WHY THE STUB INFLUENCER IS "tara-stub"?
# Per A10 + the llm-routing-matrix memory, "Tara" is the well-known
# OpenRouter-routed influencer in the v2 build. A non-real "tara-stub"
# in the placeholder list makes it obvious to a tester that they're
# seeing stub data (NOT mistaking Tara herself for real production
# data), while still mirroring the production-shape's influencer-id
# routing semantics.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# fastapi — APIRouter groups endpoints under a prefix; Depends wires
# the per-handler feature-flag dependency; Header + Path map URL +
# header components to handler parameters; Query maps query-string
# parameters (Day-8 pagination); Response is injected into the list
# handler to set the BLOCKER-6 Cache-Control header.
from fastapi import APIRouter, Depends, Header, Path, Query, Response

# JSONResponse — BLOCKER-4 stubs return one of these directly so the
# envelope shape + 503 status reach mobile without FastAPI re-wrapping.
from fastapi.responses import JSONResponse

# httpx — Day-8 directory-RPC wrappers catch httpx.TimeoutException +
# httpx.ConnectError on the directory call to map them to the
# public-api 503 envelope. Same pattern Day-4C's chat_routes uses for
# the orchestrator call.
import httpx

# sentry_sdk — Day-8 directory failures tag every failure path with
# `directory.call.failed=<timeout|connect|status|bad_response_shape>`
# so the Sentry dashboard can pivot on the upstream failure mode.
# Same pattern Day-4C's chat_routes uses for the orchestrator call.
import sentry_sdk

# Response model for the read endpoints (renamed from `*Dto` per
# Codex PR #97 BLOCKER 1 + Rishi 2026-05-19 Option-A).
from app.api.response_models import InfluencerResponse

# ApiResponse envelope every read endpoint wraps its payload in.
from app.api.envelope import ApiResponse

# Feature-flag dependency gating the Day-2 placeholder read responses
# so they can't accidentally ship to production traffic.
from app.api.feature_flag import require_day_2_placeholder_flag_enabled

# Error helper + status map — used by the BLOCKER-4 stubs for write +
# admin endpoints so the locked paths return envelope-shaped 503s
# instead of accidental 404s that look like routing bugs to mobile.
# Day-8 also uses these for directory-RPC failure-mapping paths.
from app.api.errors import HTTP_STATUS_FOR_ERROR_CODE, error_response

# Real auth dependency (Day 4B) — replaces the PR #97 round-5 placeholder
# (`require_authorization_header`). Wired per-handler so every
# influencer endpoint (read + BLOCKER-4 stubs + admin) receives an
# `AuthenticatedUser` argument with the validated user_id + raw token.
from app.api.dependencies import AuthenticatedUser, require_authenticated_user

# Day-8 directory-RPC client — list + by-id read endpoints proxy
# through this module to Session 4's influencer-and-profile-directory.
from app import directory_client

# Day-8 request_id forwarding — list + by-id forward X-Request-Id to
# the directory so the cross-service trace stays correlated.
from app.request_id_middleware import get_request_id

# Router for the influencer read endpoints. Prefix means handlers
# declare paths relative to `/api/v1/influencers/`. Day-4B replaced
# PR #97 round-5's router-level placeholder `dependencies=` with
# per-handler `Depends(require_authenticated_user)` so each handler
# receives the AuthenticatedUser as a parameter — same uniform-auth
# coverage, but the AuthenticatedUser flows into the function body
# (Day-4C's orchestrator RPC will forward user_id + raw_token from it).
influencer_router = APIRouter(prefix="/api/v1/influencers", tags=["influencers"])


# ===========================================================================
# Stub data helpers
# ===========================================================================


def _stub_influencer(
    influencer_id: str = "tara-stub-influencer-id",
    display_name: str = "Tara (stub — Day-2 placeholder)",
    archetype: str = "companion",
    is_nsfw: bool = False,
) -> InfluencerResponse:
    """Build a SCHEMA-VALID stub InfluencerResponse.

    WHAT: factory for placeholder influencer records used by Day-2
          read handlers.
    WHEN: called from every Day-2 influencer-read handler.
    WHY:  centralizes the stub shape so Day-6 parity sprint's swap to
          Session 4's influencer-directory RPC is a single-file edit.
    """
    return InfluencerResponse(
        id=influencer_id,
        display_name=display_name,
        bio=(
            "[v2 phase-1 day-2 placeholder bio — real data lands once "
            "Day 4 + Day 6-7 wire to Session 4's influencer-directory]"
        ),
        # Avatar URL is intentionally NOT a real CDN URL so mobile
        # doesn't try to fetch it during stub mode. The path is
        # syntactically a URL so the field's str type holds.
        avatar_url="https://example.invalid/placeholder-avatar.png",
        archetype=archetype,
        is_nsfw=is_nsfw,
        follower_count=0,
        creator_user_id=None,
        is_active="active",
    )


# ===========================================================================
# Handlers — declared in mobile's call-frequency order (list first,
# trending second, single-detail third)
# ===========================================================================


@influencer_router.get(
    "",
    response_model=ApiResponse[list[InfluencerResponse]],
    summary="List every active AI Influencer (Cache-Control 300s per contract)",
)
async def list_influencers(
    response: Response,
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="Page size; default 20, max 100. Plain offset/limit pagination matching mobile's ChatRemoteDataSource.kt:50-70.",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="0-indexed offset into the full catalog; default 0.",
    ),
    user: AuthenticatedUser = Depends(require_authenticated_user),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> JSONResponse:
    """List active influencers — proxies to Session 4's directory.

    WHAT: forwards user_id + request_id + (limit, offset) to
          directory_client.list_influencers(); maps the response to
          the locked ApiResponse envelope. On directory failure
          (timeout, connect-error, non-200) maps to a 503 envelope
          tagged with `directory.call.failed=<mode>` for Sentry.
    WHEN: mobile loads the chat tab — this is the influencer catalog
          everyone sees first.
    WHY:  highest-traffic influencer endpoint by far. Day-8 cuts
          over from the Day-2 stub to the directory RPC per the
          DEP-012 proposed contract.
    """
    request_id = get_request_id() or ""
    try:
        upstream = await directory_client.list_influencers(
            user_id=user.user_id,
            request_id=request_id,
            limit=limit,
            offset=offset,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        # Connection refused / DNS miss / timeout — directory unreachable.
        # Same mapping shape as chat_routes' orchestrator failure path.
        sentry_sdk.set_tag(
            "directory.call.failed",
            "connect" if isinstance(exc, httpx.ConnectError) else "timeout",
        )
        return _directory_unavailable_envelope("GET /api/v1/influencers")

    if upstream.status_code != 200:
        # Directory returned a non-200. Forward as envelope-shaped 503
        # (directory is internal; mobile shouldn't see raw upstream
        # codes for read endpoints).
        sentry_sdk.set_tag("directory.call.failed", "status")
        sentry_sdk.set_context(
            "directory_response",
            {"status_code": upstream.status_code, "path": "GET /v1/influencers"},
        )
        return _directory_unavailable_envelope("GET /api/v1/influencers")

    try:
        # Directory contract: list[InfluencerResponse]. Validate the
        # shape so a directory-side schema drift surfaces as a 503 at
        # public-api (Sentry-tagged) rather than crashing mobile's
        # parser further down the chain.
        items = [InfluencerResponse(**item) for item in upstream.json()]
    except (ValueError, TypeError) as exc:
        # JSON decode failure OR per-item Pydantic validation failure.
        sentry_sdk.set_tag("directory.call.failed", "bad_response_shape")
        sentry_sdk.set_context("directory_decode_error", {"error": str(exc)})
        return _directory_unavailable_envelope("GET /api/v1/influencers")

    # Codex PR #97 BLOCKER 6 — the locked contract requires
    # Cache-Control max-age=300 on this list endpoint so mobile (+ any
    # CDN in front of it) can cache the catalog for 5 minutes. Set on
    # the injected Response so FastAPI sends it alongside the envelope.
    response.headers["Cache-Control"] = "max-age=300"
    envelope = ApiResponse[list[InfluencerResponse]](
        success=True,
        msg="OK",
        error=None,
        data=items,
    )
    return JSONResponse(
        status_code=200,
        content=envelope.model_dump(),
        headers={"Cache-Control": "max-age=300"},
    )


def _directory_unavailable_envelope(handler_name: str) -> JSONResponse:
    """Build the directory-unavailable envelope response.

    WHAT: returns JSONResponse(status=503, content=ApiResponse-shaped
          envelope with error="service_unavailable"). Mirrors the
          BLOCKER-4 `_service_unavailable_stub` helper but stamps the
          msg with the upstream-failure-mode framing.
    WHEN: every Day-8 directory-RPC handler calls this on connect /
          timeout / non-200 / bad-shape failure paths.
    WHY:  centralized so the envelope shape stays uniform across
          failure modes + future flip to retry / circuit-breaker is
          a single-file edit.
    """
    body = error_response(
        "service_unavailable",
        (
            f"{handler_name} could not reach the influencer-and-profile-"
            "directory; retry shortly. (The upstream-failure mode is "
            "tagged on the matching Sentry event for on-call diagnosis.)"
        ),
    ).model_dump()
    return JSONResponse(
        status_code=HTTP_STATUS_FOR_ERROR_CODE["service_unavailable"],
        content=body,
    )


@influencer_router.get(
    "/trending",
    response_model=ApiResponse[list[InfluencerResponse]],
    summary="The currently-trending influencers (subset of the full list)",
)
async def list_trending_influencers(
    user: AuthenticatedUser = Depends(require_authenticated_user),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[list[InfluencerResponse]]:
    """Trending influencers (Day-2 stub).

    WHAT: returns a 1-element list with a stub influencer. The real
          impl ranks by recent engagement metrics that Session 4 owns.
    WHEN: mobile renders the trending carousel on the chat-tab landing.
    WHY:  separate endpoint per the contract — even if the stub returns
          the same shape today, the contract reserves the path.
    """
    return ApiResponse[list[InfluencerResponse]](
        success=True,
        msg="OK",
        error=None,
        data=[_stub_influencer(display_name="Tara (stub trending — Day-2 placeholder)")],
    )


@influencer_router.get(
    "/{influencer_id}",
    response_model=ApiResponse[InfluencerResponse],
    summary="One influencer's public profile by ID",
)
async def get_influencer(
    influencer_id: str = Path(..., description="Influencer UUID (preserved from chat-ai per A4)"),
    user: AuthenticatedUser = Depends(require_authenticated_user),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> JSONResponse:
    """Single-influencer detail — proxies to Session 4's directory.

    WHAT: forwards user_id + request_id + influencer_id to
          directory_client.get_influencer(); maps the response to
          the locked ApiResponse envelope. Directory 404 (no such
          influencer) maps to public-api 404 + locked
          `not_found` error code. Connect / timeout / non-200 /
          bad-shape failures map to envelope-shaped 503.
    WHEN: mobile opens an influencer's detail screen.
    WHY:  the detail screen drives the "Chat with this influencer"
          button → conversation create → message-send flow. Day-8
          cuts over from the Day-2 stub to the directory RPC per
          the contract declared on main.
    """
    request_id = get_request_id() or ""
    try:
        upstream = await directory_client.get_influencer(
            user_id=user.user_id,
            request_id=request_id,
            influencer_id=influencer_id,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        sentry_sdk.set_tag(
            "directory.call.failed",
            "connect" if isinstance(exc, httpx.ConnectError) else "timeout",
        )
        return _directory_unavailable_envelope(
            "GET /api/v1/influencers/{influencer_id}",
        )

    if upstream.status_code == 404:
        # Directory says no such influencer — surface as the locked
        # `not_found` code so mobile's per-id detail-screen knows to
        # show the "influencer no longer exists" state.
        body = error_response(
            "not_found",
            f"Influencer {influencer_id!r} not found.",
        ).model_dump()
        return JSONResponse(
            status_code=HTTP_STATUS_FOR_ERROR_CODE["not_found"],
            content=body,
        )

    if upstream.status_code != 200:
        sentry_sdk.set_tag("directory.call.failed", "status")
        sentry_sdk.set_context(
            "directory_response",
            {
                "status_code": upstream.status_code,
                "path": "GET /v1/influencers/{id}",
                "influencer_id": influencer_id,
            },
        )
        return _directory_unavailable_envelope(
            "GET /api/v1/influencers/{influencer_id}",
        )

    try:
        item = InfluencerResponse(**upstream.json())
    except (ValueError, TypeError) as exc:
        sentry_sdk.set_tag("directory.call.failed", "bad_response_shape")
        sentry_sdk.set_context("directory_decode_error", {"error": str(exc)})
        return _directory_unavailable_envelope(
            "GET /api/v1/influencers/{influencer_id}",
        )

    envelope = ApiResponse[InfluencerResponse](
        success=True,
        msg="OK",
        error=None,
        data=item,
    )
    return JSONResponse(status_code=200, content=envelope.model_dump())


# ===========================================================================
# Codex PR #97 BLOCKER 4 — locked-contract endpoints that previously
# 404'd. Now registered as service_unavailable stubs so the wire
# surface is 100% per A8 + A16, with explicit "not implemented yet"
# signaling instead of accidental 404s mobile would treat as routing
# bugs. Each stub returns the envelope shape mobile pattern-matches
# on; real bodies land in Day 6-7 parity sprint + the admin sprint.
# ===========================================================================


def _service_unavailable_stub(handler_name: str) -> JSONResponse:
    """Build the BLOCKER-4 service_unavailable envelope response.

    WHAT: returns JSONResponse(status=503, content=ApiResponse-shaped
          envelope with error="service_unavailable").
    WHEN: every BLOCKER-4 stub endpoint calls this as its one-line body.
    WHY:  centralized so the envelope shape stays uniform + future
          flip-on is a single-file search-and-edit per handler.
    """
    body = error_response(
        "service_unavailable",
        (
            f"{handler_name} is registered per the locked contract but "
            "the real implementation lands in a later PR (see the PR "
            "deferral table). Mobile + tests can rely on the envelope "
            "shape; behavior is not implemented yet."
        ),
    ).model_dump()
    return JSONResponse(
        status_code=HTTP_STATUS_FOR_ERROR_CODE["service_unavailable"],
        content=body,
    )


# F10 DEFERRAL NOTE — Codex PR #97 round-5 ITEM 5:
# Every BLOCKER-4 stub below returns `service_unavailable` immediately
# WITHOUT mutating any state (no DB writes, no Redis writes, no
# downstream RPC calls). Per F10's "per-endpoint opt-out for truly
# stateless" carve-out + the round-5 directive's "If the endpoint is a
# stub returning service_unavailable: no idempotency needed because
# there's no state mutation. Document this in each stub's docstring":
# the stubs in this section do NOT require X-Idempotency-Key. The real
# implementations (Day 6-7 parity sprint for the write set + admin
# sprint for ban/unban) will add F10 dedup at the same time they
# add the real state-mutation paths.


# --- 3-step creation flow ----------------------------------------------------


@influencer_router.post("/generate-prompt", summary="Step 1 of 3-step creation (stub)")
async def generate_prompt_stub(
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> JSONResponse:
    """Step 1 of the 3-step influencer-creation flow — BLOCKER 4 stub."""
    _ = user
    return _service_unavailable_stub("POST /api/v1/influencers/generate-prompt")


@influencer_router.post(
    "/validate-and-generate-metadata",
    summary="Step 2 of 3-step creation (stub)",
)
async def validate_and_generate_metadata_stub(
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> JSONResponse:
    """Step 2 of the 3-step influencer-creation flow — BLOCKER 4 stub."""
    _ = user
    return _service_unavailable_stub(
        "POST /api/v1/influencers/validate-and-generate-metadata",
    )


@influencer_router.post("/create", summary="Step 3 of 3-step creation (stub)")
async def create_influencer_stub(
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> JSONResponse:
    """Step 3 of the 3-step influencer-creation flow — BLOCKER 4 stub."""
    _ = user
    return _service_unavailable_stub("POST /api/v1/influencers/create")


# --- Creator-owned edit endpoints --------------------------------------------


@influencer_router.patch(
    "/{influencer_id}/system-prompt",
    summary="Edit the Soul File (creator) (stub)",
)
async def edit_system_prompt_stub(
    influencer_id: str = Path(..., description="Influencer UUID"),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> JSONResponse:
    """Edit the AI Influencer's Soul File — BLOCKER 4 stub.

    WHY THE NAME: per B4 the canonical product term is "Soul File," not
    "system prompt." The path keeps the chat-ai contract name verbatim
    (mobile uses it today) but the docs + future logs use Soul File.
    """
    _ = (influencer_id, user)
    return _service_unavailable_stub(
        "PATCH /api/v1/influencers/{influencer_id}/system-prompt",
    )


@influencer_router.post(
    "/{influencer_id}/generate-video-prompt",
    summary="Video-prompt generation helper (stub)",
)
async def generate_video_prompt_stub(
    influencer_id: str = Path(..., description="Influencer UUID"),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> JSONResponse:
    """Generate a video-prompt seeded by the influencer — BLOCKER 4 stub."""
    _ = (influencer_id, user)
    return _service_unavailable_stub(
        "POST /api/v1/influencers/{influencer_id}/generate-video-prompt",
    )


@influencer_router.delete(
    "/{influencer_id}",
    summary="Soft-delete an influencer (sets is_active='discontinued') (stub)",
)
async def delete_influencer_stub(
    influencer_id: str = Path(..., description="Influencer UUID"),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> JSONResponse:
    """Soft-delete an AI Influencer — BLOCKER 4 stub.

    Real impl flips `is_active='discontinued'`; existing user
    conversations stay readable. Locked path; stub holds the wire
    contract.
    """
    _ = (influencer_id, user)
    return _service_unavailable_stub("DELETE /api/v1/influencers/{influencer_id}")


# --- Admin endpoints (X-Admin-Key header per the contract) -------------------
#
# These live in a SEPARATE admin_router so the OpenAPI tags page groups
# them cleanly + so a future PR can wire a different auth dep onto the
# admin surface (mTLS / X-Admin-Key validation) without touching the
# public-influencer routes.


admin_influencer_router = APIRouter(
    prefix="/api/v1/admin/influencers",
    tags=["admin-influencers"],
)


@admin_influencer_router.post("/{influencer_id}/ban", summary="Admin: ban (stub)")
async def admin_ban_stub(
    influencer_id: str = Path(..., description="Influencer UUID"),
    x_admin_key: "str | None" = Header(default=None, alias="X-Admin-Key"),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> JSONResponse:
    """Admin: ban an AI Influencer (X-Admin-Key required) — BLOCKER 4 stub.

    DAY-4B: real JWT auth is wired per-handler (replacing the PR #97
    round-5 placeholder). A stricter admin-only auth layer (e.g.,
    X-Admin-Key validation against a Swarm secret) lands when the
    real admin endpoints' bodies land; for now, X-Admin-Key is parsed
    but unenforced (the stub returns service_unavailable regardless).
    """
    _ = (influencer_id, x_admin_key, user)
    return _service_unavailable_stub(
        "POST /api/v1/admin/influencers/{influencer_id}/ban",
    )


@admin_influencer_router.post("/{influencer_id}/unban", summary="Admin: unban (stub)")
async def admin_unban_stub(
    influencer_id: str = Path(..., description="Influencer UUID"),
    x_admin_key: "str | None" = Header(default=None, alias="X-Admin-Key"),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> JSONResponse:
    """Admin: unban an AI Influencer (X-Admin-Key required) — BLOCKER 4 stub.

    See `admin_ban_stub` for the Day-4B auth wiring note.
    """
    _ = (influencer_id, x_admin_key, user)
    return _service_unavailable_stub(
        "POST /api/v1/admin/influencers/{influencer_id}/unban",
    )


# ===========================================================================
# RELATED FILES:
#   ../main.py               — wires influencer_router into the FastAPI app
#   feature_flag.py          — every handler depends on
#                              require_day_2_placeholder_flag_enabled
#   envelope.py              — ApiResponse[T] wrapper
#   response_models.py                  — InfluencerResponse shape (matches contract verbatim)
#   ../../tests/contract/test_influencer_routes.py
#                            — asserts envelope + DTO shape + feature-flag gating
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — locked endpoint paths
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — Session 4's influencer-directory RPC (Day-6-7 sprint hooks here)
# ===========================================================================
