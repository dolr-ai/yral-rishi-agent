import logging
from datetime import datetime

import httpx
from fastapi import APIRouter, Request, Query

from database import get_pool
from auth import get_current_user
from repositories import influencer_repo, conversation_repo
import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/chat", tags=["Chat v2 — Bot-aware"])


async def _is_bot(pool, principal_id: str) -> bool:
    inf = await influencer_repo.get_by_id(pool, principal_id)
    return inf is not None


async def _fetch_user_profiles(user_ids: list[str]) -> dict[str, dict]:
    if not user_ids or not config.METADATA_URL:
        return {}

    profiles = {
        uid: {"principal_id": uid, "username": None, "profile_picture_url": None}
        for uid in user_ids
    }

    try:
        url = f"{config.METADATA_URL.rstrip('/')}/metadata-bulk"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={"users": user_ids})
            if response.status_code == 200:
                data = response.json()
                ok_data = data.get("Ok", {})
                if isinstance(ok_data, dict):
                    for principal, meta in ok_data.items():
                        if principal in profiles:
                            username = meta.get("user_name", "")
                            if username and username.strip():
                                profiles[principal]["username"] = username.strip()
    except Exception as e:
        logger.warning(f"Failed to fetch user profiles: {e}")

    return profiles


def _format_dt(dt) -> str:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt) if dt else ""


@router.get("/conversations")
async def list_conversations_v2(
    request: Request,
    principal: str = Query(..., description="Principal ID (user or bot)"),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    influencer_id: str | None = Query(default=None),
):
    get_current_user(request)
    pool = await get_pool()

    is_bot_caller = await _is_bot(pool, principal)

    if is_bot_caller:
        return await _list_for_bot(pool, principal, limit, offset)
    else:
        return await _list_for_user(pool, principal, influencer_id, limit, offset)


async def _list_for_user(
    pool,
    user_id: str,
    influencer_id: str | None,
    limit: int,
    offset: int,
) -> dict:
    conversations = await conversation_repo.list_by_user(
        pool,
        user_id,
        influencer_id,
        limit,
        offset,
    )
    total = await conversation_repo.count_by_user(pool, user_id, influencer_id)

    conv_ids = [c["id"] for c in conversations]
    last_messages = (
        await conversation_repo.get_last_messages_batch(pool, conv_ids)
        if conv_ids
        else []
    )

    last_msg_map = {}
    for lm in last_messages:
        last_msg_map[lm["conversation_id"]] = {
            "content": lm.get("content") or "",
            "role": lm["role"],
            "created_at": _format_dt(lm["created_at"]),
        }

    formatted = []
    for c in conversations:
        influencer_info = {
            "id": c.get("inf_id") or c.get("influencer_id") or "",
            "name": c.get("inf_name") or "",
            "display_name": c.get("inf_display_name") or "",
            "avatar_url": c.get("inf_avatar_url"),
            "is_online": c.get("inf_is_active") != "discontinued"
            if c.get("inf_is_active")
            else True,
        }
        formatted.append(
            {
                "id": c["id"],
                "user_id": c["user_id"],
                "influencer_id": c.get("influencer_id"),
                "influencer": influencer_info,
                "user": None,
                "created_at": _format_dt(c["created_at"]),
                "updated_at": _format_dt(c["updated_at"]),
                "message_count": c.get("message_count", 0),
                "unread_count": c.get("unread_count", 0),
                "last_message": last_msg_map.get(c["id"]),
            }
        )

    return {
        "conversations": formatted,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def _list_for_bot(pool, bot_principal: str, limit: int, offset: int) -> dict:
    conversations = await conversation_repo.list_by_influencer(
        pool,
        bot_principal,
        limit,
        offset,
    )
    total = await conversation_repo.count_by_influencer(pool, bot_principal)

    conv_ids = [c["id"] for c in conversations]
    last_messages = (
        await conversation_repo.get_last_messages_batch(pool, conv_ids)
        if conv_ids
        else []
    )

    last_msg_map = {}
    for lm in last_messages:
        last_msg_map[lm["conversation_id"]] = {
            "content": lm.get("content") or "",
            "role": lm["role"],
            "created_at": _format_dt(lm["created_at"]),
        }

    unique_user_ids = list(set(c["user_id"] for c in conversations))
    user_profiles = await _fetch_user_profiles(unique_user_ids)

    formatted = []
    for c in conversations:
        user_info = user_profiles.get(
            c["user_id"],
            {
                "principal_id": c["user_id"],
                "username": None,
                "profile_picture_url": None,
            },
        )
        formatted.append(
            {
                "id": c["id"],
                "user_id": c["user_id"],
                "influencer_id": c.get("influencer_id") or bot_principal,
                "influencer": None,
                "user": user_info,
                "created_at": _format_dt(c["created_at"]),
                "updated_at": _format_dt(c["updated_at"]),
                "message_count": c.get("message_count", 0),
                "unread_count": c.get("unread_count", 0),
                "last_message": last_msg_map.get(c["id"]),
            }
        )

    return {
        "conversations": formatted,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
