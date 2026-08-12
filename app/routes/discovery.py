"""Phase 21γ.P34.M2a — Discovery Feed endpoint shell.

`GET /api/v2/discovery/influencer-feed`

Anshuman-compatible FeedResponse envelope (byte-identical to the
current chat-ai feed at cutover time — design doc §8). JWT is
optional: cold-start serves the global feed shuffled per session_id.

Query params (all optional):
  offset           : int  ≥ 0   (default 0)
  limit            : int  1..50 (default 20)
  with_metadata    : bool       (default false) — surfaces archetype,
                                  gender, momentum, live, rank_source
                                  for debug. Mobile contract treats
                                  these as optional fields.
  session_id       : str         (default = derived from user_id or
                                  Authorization-header hash) — drives
                                  the per-session shuffle + seen-set
                                  dedup. Stable session_id ⇒ stable
                                  pagination + non-repeating bots.
"""

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from database import get_pool
from services import discovery_feed, discovery_search

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Discovery feed"])


def _derive_session_id(
    request: Request, explicit: str | None, user_id: str | None
) -> str:
    """If the caller doesn't supply session_id, derive a stable one
    from (user_id) or (authorization-header hash) or (client-ip) so
    seen-set dedup + shuffle stay deterministic across the user's
    session even without an explicit cookie.

    Pure SHA1 — not a credential, just a deterministic bucket key."""
    if explicit:
        return explicit[:128]  # length-bound
    if user_id:
        return f"u:{user_id}"
    auth = request.headers.get("Authorization") or ""
    if auth:
        return "h:" + hashlib.sha1(auth.encode("utf-8")).hexdigest()[:16]
    # Last resort: client IP. Same shape as the JWT-hash variant.
    client_host = (request.client.host if request.client else "") or "anon"
    return "i:" + hashlib.sha1(client_host.encode("utf-8")).hexdigest()[:16]


def _maybe_user_id(request: Request) -> str | None:
    """JWT is OPTIONAL on this endpoint. We tolerate missing /
    invalid auth and serve cold-start; we only extract the principal
    when it parses cleanly."""
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None
    try:
        import jwt as _jwt

        payload = _jwt.decode(
            auth.removeprefix("Bearer ").strip(),
            options={"verify_signature": False},
        )
        sub = payload.get("sub")
        return sub if isinstance(sub, str) and sub else None
    except Exception:
        return None


@router.get("/api/v2/discovery/influencer-feed")
async def influencer_feed(
    request: Request,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(20, ge=1, le=50),
    with_metadata: bool = Query(False),
    session_id: str | None = Query(None),
):
    """Return a paginated, deduplicated, per-session-shuffled feed.

    Latency contract: p95 < 100 ms on the live catalog. See
    `discovery_feed` module docstring for budget breakdown.

    Errors: this endpoint NEVER 5xx's on routine failures (Redis down,
    feed:global missing, pin table missing). Each subsystem degrades
    open per the design's DORMANT-FIRST principle — the worst-case
    response is "alphabetical-by-created_at fallback list, no pins,
    no dedup." A real 5xx only on Postgres being completely
    unreachable (the fallback SELECT can't run)."""
    t0 = datetime.now(timezone.utc)
    user_id = _maybe_user_id(request)
    sid = _derive_session_id(request, session_id, user_id)
    pool = await get_pool()
    try:
        payload = await discovery_feed.build_feed_page(
            pool,
            offset=offset,
            limit=limit,
            with_metadata=with_metadata,
            session_id=sid,
            # Phase 21γ.P34.M2b — feed user_id to the composer so it
            # can resolve cold-start vs warm. None when JWT absent /
            # malformed; the composer treats that as cold-start.
            user_id=user_id,
        )
    except Exception as e:
        # Final belt-and-braces. Postgres pool issue is the only thing
        # that should ever reach here — the inner module fail-opens on
        # everything else.
        logger.exception("influencer_feed: catastrophic failure: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Discovery feed temporarily unavailable. Please try again.",
        )
    elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    # Log slow requests for the latency tracking. Threshold matches the
    # 100ms p95 target — anything above is signal to investigate.
    if elapsed_ms > 100:
        logger.warning(
            "influencer_feed slow: %.1fms session=%s offset=%d limit=%d",
            elapsed_ms,
            sid,
            offset,
            limit,
        )
    return JSONResponse(
        content=payload,
        headers={
            # No browser cache — feed is per-session + lives behind
            # auth-aware logic.
            "Cache-Control": "no-store",
            # Surface latency to mobile for client-side telemetry +
            # to ops for spot checks via curl.
            "X-Feed-Latency-Ms": f"{elapsed_ms:.0f}",
        },
    )


# ─── search endpoint ────────────────────────────────────────────────────


@router.get("/api/v2/discovery/search")
async def discovery_search_endpoint(
    # No max_length here: a hard Query cap 422s on over-long input,
    # which breaks this endpoint's documented "never 422" contract
    # (a user pasting a long block into search should degrade, not
    # error). The service already bounds `q` to 100 chars internally.
    q: str = Query(...),
    limit: int = Query(20, ge=1, le=50),
):
    """`GET /api/v2/discovery/search?q=<text>&limit=20`

    Per docs/discovery-feed-search-addendum-2026-06-18.md §2 + §4.
    Backs the mobile search bar that replaces the "Create AI
    Influencer" button on the discovery page (Option B). Mobile
    handles debounce / cancel-in-flight / recent-query cache per
    §5; the backend is plain SQL.

    JWT not required — consistent with the discovery feed endpoint.
    Empty / whitespace `q` returns `{"results": [], "count": 0}`
    (NOT 422; mobile sends `?q=` while the user types). Only a real
    catastrophic Postgres pool failure raises 503."""
    t0 = datetime.now(timezone.utc)
    pool = await get_pool()
    try:
        payload = await discovery_search.search(pool, q, limit)
    except Exception as e:
        # Pool unreachable / unexpected — match the M2a route's
        # catastrophic-only 503 shape so mobile's error handling
        # stays uniform across both endpoints.
        logger.exception("discovery_search: catastrophic failure: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Search temporarily unavailable. Please try again.",
        )
    elapsed_ms = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    if elapsed_ms > 200:
        # 200 ms upper bound for the SQL path. Slower means the GIN
        # index didn't catch — investigate.
        logger.warning(
            "discovery_search slow: %.1fms q=%r limit=%d",
            elapsed_ms,
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
