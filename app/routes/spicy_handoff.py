"""Spicy chat gate — handoff endpoints (track 2a).

Contract: docs/amorae-v2-contract-2026-07-01.md §1
Design: docs/spicy-chat-gate-design-2026-06-28.md §4.7

Two endpoints under the same router:
  POST /api/v1/spicy/handoff          → JWT-authed (native app mints)
  POST /api/v1/spicy/handoff/exchange → X-Amorae-Secret (amorae redeems)

Same auth split as track 1b: the mint side is scoped to the user's
own identity via JWT; the exchange side is server-to-server. Session 6
Option A verdict from track 1b applies verbatim here.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from amorae_auth import require_amorae_secret
from auth import get_current_user
from services import spicy_handoff

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/spicy/handoff", tags=["Spicy — Handoff"])


# ─── Request / response models ──────────────────────────────────────────


class HandoffMintRequest(BaseModel):
    """Optional bot_handle so the ticket can carry the intended web
    destination. Nullable so the client can mint without knowing the
    specific bot (rare — amorae's URL always includes /<bot>)."""

    bot_handle: Optional[str] = Field(default=None, max_length=64)


class HandoffMintResponse(BaseModel):
    ticket: str
    ttl_sec: int


class HandoffExchangeRequest(BaseModel):
    ticket: str = Field(min_length=1, max_length=256)


class HandoffExchangeResponse(BaseModel):
    """Contract §1 shape. `is_anonymous` defaults false for the
    logged-in flow; anonymous handoff is a design open question left
    for a fast-follow (mint side would need to accept the anon
    principal separately)."""

    user_id: str
    bot_handle: Optional[str] = None
    is_anonymous: bool = False


# ─── Routes ─────────────────────────────────────────────────────────────


@router.post("", response_model=HandoffMintResponse)
async def mint_handoff(
    body: HandoffMintRequest, request: Request
) -> HandoffMintResponse:
    """Native app mints a one-time ticket with its JWT. The ticket is
    the ONLY thing that ends up in the URL — the raw JWT never crosses
    the domain boundary.

    Redis unavailable → 503. Failing silently would land the user on
    the brand with a ticket that never exchanges — better to surface
    a real error the app can retry."""
    user_id = get_current_user(request)
    try:
        ticket = await spicy_handoff.mint(
            user_id=user_id,
            bot_handle=body.bot_handle,
            is_anonymous=False,
        )
    except RuntimeError as e:
        logger.error("spicy_handoff mint failed: %s", e)
        raise HTTPException(status_code=503, detail="handoff temporarily unavailable")
    return HandoffMintResponse(ticket=ticket, ttl_sec=spicy_handoff.TICKET_TTL_SEC)


@router.post(
    "/exchange",
    response_model=HandoffExchangeResponse,
    dependencies=[Depends(require_amorae_secret)],
)
async def exchange_handoff(body: HandoffExchangeRequest) -> HandoffExchangeResponse:
    """Amorae server calls this with its shared secret to redeem a
    ticket. The atomic GETDEL in the service enforces single-use:
    a concurrent second call gets None and 401s. On any failure
    (expired / already used / malformed) we return 401 so amorae
    can bounce the user back to the landing to re-tap the mint URL
    per the contract (§1: "4xx → amorae bounces the user back")."""
    payload = await spicy_handoff.exchange(body.ticket)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid or consumed ticket")
    return HandoffExchangeResponse(
        user_id=payload["user_id"],
        bot_handle=payload.get("bot_handle"),
        is_anonymous=bool(payload.get("is_anonymous", False)),
    )
