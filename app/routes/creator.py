"""Creator Studio: manage AI influencers, view analytics, edit Soul Files."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Query

from database import get_pool
from auth import get_current_user
from repositories import influencer_repo, quality_score_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/creator", tags=["Creator Studio"])


@router.get("/influencers")
async def list_my_influencers(request: Request):
    """List all influencers created by the authenticated user."""
    user_id = get_current_user(request)
    pool = await get_pool()

    rows = await pool.fetch(
        """
        SELECT i.id, i.name, i.display_name, i.avatar_url, i.description,
               i.category, i.is_active, i.is_nsfw, i.created_at, i.updated_at,
               COUNT(DISTINCT c.id) as conversation_count,
               COUNT(m.id) FILTER (WHERE m.role = 'user') as message_count
        FROM ai_influencers i
        LEFT JOIN conversations c ON i.id = c.influencer_id
        LEFT JOIN messages m ON c.id = m.conversation_id
        WHERE i.parent_principal_id = $1
        GROUP BY i.id
        ORDER BY i.created_at DESC
        """,
        user_id,
    )

    influencers = []
    for r in rows:
        created_at = r["created_at"]
        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()
        influencers.append(
            {
                "id": r["id"],
                "name": r["name"],
                "display_name": r["display_name"],
                "avatar_url": r.get("avatar_url"),
                "description": r.get("description"),
                "category": r.get("category"),
                "is_active": r["is_active"],
                "is_nsfw": r["is_nsfw"],
                "created_at": created_at,
                "conversation_count": r["conversation_count"],
                "message_count": r["message_count"],
            }
        )

    return {"influencers": influencers, "total": len(influencers)}


@router.get("/influencers/{influencer_id}/analytics")
async def get_influencer_analytics(influencer_id: str, request: Request):
    """Get analytics for a specific influencer (creator only)."""
    user_id = get_current_user(request)
    pool = await get_pool()

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(status_code=403, detail="Not your influencer")

    stats = await pool.fetchrow(
        """
        SELECT
            COUNT(DISTINCT c.id) as total_conversations,
            COUNT(DISTINCT c.user_id) as unique_users,
            COUNT(m.id) FILTER (WHERE m.role = 'user') as total_user_messages,
            COUNT(m.id) FILTER (WHERE m.role = 'assistant') as total_bot_messages,
            COUNT(DISTINCT c.id) FILTER (WHERE c.updated_at > NOW() - INTERVAL '24 hours') as active_conversations_24h,
            COUNT(DISTINCT c.id) FILTER (WHERE c.updated_at > NOW() - INTERVAL '7 days') as active_conversations_7d,
            AVG(EXTRACT(EPOCH FROM (m2.created_at - m1.created_at))) as avg_response_time_sec
        FROM conversations c
        LEFT JOIN messages m ON c.id = m.conversation_id
        LEFT JOIN LATERAL (
            SELECT created_at FROM messages WHERE conversation_id = c.id AND role = 'user' ORDER BY created_at DESC LIMIT 1
        ) m1 ON true
        LEFT JOIN LATERAL (
            SELECT created_at FROM messages WHERE conversation_id = c.id AND role = 'assistant' AND created_at > m1.created_at ORDER BY created_at ASC LIMIT 1
        ) m2 ON true
        WHERE c.influencer_id = $1
        """,
        influencer_id,
    )

    return {
        "influencer_id": influencer_id,
        "total_conversations": stats["total_conversations"],
        "unique_users": stats["unique_users"],
        "total_user_messages": stats["total_user_messages"],
        "total_bot_messages": stats["total_bot_messages"],
        "active_conversations_24h": stats["active_conversations_24h"],
        "active_conversations_7d": stats["active_conversations_7d"],
        "avg_response_time_sec": round(stats["avg_response_time_sec"] or 0, 2),
    }


@router.get("/influencers/{influencer_id}/conversations")
async def list_influencer_conversations(
    influencer_id: str,
    request: Request,
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
):
    """List all conversations with an influencer (creator's view for Chat-as-Human)."""
    user_id = get_current_user(request)
    pool = await get_pool()

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(status_code=403, detail="Not your influencer")

    rows = await pool.fetch(
        """
        SELECT c.id, c.user_id, c.created_at, c.updated_at,
               COUNT(m.id) as message_count,
               (SELECT content FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC LIMIT 1) as last_message
        FROM conversations c
        LEFT JOIN messages m ON c.id = m.conversation_id
        WHERE c.influencer_id = $1
        GROUP BY c.id
        ORDER BY c.updated_at DESC
        LIMIT $2 OFFSET $3
        """,
        influencer_id,
        limit,
        offset,
    )

    total = await pool.fetchval(
        "SELECT COUNT(*) FROM conversations WHERE influencer_id = $1",
        influencer_id,
    )

    conversations = []
    for r in rows:
        created_at = r["created_at"]
        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()
        updated_at = r["updated_at"]
        if isinstance(updated_at, datetime):
            updated_at = updated_at.isoformat()
        conversations.append(
            {
                "id": r["id"],
                "user_id": r["user_id"],
                "created_at": created_at,
                "updated_at": updated_at,
                "message_count": r["message_count"],
                "last_message_preview": (r["last_message"] or "")[:100],
            }
        )

    return {
        "conversations": conversations,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/influencers/{influencer_id}/soul-file")
async def get_soul_file(influencer_id: str, request: Request):
    """Get the current Soul File (system instructions) for editing."""
    user_id = get_current_user(request)
    pool = await get_pool()

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(status_code=403, detail="Not your influencer")

    from services.moderation import strip_guardrails

    return {
        "influencer_id": influencer_id,
        "display_name": inf["display_name"],
        "system_instructions": strip_guardrails(inf.get("system_instructions", "")),
        "category": inf.get("category"),
        "personality_traits": inf.get("personality_traits"),
        "initial_greeting": inf.get("initial_greeting"),
        "suggested_messages": inf.get("suggested_messages"),
    }


@router.get("/influencers/{influencer_id}/quality-score")
async def get_quality_score(influencer_id: str, request: Request):
    """Phase 7.7: latest bot quality score for an owned bot.

    Returns the most recent row from bot_quality_scores. If the bot has never
    been scored (new bot, no traffic), returns null fields with a hint.
    Owner-only auth — non-owners get 403 same as the rest of /creator.
    """
    user_id = get_current_user(request)
    pool = await get_pool()

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(status_code=403, detail="Not your influencer")

    row = await quality_score_repo.latest_for_bot(pool, influencer_id)
    if not row:
        return {
            "influencer_id": influencer_id,
            "score_overall": None,
            "score_in_character": None,
            "score_response_quality": None,
            "score_engagement": None,
            "last_n_conversations": 0,
            "sample_size": 0,
            "scored_at": None,
            "hint": "No score yet — bot needs sampled conversations + a nightly scoring pass.",
        }

    created_at = row["created_at"]
    return {
        "influencer_id": influencer_id,
        "score_overall": row["score_overall"],
        "score_in_character": row["score_in_character"],
        "score_response_quality": row["score_response_quality"],
        "score_engagement": row["score_engagement"],
        "last_n_conversations": row["last_n_conversations"],
        "sample_size": row["sample_size"],
        "scored_at": created_at.isoformat()
        if isinstance(created_at, datetime)
        else created_at,
    }


@router.get("/influencers/{influencer_id}/recommendations")
async def get_recommendations(influencer_id: str, request: Request):
    """Phase 7.8: 2-3 specific, actionable Soul File improvements grounded
    in the latest quality score + a sample of recent bot replies.

    Returns `{recommendations: [...], hint: ?}` — never a 5xx on model
    failure. Owner-only.
    """
    user_id = get_current_user(request)
    pool = await get_pool()

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Influencer not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(status_code=403, detail="Not your influencer")

    score = await quality_score_repo.latest_for_bot(pool, influencer_id)

    # Sample recent non-proactive bot replies across this bot's conversations
    # (last 7 days). Anonymized — we send only the bot's own text to the
    # model, no user_id, no user message.
    rows = await pool.fetch(
        """
        SELECT m.content
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE c.influencer_id = $1
          AND m.role = 'assistant'
          AND COALESCE(m.is_proactive, FALSE) = FALSE
          AND m.created_at > NOW() - INTERVAL '7 days'
          AND COALESCE(m.content, '') <> ''
        ORDER BY m.created_at DESC
        LIMIT 30
        """,
        influencer_id,
    )
    sample_replies = [dict(r) for r in rows]

    from services import recommendations as rec_service

    recs = await rec_service.generate_recommendations(
        bot_name=inf.get("display_name") or inf.get("name") or "this bot",
        bot_archetype=inf.get("category") or "general",
        current_instructions=inf.get("system_instructions") or "",
        quality_score=score,
        sample_bot_replies=sample_replies,
    )

    hint = None
    if not recs:
        hint = "No recommendations available — bot may be new, or the model didn't return valid output. Retry later."

    return {
        "influencer_id": influencer_id,
        "recommendations": recs,
        "based_on_score": score is not None,
        "sample_replies_count": len(sample_replies),
        "hint": hint,
    }
