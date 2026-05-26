# ---------------------------------------------------------------------------
# composed_prompt_routes.py — HTTP surface for `GET /composed-prompt`.
#
# ⭐ START HERE: this module exposes ONE FastAPI APIRouter that defines
# `GET /composed-prompt?influencer_id={uuid}&user_segment={...}`. The
# route delegates to `four_layer_composer.compose(...)` and serialises
# the returned `ComposedPromptResponse` as JSON. Error mapping:
#
#   404 — InfluencerSoulFileMissingError raised (no L3 for influencer)
#   500 — SoulFileDataIntegrityError raised (L1/L2/L4 missing — bug)
#   422 — user_segment not in {new, paying, dormant} (Pydantic validation)
#
# INTERNAL-ONLY — NO AUTH ON DAY 4
# Per the Day-4 directive verbatim: "Internal-only per C3 — no auth on
# Day 4 (overlay yral-v2-internal protects; same trust model as
# orchestrator → soul-file mentioned in 01-internal-rpc-contracts.md).
# Document this explicitly in code comment + RUNBOOK." The cluster's
# Swarm overlay `yral-v2-internal` per C3 is the trust boundary — only
# services on that overlay can reach this port + the orchestrator
# trusts whatever scope_key it forwards because it has already
# validated the user's JWT before issuing the internal RPC. Future
# Day-5+ work may add `X-Internal-Caller: orchestrator` validation as
# defence-in-depth, but Day-4 leaves the port unauthenticated.
#
# WHY MATCHES `01-internal-rpc-contracts.md` EXACTLY
# The orchestrator (Session 4's Day-5+) integrates against this route
# per the internal-rpc-contracts doc. The shape — query params +
# response keys — must be byte-identical to the doc so Session 5's
# contract-tests (Day-10+) can lock the contract.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.composer.four_layer_composer import (
    InfluencerSoulFileMissingError,
    SoulFileDataIntegrityError,
    compose,
)
from app.models.soul_file import ComposedPromptResponse, UserSegment


# FastAPI APIRouter — mounted in `app/main.py` via `include_router`.
# `tags=["composed-prompt"]` groups this route under that label in the
# auto-generated /docs UI so non-programmer readers see the per-feature
# block clearly.
router = APIRouter(tags=["composed-prompt"])


_log = logging.getLogger("app.api.composed_prompt_routes")


@router.get(
    "/composed-prompt",
    response_model=ComposedPromptResponse,
    status_code=status.HTTP_200_OK,
    summary="Compose the 4-layer Soul File prompt for (influencer, user_segment).",
)
async def get_composed_prompt(
    influencer_id: Annotated[
        str,
        Query(
            description=(
                "AI Influencer UUID. Looked up against soul_file_layers"
                " WHERE layer=3 AND scope_key=influencer_id."
            ),
            min_length=1,
        ),
    ],
    user_segment: Annotated[
        UserSegment,
        Query(
            description=(
                "User segment — drives the Layer 4 lookup."
                " Pydantic 422 if not in {new, paying, dormant}."
            ),
        ),
    ],
) -> ComposedPromptResponse:
    """Return the assembled 4-layer Soul File prompt + version_pin.

    WHAT: dispatches to `four_layer_composer.compose(...)` + maps the
          composer's exceptions to HTTP status codes.
    WHEN: called once per chat turn by the orchestrator (Day-5+) via
          the internal Swarm overlay `yral-v2-internal` per C3.
    WHY:  the orchestrator hands the returned `layered_prompt` to the
          LLM provider as the cache-eligible prefix; byte-identity
          across turns for the same `(influencer_id, user_segment)`
          is what makes provider-side prompt caching hit.

    Raises:
        HTTPException(404) when no Layer 3 row exists for `influencer_id`.
        HTTPException(500) when the composer flags a data-integrity issue
        (L1/L2/L4 missing — should never happen in steady state).
    """
    try:
        return await compose(influencer_id=influencer_id, user_segment=user_segment)
    except InfluencerSoulFileMissingError as exc:
        # Caller asked about an influencer with no Soul File row.
        # 404 is the right answer — orchestrator will surface this to
        # public-api which returns an empty / fallback to the mobile.
        _log.info(
            "composed_prompt_404",
            extra={
                "influencer_id": influencer_id,
                "user_segment": user_segment,
                "reason": "layer_3_missing",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except SoulFileDataIntegrityError as exc:
        # Seed data missing — operator action required (re-seed or
        # restore the retired row). 500 because this is our fault,
        # not the caller's.
        _log.error(
            "composed_prompt_500_data_integrity",
            extra={
                "influencer_id": influencer_id,
                "user_segment": user_segment,
                "reason": "data_integrity_issue",
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


# ===========================================================================
# RELATED FILES:
#   __init__.py                     — package marker
#   ../composer/four_layer_composer.py
#                                  — compose() the route delegates to
#   ../models/soul_file.py          — ComposedPromptResponse + UserSegment
#   ../main.py                      — mounts this router via include_router
#   ../../tests/test_api_composed_prompt.py
#                                  — HTTP 200 / 422 / 404 path tests
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                                  — the cross-service RPC contract this
#                                    route's shape implements
#   ../../RUNBOOK.md                — operator commands for rollback +
#                                    re-seeding (the 500-path runbook)
#   ../../SECURITY.md               — C3 overlay trust model + the
#                                    no-auth-on-Day-4 rationale
# ===========================================================================
