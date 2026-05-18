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
#   GET /api/v1/influencers           → list[InfluencerDto]  (Cache-Control 300s per contract)
#   GET /api/v1/influencers/trending  → list[InfluencerDto]
#   GET /api/v1/influencers/{id}      → InfluencerDto
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

from fastapi import APIRouter, Depends, Path

from app.api.dtos import InfluencerDto
from app.api.envelope import ApiResponse
from app.api.feature_flag import require_day_2_placeholder_flag_enabled

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
) -> InfluencerDto:
    """Build a SCHEMA-VALID stub InfluencerDto.

    WHAT: factory for placeholder influencer records used by Day-2
          read handlers.
    WHEN: called from every Day-2 influencer-read handler.
    WHY:  centralizes the stub shape so Day-6 parity sprint's swap to
          Session 4's influencer-directory RPC is a single-file edit.
    """
    return InfluencerDto(
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
    response_model=ApiResponse[list[InfluencerDto]],
    summary="List every active AI Influencer (Cache-Control 300s per contract)",
)
async def list_influencers(
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[list[InfluencerDto]]:
    """List active influencers (Day-2 stub).

    WHAT: returns a 1-element list with a stub influencer. The real
          impl proxies to Session 4's influencer-directory at
          GET http://yral-rishi-agent-influencer-and-profile-directory:8000/influencers
          per interface-contracts/01-internal-rpc-contracts.md.
    WHEN: mobile loads the chat tab — this is the influencer catalog
          everyone sees first.
    WHY:  highest-traffic influencer endpoint by far.
    """
    # Contract notes Cache-Control 300s on the list endpoint. We don't
    # set the header at Day 2 (the stub data is fixed; caching would
    # mask test runs across flag flips). Day-4 RPC integration adds the
    # response header via a FastAPI Response object.
    return ApiResponse[list[InfluencerDto]](
        success=True,
        msg="OK",
        error=None,
        data=[_stub_influencer()],
    )


@influencer_router.get(
    "/trending",
    response_model=ApiResponse[list[InfluencerDto]],
    summary="The currently-trending influencers (subset of the full list)",
)
async def list_trending_influencers(
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[list[InfluencerDto]]:
    """Trending influencers (Day-2 stub).

    WHAT: returns a 1-element list with a stub influencer. The real
          impl ranks by recent engagement metrics that Session 4 owns.
    WHEN: mobile renders the trending carousel on the chat-tab landing.
    WHY:  separate endpoint per the contract — even if the stub returns
          the same shape today, the contract reserves the path.
    """
    return ApiResponse[list[InfluencerDto]](
        success=True,
        msg="OK",
        error=None,
        data=[_stub_influencer(display_name="Tara (stub trending — Day-2 placeholder)")],
    )


@influencer_router.get(
    "/{influencer_id}",
    response_model=ApiResponse[InfluencerDto],
    summary="One influencer's public profile by ID",
)
async def get_influencer(
    influencer_id: str = Path(..., description="Influencer UUID (preserved from chat-ai per A4)"),
    _: None = Depends(require_day_2_placeholder_flag_enabled),
) -> ApiResponse[InfluencerDto]:
    """Single-influencer detail (Day-2 stub).

    WHAT: returns an InfluencerDto whose `id` field echoes the path
          parameter (so mobile's "fetch the influencer I picked"
          flow gets the right id back).
    WHEN: mobile opens an influencer's detail screen.
    WHY:  the detail screen drives the "Chat with this influencer"
          button → conversation create → message-send flow.
    """
    return ApiResponse[InfluencerDto](
        success=True,
        msg="OK",
        error=None,
        # Stub the influencer's id from the URL so mobile's local
        # detail-vs-list join works even in stub mode.
        data=_stub_influencer(influencer_id=influencer_id),
    )


# ===========================================================================
# RELATED FILES:
#   ../main.py               — wires influencer_router into the FastAPI app
#   feature_flag.py          — every handler depends on
#                              require_day_2_placeholder_flag_enabled
#   envelope.py              — ApiResponse[T] wrapper
#   dtos.py                  — InfluencerDto shape (matches contract verbatim)
#   ../../tests/contract/test_influencer_routes.py
#                            — asserts envelope + DTO shape + feature-flag gating
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — locked endpoint paths
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — Session 4's influencer-directory RPC (Day-6-7 sprint hooks here)
# ===========================================================================
