"""Diagnostic endpoints for user_memories.

Phase 4.4 surfaces stored memories so creators and end-users can see what the
AI 'remembers' about them. Read-only; write is via background extraction in
services/memory.py.
"""

import logging

from fastapi import APIRouter, Request

from auth import get_current_user
from database import get_pool
from repositories import memory_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/users/me", tags=["User Memories"])


def _format(row: dict) -> dict:
    return {
        "category": row["category"],
        "key": row["key"],
        "value": row["value"],
        "confidence": row.get("confidence", 1.0),
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@router.get("/memories")
async def list_my_memories(request: Request, influencer_id: str | None = None):
    """List the calling user's stored memories.

    If influencer_id is given, returns memories for that influencer + global ones.
    Otherwise returns global-only (influencer_id IS NULL).
    """
    user_id = get_current_user(request)
    pool = await get_pool()
    if influencer_id:
        rows = await memory_repo.get_all_for_user(pool, user_id, influencer_id)
    else:
        rows = await memory_repo.get_for_user_global(pool, user_id)
    return {"memories": [_format(r) for r in rows], "total": len(rows)}
