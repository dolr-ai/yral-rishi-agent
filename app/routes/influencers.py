import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from database import get_pool
from repositories import influencer_repo
from services import moderation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Influencers"])


def _format_influencer_response(inf: dict) -> dict:
    system_instructions = inf.get("system_instructions", "")
    system_prompt_display = moderation.strip_guardrails(system_instructions) if system_instructions else ""

    return {
        "id": inf["id"],
        "name": inf["name"],
        "display_name": inf["display_name"],
        "avatar_url": inf.get("avatar_url") or "",
        "description": inf.get("description") or "",
        "category": inf.get("category") or "",
        "is_active": inf.get("is_active", "active"),
        "parent_principal_id": inf.get("parent_principal_id"),
        "source": inf.get("source"),
        "system_prompt": system_prompt_display,
        "created_at": inf["created_at"].isoformat() if isinstance(inf["created_at"], datetime) else str(inf["created_at"]),
        "conversation_count": inf.get("conversation_count"),
        "message_count": inf.get("message_count"),
    }


def _format_influencer_detail(inf: dict) -> dict:
    personality_traits = inf.get("personality_traits")
    if isinstance(personality_traits, str):
        try:
            personality_traits = json.loads(personality_traits)
        except (json.JSONDecodeError, TypeError):
            personality_traits = {}

    suggested_messages = inf.get("suggested_messages")
    if isinstance(suggested_messages, str):
        try:
            suggested_messages = json.loads(suggested_messages)
        except (json.JSONDecodeError, TypeError):
            suggested_messages = []

    metadata = inf.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    system_instructions = inf.get("system_instructions", "")
    system_instructions_display = moderation.strip_guardrails(system_instructions)

    return {
        "id": inf["id"],
        "name": inf["name"],
        "display_name": inf["display_name"],
        "avatar_url": inf.get("avatar_url"),
        "description": inf.get("description"),
        "category": inf.get("category"),
        "system_instructions": system_instructions_display,
        "personality_traits": personality_traits,
        "initial_greeting": inf.get("initial_greeting"),
        "suggested_messages": suggested_messages,
        "is_active": inf.get("is_active", "active"),
        "is_nsfw": inf.get("is_nsfw", False),
        "parent_principal_id": inf.get("parent_principal_id"),
        "source": inf.get("source"),
        "created_at": inf["created_at"].isoformat() if isinstance(inf["created_at"], datetime) else str(inf["created_at"]),
        "updated_at": inf["updated_at"].isoformat() if isinstance(inf["updated_at"], datetime) else str(inf["updated_at"]),
        "metadata": metadata,
        "conversation_count": inf.get("conversation_count"),
    }


@router.get("/influencers")
async def list_influencers(
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
):
    try:
        pool = await get_pool()
        influencers = await influencer_repo.list_all(pool, limit, offset)
        total = await influencer_repo.count_all(pool)

        response = JSONResponse(content={
            "influencers": [_format_influencer_response(i) for i in influencers],
            "total": total,
            "limit": limit,
            "offset": offset,
        })
        response.headers["Cache-Control"] = "public, max-age=300"
        return response
    except Exception as e:
        logger.error(f"list_influencers failed: {type(e).__name__}: {e}")
        import sentry_sdk
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=f"Internal error: {type(e).__name__}: {e}")


@router.get("/influencers/trending")
async def list_trending(
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
):
    pool = await get_pool()
    influencers = await influencer_repo.list_trending(pool, limit, offset)
    total = await influencer_repo.count_trending(pool)

    formatted = []
    for i in influencers:
        inf = _format_influencer_response(i)
        inf["message_count"] = i.get("message_count", 0)
        inf["conversation_count"] = i.get("conversation_count", 0)
        formatted.append(inf)

    response = JSONResponse(content={
        "influencers": formatted,
        "total": total,
        "limit": limit,
        "offset": offset,
    })
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@router.get("/influencers/{influencer_id}")
async def get_influencer(influencer_id: str):
    pool = await get_pool()
    inf = await influencer_repo.get_with_conversation_count(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")

    response = JSONResponse(content=_format_influencer_detail(inf))
    response.headers["Cache-Control"] = "public, max-age=300"
    return response
