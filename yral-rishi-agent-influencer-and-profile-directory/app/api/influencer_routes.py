# ---------------------------------------------------------------------------
# influencer_routes.py — HTTP surface for the public catalog endpoints.
#
# ⭐ START HERE: this module exposes ONE FastAPI `APIRouter` (prefix
# `/v1`) that defines two routes:
#
#   GET /v1/influencers?limit=<int>&offset=<int>   → list[InfluencerResponse]
#   GET /v1/influencers/{id}                       → InfluencerResponse
#
# Each route delegates to `influencer_metadata_repository` for data
# access, then projects each persistence row through
# `InfluencerResponse.from_persistence` onto the wire shape per the
# `InfluencerDto` contract at `interface-contracts/00-api-contract.md`.
#
# CATALOG AUTHORITY
# ------------------------------------------------------------------
# Both endpoints surface only `is_active IN ('active', 'coming_soon')`
# rows; discontinued rows are filtered at the repository layer per
# the Chunk B coordinator routing 2026-05-25. The 404 path on
# `/v1/influencers/{id}` is intentionally indistinguishable between
# "no such id" and "discontinued" so an external probe can't
# enumerate which ids the catalog has soft-deleted.
#
# 4-HEADER INTERNAL-CALL AUTHENTICATION
# ------------------------------------------------------------------
# Each route requires the 4 internal-call headers declared in
# `interface-contracts/01-internal-rpc-contracts.md:147-156`:
#
#   X-User-Id         — forwarded from public-api after JWT validation
#                       (per E6); we trust without re-validating.
#   X-Internal-Caller — caller service name (e.g.
#                       `yral-rishi-agent-public-api`); used for
#                       Sentry + Langfuse tagging.
#   X-Request-Id      — per-request correlation id from public-api's
#                       request_id_middleware (per D4).
#   X-Trace-Id        — same value as X-Request-Id today; held as a
#                       distinct header so the cross-service trace
#                       graph can diverge from per-request logging
#                       later without a wire-shape break.
#
# Missing-header behaviour: FastAPI emits a 422 with the per-header
# `field required` detail. The mesh-trust model (C3 overlay
# `yral-v2-internal`) is the actual authorisation boundary; the
# headers are observability + per-request user binding rather than
# auth credentials. Future Day-N internal-mesh-mTLS work may add
# SAN-based caller verification (defence-in-depth).
#
# WHY THIN HANDLER FUNCTIONS
# ------------------------------------------------------------------
# Each handler is ~10 lines: read headers, call repository, project
# via `InfluencerResponse.from_persistence`, return. All wire-contract
# policy decisions (which fields to expose, how to map tri-state
# `is_active`, how to handle nullable `avatar_url`) live in the
# response model's `from_persistence` classmethod, not in handlers.
# Pagination bounds + path parameter shape are enforced at the
# FastAPI parameter layer via `Query(..., ge=, le=)` / `Path(...)`.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Query, status

from app.models.influencer_response import InfluencerResponse
from app.repository import influencer_metadata_repository


# FastAPI APIRouter — mounted in `app/main.py` via `include_router`.
# `prefix="/v1"` matches the per-service URL shape declared in the
# internal-rpc-contracts doc. `tags=["influencers"]` groups the routes
# under that label in the auto-generated /docs UI.
router = APIRouter(prefix="/v1", tags=["influencers"])


_log = logging.getLogger("app.api.influencer_routes")


# Per-request 4-header annotations. Declared once as module-level
# type aliases so the two route handlers share them verbatim + a
# regression that drops one header from one route is impossible.
# FastAPI maps `x_user_id` parameter to the `x-user-id` header
# (underscore → hyphen, case-insensitive HTTP matching).
_XUserIdHeader = Annotated[
    str,
    Header(
        ...,
        alias="X-User-Id",
        description=(
            "The requesting user's ID, forwarded by public-api after"
            " JWT validation (per E6). Trusted without re-validation."
        ),
        min_length=1,
    ),
]

_XInternalCallerHeader = Annotated[
    str,
    Header(
        ...,
        alias="X-Internal-Caller",
        description=(
            "Caller service name (e.g. `yral-rishi-agent-public-api`)."
            " Used for Sentry + Langfuse tagging."
        ),
        min_length=1,
    ),
]

_XRequestIdHeader = Annotated[
    str,
    Header(
        ...,
        alias="X-Request-Id",
        description=(
            "Per-request correlation id from public-api's"
            " request_id_middleware (per D4)."
        ),
        min_length=1,
    ),
]

