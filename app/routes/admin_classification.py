"""Phase 21γ.P34.M1 — operator endpoints for archetype/gender labels.

Two endpoints:

  POST /admin/discovery/classify-sample
    Runs the classifier on N unclassified bots WITHOUT writing labels.
    Returns proposed `gender` + `archetype` + `confidence` so Rishi can
    review before flipping `ENABLE_INFLUENCER_CLASSIFICATION_LOOP=true`.

  POST /admin/discovery/classify-override
    Operator manual override. Writes `archetype` (validated against the
    5-value enum) + `gender` (validated against the 4-value enum) +
    `category` (free-form text). At least one field is required.

Auth: `X-Admin-Key` header (same constant-time-compare pattern as
`/admin/influencers/{id}/ban`).
"""

import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

import config
from database import get_pool
from services.influencer_classification import (
    apply_admin_override,
    classify_sample,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin — Discovery feed"])


def _require_admin_key(x_admin_key: str | None) -> None:
    if (
        not config.ADMIN_KEY
        or not x_admin_key
        or not secrets.compare_digest(x_admin_key, config.ADMIN_KEY)
    ):
        raise HTTPException(status_code=403, detail="Invalid admin key")


# ─── classify-sample (read-only, feeds Rishi's review gate) ─────────────


@router.post("/admin/discovery/classify-sample")
async def classify_sample_endpoint(
    limit: int = Query(5, ge=1, le=20),
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """Classify `limit` unclassified active bots — return labels, do
    NOT write to DB. Use this to spot-check the classifier output
    before enabling the backfill loop.

    Throughput: each call is paced at the same 10/min throttle the
    loop uses, so `limit=5` takes ~30s wall-clock. Be patient.
    """
    _require_admin_key(x_admin_key)
    pool = await get_pool()
    logger.info("classify-sample: limit=%d", limit)
    rows = await classify_sample(pool, limit=limit)
    return {"sample": rows, "count": len(rows)}


# ─── classify-override (manual labels, validates the 5-value enum) ──────


class ClassifyOverrideRequest(BaseModel):
    """Operator override. At least one of (archetype, gender, category)
    must be present. Enum validation happens in
    `apply_admin_override`; we keep Pydantic free-form here so the
    route returns a useful 422 with the canonical valid set."""

    influencer_id: str = Field(..., min_length=1, max_length=255)
    archetype: str | None = None
    gender: str | None = None
    category: str | None = Field(None, max_length=100)


@router.post("/admin/discovery/classify-override")
async def classify_override_endpoint(
    body: ClassifyOverrideRequest,
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """Manually set archetype / gender / category on a single
    influencer. Bots written by this endpoint are excluded from the
    classifier loop (the loop only touches rows where BOTH
    `gender='unknown'` AND `archetype='unknown'`)."""
    _require_admin_key(x_admin_key)
    pool = await get_pool()
    try:
        row = await apply_admin_override(
            pool,
            influencer_id=body.influencer_id,
            archetype=body.archetype,
            gender=body.gender,
            category=body.category,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    logger.info(
        "classify-override: id=%s archetype=%s gender=%s category=%s",
        body.influencer_id,
        body.archetype,
        body.gender,
        body.category,
    )
    return {"updated": True, "row": row}
