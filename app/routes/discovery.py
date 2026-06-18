"""Phase 21γ.P34.M2a — Discovery Feed endpoint shell.

`GET /api/v2/discovery/influencer-feed`

Anshuman-compatible FeedResponse envelope (byte-identical to the
current chat-ai feed at cutover time — design doc §8). JWT is
optional + currently ignored — personalization lands in M4.

Query params (all optional):
  offset           : int  0..10000  default 0
  limit            : int  1..50     default 20
  with_metadata    : bool           default false (adds archetype +
                                    gender per bot for debug)
  debug_source     : bool           default false (echoes
                                    {debug_source: "v2"} in the
                                    response so Rishi can confirm
                                    Motorola is hitting v2)
  session_id       : str            ACCEPTED + IGNORED on M2a
                                    (reserved for M2b per-session
                                    shuffle; mobile sends it now so
                                    the contract is stable on cutover)
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from database import get_pool
from services import discovery_feed

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Discovery feed"])


@router.get("/api/v2/discovery/influencer-feed")
async def influencer_feed(
    request: Request,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=50),
    with_metadata: bool = Query(False),
    debug_source: bool = Query(False),
    session_id: str | None = Query(None, max_length=128),
):
    """Return a paginated feed of active bots with admin pins on top.

    JWT is OPTIONAL — logged-out callers get the same response. M2a
    intentionally has no personalization (M4 work).

    Latency target: p95 < 200 ms per M2a brief. Single COUNT + two
    SELECTs against index-backed columns; ~10-30 ms typical."""
    _ = session_id  # accepted for contract stability; unused on M2a
    t0 = datetime.now(timezone.utc)
    pool = await get_pool()
    try:
        payload = await discovery_feed.build_feed_page(
            pool,
            offset=offset,
            limit=limit,
            with_metadata=with_metadata,
            debug_source=debug_source,
        )
    except Exception as e:
        # Only catastrophic Postgres failure reaches here — the inner
        # module fail-opens on pin-table missing. 503 keeps mobile's
        # error envelope unchanged from other 5xx paths.
        logger.exception("influencer_feed: catastrophic failure: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Discovery feed temporarily unavailable. Please try again.",
        )
    elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    if elapsed_ms > 200:
        # 200 ms is the M2a budget. Anything above is signal to
        # investigate; M2b/M2c will tighten the budget toward 100 ms
        # once Redis caching lands.
        logger.warning(
            "influencer_feed slow: %.1fms offset=%d limit=%d",
            elapsed_ms,
            offset,
            limit,
        )
    return JSONResponse(
        content=payload,
        headers={
            # No browser cache — feed is auth-aware once M4 lands; ship
            # no-store now so we don't surprise users at cutover.
            "Cache-Control": "no-store",
            # Surface latency for client-side telemetry + ops curl
            # spot-checks.
            "X-Feed-Latency-Ms": f"{elapsed_ms:.0f}",
        },
    )