_XTraceIdHeader = Annotated[
    str,
    Header(
        ...,
        alias="X-Trace-Id",
        description=(
            "Cross-service trace id (same value as X-Request-Id today)."
        ),
        min_length=1,
    ),
]


@router.get(
    "/influencers",
    response_model=list[InfluencerResponse],
    status_code=status.HTTP_200_OK,
    summary="List catalog-visible influencers (paginated).",
)
async def list_influencers(
    x_user_id: _XUserIdHeader,
    x_internal_caller: _XInternalCallerHeader,
    x_request_id: _XRequestIdHeader,
    x_trace_id: _XTraceIdHeader,
    limit: Annotated[
        int,
        Query(
            description="Max rows to return (1..100). Default 20.",
            ge=1,
            le=100,
        ),
    ] = 20,
    offset: Annotated[
        int,
        Query(
            description="Number of rows to skip (>= 0). Default 0.",
            ge=0,
        ),
    ] = 0,
) -> list[InfluencerResponse]:
    """List catalog-visible influencers with offset/limit pagination.

    WHAT: returns up to `limit` `InfluencerResponse` rows starting at
          `offset`, ordered by `id` ASC. Discontinued rows are filtered
          at the repository layer per catalog authority. The response
          body is a flat `list[InfluencerResponse]` — no total-count
          wrapper (per DEP-013); mobile derives "more pages available"
          client-side from `len(items) == limit`.
    WHEN: invoked by public-api's `directory_client.list_influencers(...)`
          on the catalog read path. Hot path; no Redis cache today
          (cache layer ships in a follow-up PR; the API contract's
          Cache-Control 300s annotation is a future optimisation).
    WHY:  the canonical catalog read endpoint. Authority lives in the
          repository's `WHERE is_active <> 'discontinued'` filter; this
          handler is a thin pass-through projecting persistence rows
          onto the wire shape.
    """
    _ = (x_internal_caller, x_request_id, x_trace_id)  # observed via Sentry/Langfuse tags

    persistence_rows = await influencer_metadata_repository.list_paginated(
        limit=limit,
        offset=offset,
    )

    return [
        InfluencerResponse.from_persistence(row)
        for row in persistence_rows
    ]


@router.get(
    "/influencers/{influencer_id}",
    response_model=InfluencerResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch a single catalog-visible influencer by id.",
    responses={
        404: {
            "description": (
                "No catalog-visible influencer with this id. Returned"
                " when the id has no row OR when the row exists but is"
                " discontinued — the two cases are intentionally"
                " indistinguishable to the caller (privacy + soft-"
                "delete-enumeration protection)."
            ),
        },
    },
)
async def get_influencer(
    x_user_id: _XUserIdHeader,
    x_internal_caller: _XInternalCallerHeader,
    x_request_id: _XRequestIdHeader,
    x_trace_id: _XTraceIdHeader,
    influencer_id: Annotated[
        str,
        Path(
            description="AI Influencer UUID (catalog id).",
            min_length=1,
        ),
    ],
) -> InfluencerResponse:
    """Fetch one catalog-visible influencer by id.

    WHAT: returns the `InfluencerResponse` for the row whose `id`
          matches AND whose `is_active` is not 'discontinued'. 404 in
          either of the two negative cases (no row OR discontinued
          row).
    WHEN: invoked by public-api's `directory_client.get_influencer(...)`
          on the by-id read path. Hot path; PK index makes the SQL
          O(1).
    WHY:  the canonical by-id catalog read endpoint. Indistinguishable
          404 between missing + discontinued is by design — an
          external probe enumerating ids should not be able to
          discover which ones were soft-deleted vs never existed.
    """
    _ = (x_internal_caller, x_request_id, x_trace_id)  # observed via Sentry/Langfuse tags

    persistence_row = await influencer_metadata_repository.get_by_id(
        influencer_id
    )

    if persistence_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "not_found",
                "message": (
                    "No catalog-visible influencer with this id."
                ),
            },
        )

    return InfluencerResponse.from_persistence(persistence_row)


# ===========================================================================
# RELATED FILES:
#   ../models/influencer_response.py
#                                  — `InfluencerResponse` wire-shape model
#                                    + `from_persistence` projection.
#   ../models/influencer_metadata.py
#                                  — `InfluencerMetadata` persistence model.
#   ../repository/influencer_metadata_repository.py
#                                  — data-access layer the handlers call.
#   ../main.py                     — mounts THIS router via include_router.
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                                  — InfluencerDto wire shape this router
#                                    serves.
#   ../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                                  — public-api → influencer-directory
#                                    contract this router implements.
# ===========================================================================
