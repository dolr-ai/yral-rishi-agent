"""Phase 21γ.P34.M0 — Discovery Feed admin pins.

Three endpoints + a small repository. M0 ships the operator surface
for `trending_overrides`. The actual feed composer (M2) will read this
table; until then these endpoints exist + work but change ZERO
user-visible behaviour.

Endpoints:
  POST /api/v2/admin/discovery/pin     {influencer_id, pinned_rank,
                                        note?, expires_at?}
  POST /api/v2/admin/discovery/unpin   {influencer_id}
  GET  /api/v2/admin/discovery/pins    → list ordered by rank

Auth: `X-Admin-Key` header (same pattern as /admin/influencers in
`app/routes/influencers.py`). Constant-time compare against
`config.ADMIN_KEY`.

Mobile contract: there is no mobile contract for M0. These are
operator-only endpoints — mobile sees the effect via the M2 feed
composer once that ships.
"""

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

import config
from database import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin — Discovery feed"])


# ─── auth ───────────────────────────────────────────────────────────────


def _require_admin_key(x_admin_key: str | None) -> None:
    if (
        not config.ADMIN_KEY
        or not x_admin_key
        or not secrets.compare_digest(x_admin_key, config.ADMIN_KEY)
    ):
        raise HTTPException(status_code=403, detail="Invalid admin key")


# ─── request models ────────────────────────────────────────────────────


class PinRequest(BaseModel):
    """Rank is 1-based — `1` = top slot. M2 composer reserves slots
    1..N for pinned bots in rank order, then fills the rest from the
    computed ranking. Out-of-range values rejected at the DB CHECK
    constraint (1..1000) — the bounds here mirror that."""

    influencer_id: str = Field(..., min_length=1, max_length=255)
    pinned_rank: int = Field(..., ge=1, le=1000)
    note: str | None = None
    expires_at: datetime | None = None


class UnpinRequest(BaseModel):
    influencer_id: str = Field(..., min_length=1, max_length=255)


# ─── repository (inline — M0 is small enough to not need a separate file) ─


async def _upsert_pin(
    pool,
    *,
    influencer_id: str,
    pinned_rank: int,
    note: str | None,
    expires_at: datetime | None,
    created_by: str,
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO trending_overrides
            (influencer_id, pinned_rank, note, expires_at, created_by,
             created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
        ON CONFLICT (influencer_id) DO UPDATE SET
            pinned_rank = EXCLUDED.pinned_rank,
            note        = EXCLUDED.note,
            expires_at  = EXCLUDED.expires_at,
            created_by  = EXCLUDED.created_by,
            updated_at  = NOW()
        RETURNING influencer_id, pinned_rank, note, expires_at,
                  created_by, created_at, updated_at
        """,
        influencer_id,
        pinned_rank,
        note,
        expires_at,
        created_by,
    )
    return dict(row)


async def _delete_pin(pool, influencer_id: str) -> bool:
    status = await pool.execute(
        "DELETE FROM trending_overrides WHERE influencer_id = $1",
        influencer_id,
    )
    # asyncpg returns 'DELETE n' as the status string; treat n>0 as
    # "actually deleted" so the API can distinguish unpin-of-missing
    # from unpin-of-existing.
    return status.endswith("0") is False


async def _list_pins(pool) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT t.influencer_id, t.pinned_rank, t.note, t.expires_at,
               t.created_by, t.created_at, t.updated_at,
               i.display_name AS influencer_display_name
        FROM trending_overrides t
        LEFT JOIN ai_influencers i ON i.id = t.influencer_id
        ORDER BY t.pinned_rank ASC
        """
    )
    return [dict(r) for r in rows]


# ─── serialization ──────────────────────────────────────────────────────


def _serialize_pin(row: dict) -> dict:
    """Render datetimes as ISO + drop None fields the operator doesn't
    care about. Same shape for POST response + GET list items."""
    return {
        "influencer_id": row["influencer_id"],
        "influencer_display_name": row.get("influencer_display_name"),
        "pinned_rank": row["pinned_rank"],
        "note": row.get("note"),
        "expires_at": (
            row["expires_at"].isoformat() if row.get("expires_at") else None
        ),
        "created_by": row.get("created_by"),
        "created_at": (
            row["created_at"].isoformat() if row.get("created_at") else None
        ),
        "updated_at": (
            row["updated_at"].isoformat() if row.get("updated_at") else None
        ),
    }


# ─── endpoints ──────────────────────────────────────────────────────────


@router.post("/api/v2/admin/discovery/pin")
async def admin_pin(
    body: PinRequest,
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """Upsert a pin. Same influencer_id called twice = the second call
    wins (note/rank/expires_at all updated, created_by stamped to the
    most recent caller). Idempotent by design — Rishi may re-pin to
    bump rank or extend an expiry."""
    _require_admin_key(x_admin_key)
    pool = await get_pool()

    # Verify the influencer exists. The FK would catch it on INSERT, but
    # a 404 with a clear message beats a 500 from the constraint trip.
    exists = await pool.fetchval(
        "SELECT 1 FROM ai_influencers WHERE id = $1", body.influencer_id
    )
    if not exists:
        raise HTTPException(
            status_code=404,
            detail=f"Influencer not found: {body.influencer_id!r}",
        )

    # Normalize expires_at to UTC if the caller sent a naive timestamp.
    # asyncpg requires tz-aware for TIMESTAMPTZ.
    expires_at = body.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    row = await _upsert_pin(
        pool,
        influencer_id=body.influencer_id,
        pinned_rank=body.pinned_rank,
        note=body.note,
        expires_at=expires_at,
        created_by="admin",
    )
    logger.info(
        "discovery pin: id=%s rank=%s expires_at=%s",
        body.influencer_id,
        body.pinned_rank,
        expires_at,
    )
    return _serialize_pin(row)


@router.post("/api/v2/admin/discovery/unpin")
async def admin_unpin(
    body: UnpinRequest,
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """Remove a pin. Returns 200 + `{deleted: true|false}` either way
    — unpinning a non-pinned bot is a no-op, not an error."""
    _require_admin_key(x_admin_key)
    pool = await get_pool()
    deleted = await _delete_pin(pool, body.influencer_id)
    logger.info("discovery unpin: id=%s deleted=%s", body.influencer_id, deleted)
    return {"influencer_id": body.influencer_id, "deleted": deleted}


@router.get("/api/v2/admin/discovery/pins")
async def admin_list_pins(
    x_admin_key: str = Header(None, alias="X-Admin-Key"),
):
    """List current pins ordered by rank. Cheap on the rank index."""
    _require_admin_key(x_admin_key)
    pool = await get_pool()
    rows = await _list_pins(pool)
    return {"pins": [_serialize_pin(r) for r in rows], "count": len(rows)}
