"""Phase 21γ.P34.InboxSearch — search the calling user's
conversations by bot display_name / category / archetype.

`GET /api/v2/chat/conversations/search?q=<text>&limit=20`
Requires `Authorization: Bearer <jwt>`. Returns the inbox-search
envelope (one row per matching conversation owned by the caller).

Backs the shared-search-bar's inbox-tab behaviour added by Rishi
2026-06-18 PM. Discover tab uses `/api/v2/discovery/search`; inbox
tab uses this endpoint. Mobile switches based on focused tab.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from auth import get_current_user
from database import get_pool
from services import inbox_search

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat v2 — Bot-aware"])


@router.get("/api/v2/chat/conversations/search")
async def conversations_search_endpoint(
    request: Request,
    # No max_length here: a hard Query cap 422s on over-long input,
    # which breaks this endpoint's documented "never 422" contract.
    # The service already bounds `q` to 100 chars internally.
    q: str = Query(...),
    limit: int = Query(20, ge=1, le=50),
):
    """Search the JWT-bearing user's existing conversations by bot
    metadata. JWT REQUIRED — 401 on missing / invalid header
    (privacy gate; we never surface other users' inboxes).

    Empty / whitespace `q` returns `{"results": [], "count": 0}`
    (NOT 422; mobile sends `q=""` while debouncing). Pool
    unreachable / catastrophic Postgres failure → 503 with the same
    envelope shape as `/api/v2/discovery/search`."""
    # JWT enforcement — raises 401 cleanly via auth.get_current_user
    # before we touch the pool.
    user_id = get_current_user(request)

    t0 = datetime.now(timezone.utc)
    pool = await get_pool()
    try:
        payload = await inbox_search.search(pool, user_id, q, limit)
    except Exception as e:
        logger.exception("inbox_search: catastrophic failure: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Inbox search temporarily unavailable. Please try again.",
        )
    elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    if elapsed_ms > 200:
        logger.warning(
            "inbox_search slow: %.1fms user=%s q=%r limit=%d",
            elapsed_ms,
            user_id,
            (q or "")[:32],
            limit,
        )
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": "no-store",
            "X-Search-Latency-Ms": f"{elapsed_ms:.0f}",
        },
    )
