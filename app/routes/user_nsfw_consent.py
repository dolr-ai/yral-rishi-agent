"""User NSFW consent endpoints — track 1b of the spicy chat gate.

Contract: docs/amorae-v2-contract-2026-07-01.md §2. Design:
docs/spicy-chat-gate-design-2026-06-28.md §4.3.

Auth split (Session 6 verdict on the pre-1b clarification):
  - POST → X-Amorae-Secret (server-to-server; amorae writes on the
    user's 18+ click). Idempotent upsert.
  - GET  → JWT (native app reads its own consent for cross-device
    memory). Returns {confirmed, expires_at} — audit fields like
    source_ip stay out of the response.

Two auth paths in one router keeps Rule 1 symmetry: both routes are
about the SAME resource (`user_nsfw_consent`), but their consumers
differ. Splitting the file by auth model would fracture that.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from amorae_auth import require_amorae_secret
from auth import get_current_user
from database import get_pool
from repositories import user_nsfw_consent_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/users/nsfw-consent", tags=["Users — NSFW consent"])


class ConsentWriteRequest(BaseModel):
    """POST body, per amorae contract §2. `surface` is descriptive
    (future-proofs multi-web-surface consent) but not stored today —
    the row is scoped to (user_id) so the last write wins across any
    surface. Accepting the field now avoids a client change when we
    wire it into the audit column later."""

    user_id: str = Field(min_length=1, max_length=255)
    source_ip: Optional[str] = Field(default=None, max_length=45)
    surface: str = Field(default="web_spicy", min_length=1, max_length=64)


class ConsentReadResponse(BaseModel):
    """GET response. `confirmed` is boolean-derived from the row's
    existence (no row = never confirmed); `expires_at` is null when
    the consent is open-ended (design default is +90d but that policy
    lives on the write side)."""

    confirmed: bool
    expires_at: Optional[datetime] = None


@router.post("", status_code=200, dependencies=[Depends(require_amorae_secret)])
async def post_consent(body: ConsentWriteRequest, request: Request) -> dict:
    """Server-to-server write from amorae. Idempotent — repeat clicks
    upsert cleanly. `source_ip` in body takes precedence over the
    request's peer IP (amorae has already resolved X-Forwarded-For on
    its edge; v2 shouldn't second-guess it). Fall back to the peer IP
    only when amorae didn't include one."""
    ip = body.source_ip or (request.client.host if request.client else None)
    pool = await get_pool()
    row = await user_nsfw_consent_repo.upsert(
        pool,
        user_id=body.user_id,
        source_ip=ip,
    )
    # Response body is not part of the contract (§2 says "any 2xx is
    # success") but returning the persisted shape helps debugging
    # amorae's write side without requiring a follow-up GET.
    return {
        "user_id": row["user_id"],
        "confirmed_at": row["confirmed_at"].isoformat()
        if row["confirmed_at"]
        else None,
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
    }


@router.get("", response_model=ConsentReadResponse)
async def get_consent(request: Request) -> ConsentReadResponse:
    """JWT-scoped read of the caller's own consent row. Returns
    `{confirmed: false, expires_at: null}` when no row exists — the
    native app treats that as "gate not yet passed on any device."
    A user can never read someone else's row (only their own `sub`
    from the JWT reaches the query)."""
    user_id = get_current_user(request)
    pool = await get_pool()
    row = await user_nsfw_consent_repo.get(pool, user_id)
    if row is None:
        return ConsentReadResponse(confirmed=False, expires_at=None)
    return ConsentReadResponse(confirmed=True, expires_at=row["expires_at"])
