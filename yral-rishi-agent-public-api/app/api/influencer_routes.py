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
# header components to handler parameters; Response is injected into
# the list handler to set the BLOCKER-6 Cache-Control header.
from fastapi import APIRouter, Depends, Header, Path, Response

# JSONResponse — BLOCKER-4 stubs return one of these directly so the
# envelope shape + 503 status reach mobile without FastAPI re-wrapping.
from fastapi.responses import JSONResponse

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
from app.api.errors import HTTP_STATUS_FOR_ERROR_CODE, error_response

# Router for the influencer read endpoints. Prefix means handlers
# declare paths relative to `/api/v1/influencers/`.
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
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[list[InfluencerResponse]]:
    """List active influencers (Day-2 stub).

    WHAT: returns a 1-element list with a stub influencer. The real
          impl proxies to Session 4's influencer-directory at
          GET http://yral-rishi-agent-influencer-and-profile-directory:8000/influencers
          per interface-contracts/01-internal-rpc-contracts.md.
    WHEN: mobile loads the chat tab — this is the influencer catalog
          everyone sees first.
    WHY:  highest-traffic influencer endpoint by far.
    """
    # Codex PR #97 BLOCKER 6 — the locked contract requires
    # Cache-Control max-age=300 on this list endpoint so mobile (+ any
    # CDN in front of it) can cache the catalog for 5 minutes. Set on
    # the injected Response so FastAPI sends it alongside the envelope.
    response.headers["Cache-Control"] = "max-age=300"
    return ApiResponse[list[InfluencerResponse]](
        success=True,
        msg="OK",
        error=None,
        data=[_stub_influencer()],
    )


@influencer_router.get(
    "/trending",
    response_model=ApiResponse[list[InfluencerResponse]],
    summary="The currently-trending influencers (subset of the full list)",
)
async def list_trending_influencers(
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
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[InfluencerResponse]:
    """Single-influencer detail (Day-2 stub).

    WHAT: returns an InfluencerResponse whose `id` field echoes the path
          parameter (so mobile's "fetch the influencer I picked"
          flow gets the right id back).
    WHEN: mobile opens an influencer's detail screen.
    WHY:  the detail screen drives the "Chat with this influencer"
          button → conversation create → message-send flow.
    """
    return ApiResponse[InfluencerResponse](
        success=True,
        msg="OK",
        error=None,
        # Stub the influencer's id from the URL so mobile's local
        # detail-vs-list join works even in stub mode.
        data=_stub_influencer(influencer_id=influencer_id),
    )


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


# --- 3-step creation flow ----------------------------------------------------


@influencer_router.post("/generate-prompt", summary="Step 1 of 3-step creation (stub)")
async def generate_prompt_stub() -> JSONResponse:
    """Step 1 of the 3-step influencer-creation flow — BLOCKER 4 stub."""
    return _service_unavailable_stub("POST /api/v1/influencers/generate-prompt")


@influencer_router.post(
    "/validate-and-generate-metadata",
    summary="Step 2 of 3-step creation (stub)",
)
async def validate_and_generate_metadata_stub() -> JSONResponse:
    """Step 2 of the 3-step influencer-creation flow — BLOCKER 4 stub."""
    return _service_unavailable_stub(
        "POST /api/v1/influencers/validate-and-generate-metadata",
    )


@influencer_router.post("/create", summary="Step 3 of 3-step creation (stub)")
async def create_influencer_stub() -> JSONResponse:
    """Step 3 of the 3-step influencer-creation flow — BLOCKER 4 stub."""
    return _service_unavailable_stub("POST /api/v1/influencers/create")


# --- Creator-owned edit endpoints --------------------------------------------


@influencer_router.patch(
    "/{influencer_id}/system-prompt",
    summary="Edit the Soul File (creator) (stub)",
)
async def edit_system_prompt_stub(
    influencer_id: str = Path(..., description="Influencer UUID"),
) -> JSONResponse:
    """Edit the AI Influencer's Soul File — BLOCKER 4 stub.

    WHY THE NAME: per B4 the canonical product term is "Soul File," not
    "system prompt." The path keeps the chat-ai contract name verbatim
    (mobile uses it today) but the docs + future logs use Soul File.
    """
    _ = influencer_id
    return _service_unavailable_stub(
        "PATCH /api/v1/influencers/{influencer_id}/system-prompt",
    )


@influencer_router.post(
    "/{influencer_id}/generate-video-prompt",
    summary="Video-prompt generation helper (stub)",
)
async def generate_video_prompt_stub(
    influencer_id: str = Path(..., description="Influencer UUID"),
) -> JSONResponse:
    """Generate a video-prompt seeded by the influencer — BLOCKER 4 stub."""
    _ = influencer_id
    return _service_unavailable_stub(
        "POST /api/v1/influencers/{influencer_id}/generate-video-prompt",
    )


@influencer_router.delete(
    "/{influencer_id}",
    summary="Soft-delete an influencer (sets is_active='discontinued') (stub)",
)
async def delete_influencer_stub(
    influencer_id: str = Path(..., description="Influencer UUID"),
) -> JSONResponse:
    """Soft-delete an AI Influencer — BLOCKER 4 stub.

    Real impl flips `is_active='discontinued'`; existing user
    conversations stay readable. Locked path; stub holds the wire
    contract.
    """
    _ = influencer_id
    return _service_unavailable_stub("DELETE /api/v1/influencers/{influencer_id}")


# --- Admin endpoints (X-Admin-Key header per the contract) -------------------
#
# These live in a SEPARATE admin_router so the OpenAPI tags page groups
# them cleanly + so a future PR can wire a different auth dep onto the
# admin surface (mTLS / X-Admin-Key validation) without touching the
# public-influencer routes.


admin_influencer_router = APIRouter(
    prefix="/api/v1/admin/influencers", tags=["admin-influencers"],
)


@admin_influencer_router.post("/{influencer_id}/ban", summary="Admin: ban (stub)")
async def admin_ban_stub(
    influencer_id: str = Path(..., description="Influencer UUID"),
    x_admin_key: "str | None" = Header(default=None, alias="X-Admin-Key"),
) -> JSONResponse:
    """Admin: ban an AI Influencer (X-Admin-Key required) — BLOCKER 4 stub."""
    _ = (influencer_id, x_admin_key)
    return _service_unavailable_stub(
        "POST /api/v1/admin/influencers/{influencer_id}/ban",
    )


@admin_influencer_router.post("/{influencer_id}/unban", summary="Admin: unban (stub)")
async def admin_unban_stub(
    influencer_id: str = Path(..., description="Influencer UUID"),
    x_admin_key: "str | None" = Header(default=None, alias="X-Admin-Key"),
) -> JSONResponse:
    """Admin: unban an AI Influencer (X-Admin-Key required) — BLOCKER 4 stub."""
    _ = (influencer_id, x_admin_key)
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
