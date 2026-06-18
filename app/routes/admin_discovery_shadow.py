"""Phase 21γ.P34.M7 — admin endpoint for the v2-vs-Anshuman shadow diff.

`POST /admin/discovery/shadow-diff?limit=20&session_id=<opt>`

Operator-only (X-Admin-Key gated, same pattern as
`/admin/influencers/{id}/ban`). Returns the diff dict from
`feed_shadow_diff.shadow_diff` — does NOT 5xx on routine fetch
failures (errors surface in the response envelope so the operator
gets actionable output rather than a 500).
"""

import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

import config
from database import get_pool
from services.feed_shadow_diff import shadow_diff

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin — Discovery feed"])


def _require_admin_key(x_admin_key: str | None) -> None:
    if (
        not config.ADMIN_KEY
        or not x_admin_key
        or not secrets.compare_digest(x_admin_key, config.ADMIN_KEY)
    ):
        raise HTTPException(status_code=403, detail="Invalid admin key")


@router.post("/admin/discovery/shadow-diff")
async def admin_shadow_diff(
    limit: int = Query(20, ge=1, le=50),
    session_id: str = Query("shadow-diff-probe", max_length=128),
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """Cutover-prep parity check. Compare v2's discovery feed against
    Anshuman's recsys at offset=0 + the given limit. Returns
    `{v2_count, anshuman_count, overlap_pct, only_in_v2,
    only_in_anshuman, ordering_deltas, checked_at, elapsed_ms}` plus
    optional `errors[]` if either side failed.

    Run on demand by Rishi to spot-check overlap before flipping the
    alpha-team Remote Config flag. Suggested cadence: a handful of
    runs across different `session_id` values to surface any
    session-shuffle-driven divergence."""
    _require_admin_key(x_admin_key)
    pool = await get_pool()
    diff = await shadow_diff(pool, limit=limit, session_id=session_id)
    logger.info(
        "shadow_diff probe: overlap=%.1f%% v2=%d ansuman=%d limit=%d",
        diff.get("overlap_pct", 0.0),
        diff.get("v2_count", 0),
        diff.get("anshuman_count", 0),
        limit,
    )
    return JSONResponse(
        content=diff,
        headers={"Cache-Control": "no-store"},
    )
