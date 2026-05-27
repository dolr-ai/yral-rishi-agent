"""Creator earnings: view revenue from AI influencer interactions."""

import logging
from datetime import datetime

from fastapi import APIRouter, Request, Query

from database import get_pool
from auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/creator", tags=["Creator Earnings"])


@router.get("/earnings")
async def get_earnings_summary(request: Request):
    """Get total earnings across all influencers owned by this creator."""
    user_id = get_current_user(request)
    pool = await get_pool()

    row = await pool.fetchrow(
        """
        SELECT
            COALESCE(SUM(amount_cents), 0) as total_cents,
            COALESCE(SUM(amount_cents) FILTER (WHERE status = 'confirmed'), 0) as confirmed_cents,
            COALESCE(SUM(amount_cents) FILTER (WHERE status = 'paid_out'), 0) as paid_out_cents,
            COALESCE(SUM(amount_cents) FILTER (WHERE status = 'pending'), 0) as pending_cents,
            COUNT(DISTINCT influencer_id) as earning_influencers
        FROM creator_earnings
        WHERE creator_id = $1
        """,
        user_id,
    )

    return {
        "total_cents": row["total_cents"],
        "confirmed_cents": row["confirmed_cents"],
        "paid_out_cents": row["paid_out_cents"],
        "pending_cents": row["pending_cents"],
        "earning_influencers": row["earning_influencers"],
        "currency": "USD",
    }


@router.get("/earnings/by-influencer")
async def get_earnings_by_influencer(request: Request):
    """Get earnings breakdown by influencer."""
    user_id = get_current_user(request)
    pool = await get_pool()

    rows = await pool.fetch(
        """
        SELECT e.influencer_id, i.display_name, i.avatar_url,
               SUM(e.amount_cents) as total_cents,
               SUM(e.amount_cents) FILTER (WHERE e.status = 'confirmed') as confirmed_cents,
               MAX(e.period_end) as last_earning_period
        FROM creator_earnings e
        JOIN ai_influencers i ON e.influencer_id = i.id
        WHERE e.creator_id = $1
        GROUP BY e.influencer_id, i.display_name, i.avatar_url
        ORDER BY total_cents DESC
        """,
        user_id,
    )

    return {
        "influencers": [
            {
                "influencer_id": r["influencer_id"],
                "display_name": r["display_name"],
                "avatar_url": r.get("avatar_url"),
                "total_cents": r["total_cents"],
                "confirmed_cents": r["confirmed_cents"],
                "last_earning_period": r["last_earning_period"].isoformat()
                if isinstance(r["last_earning_period"], datetime)
                else str(r["last_earning_period"] or ""),
            }
            for r in rows
        ]
    }


@router.get("/earnings/history")
async def get_earnings_history(
    request: Request,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Get detailed earnings history with pagination."""
    user_id = get_current_user(request)
    pool = await get_pool()

    rows = await pool.fetch(
        """
        SELECT e.id, e.influencer_id, i.display_name,
               e.amount_cents, e.currency, e.source,
               e.period_start, e.period_end, e.status, e.created_at
        FROM creator_earnings e
        JOIN ai_influencers i ON e.influencer_id = i.id
        WHERE e.creator_id = $1
        ORDER BY e.created_at DESC
        LIMIT $2 OFFSET $3
        """,
        user_id,
        limit,
        offset,
    )

    total = await pool.fetchval(
        "SELECT COUNT(*) FROM creator_earnings WHERE creator_id = $1",
        user_id,
    )

    return {
        "earnings": [
            {
                "id": r["id"],
                "influencer_id": r["influencer_id"],
                "display_name": r["display_name"],
                "amount_cents": r["amount_cents"],
                "currency": r["currency"],
                "source": r["source"],
                "period_start": r["period_start"].isoformat()
                if isinstance(r["period_start"], datetime)
                else str(r["period_start"]),
                "period_end": r["period_end"].isoformat()
                if isinstance(r["period_end"], datetime)
                else str(r["period_end"]),
                "status": r["status"],
                "created_at": r["created_at"].isoformat()
                if isinstance(r["created_at"], datetime)
                else str(r["created_at"]),
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
