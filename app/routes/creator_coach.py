"""Phase 7.5 — Soul File Coach endpoints (creator-facing).

Creators chat with an AI coach to improve their bots' personality. Auth
gate on every endpoint: the creator must own the bot (or the coach
conversation tied to the bot).
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from auth import get_current_user
from database import get_pool
from repositories import coach_repo, influencer_repo
from services import coach as coach_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/creator/coach", tags=["Soul File Coach"])


def _format_message(m: dict) -> dict:
    return {
        "id": str(m["id"]),
        "coach_conversation_id": str(m["coach_conversation_id"]),
        "role": m["role"],
        "content": m["content"],
        "proposed_changes": m.get("proposed_changes"),
        "reasoning": m.get("reasoning"),
        "created_at": m["created_at"].isoformat()
        if isinstance(m["created_at"], datetime)
        else m["created_at"],
    }


async def _load_owned_bot(pool, user_id: str, bot_id: str) -> dict:
    inf = await influencer_repo.get_by_id(pool, bot_id)
    if not inf:
        raise HTTPException(status_code=404, detail="Bot not found")
    if inf.get("parent_principal_id") != user_id:
        raise HTTPException(status_code=403, detail="You don't own this bot")
    return inf


async def _load_owned_session(pool, user_id: str, coach_conversation_id: str) -> dict:
    session = await coach_repo.get_session(pool, coach_conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="Coach session not found")
    if session["creator_user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not your coach session")
    return session


@router.post("/conversations/{bot_id}", status_code=201)
async def create_coach_session(bot_id: str, request: Request):
    """Start a new coach session for an owned bot."""
    user_id = get_current_user(request)
    pool = await get_pool()
    inf = await _load_owned_bot(pool, user_id, bot_id)
    session = await coach_repo.create_session(pool, user_id, bot_id)
    return {
        "id": str(session["id"]),
        "bot_id": session["bot_id"],
        "bot_name": inf.get("display_name"),
        "created_at": session["created_at"].isoformat()
        if isinstance(session["created_at"], datetime)
        else session["created_at"],
    }


@router.post("/conversations/{coach_conversation_id}/messages")
async def send_coach_message(coach_conversation_id: str, body: dict, request: Request):
    """Creator sends a message; coach replies with text and optionally a
    structured proposal (proposed_changes + reasoning)."""
    user_id = get_current_user(request)
    pool = await get_pool()
    session = await _load_owned_session(pool, user_id, coach_conversation_id)

    content = (body or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(status_code=422, detail="content is required")

    inf = await influencer_repo.get_by_id(pool, session["bot_id"])
    if not inf:
        raise HTTPException(status_code=410, detail="Underlying bot was deleted")

    # Save the creator's message first so the coach can see it in history
    creator_msg = await coach_repo.add_message(
        pool, coach_conversation_id, "creator", content.strip()
    )

    # Build context for the coach: prior session turns + last ~60 user-bot
    # messages across this bot's conversations (anonymized — no user_id in
    # the prompt). 60 is enough to cover 5-10 short conversations; the
    # coach service caps each line to 200 chars to stay under Gemini's
    # input budget.
    history = await coach_repo.list_messages(pool, coach_conversation_id)
    recent = await pool.fetch(
        """
        SELECT m.conversation_id, m.role, m.content, m.created_at
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE c.influencer_id = $1
        ORDER BY m.created_at DESC
        LIMIT 60
        """,
        session["bot_id"],
    )
    recent_rows = [dict(r) for r in recent]

    display, proposed, reasoning = await coach_service.coach_reply(
        bot_name=inf.get("display_name") or inf.get("name") or "this bot",
        bot_archetype=inf.get("category") or "general",
        current_instructions=inf.get("system_instructions") or "",
        recent_conv_rows=recent_rows,
        # Exclude the creator's just-saved message from history; it's the
        # latest_message slot in the meta-prompt instead
        session_history=[m for m in history if m["id"] != creator_msg["id"]],
        latest_message=content.strip(),
    )

    coach_msg = await coach_repo.add_message(
        pool,
        coach_conversation_id,
        "coach",
        display,
        proposed_changes=proposed,
        reasoning=reasoning,
    )
    await coach_repo.touch_session(pool, coach_conversation_id)

    return {
        "creator_message": _format_message(creator_msg),
        "coach_message": _format_message(coach_msg),
    }


@router.post("/conversations/{coach_conversation_id}/apply")
async def apply_coach_proposal(coach_conversation_id: str, request: Request):
    """Apply the most recent coach proposal to the bot's system_instructions.
    Records previous text in system_instructions_history for rollback."""
    user_id = get_current_user(request)
    pool = await get_pool()
    session = await _load_owned_session(pool, user_id, coach_conversation_id)

    proposal = await coach_repo.latest_proposal(pool, coach_conversation_id)
    if not proposal:
        raise HTTPException(
            status_code=409, detail="No proposal to apply in this session"
        )

    inf = await influencer_repo.get_by_id(pool, session["bot_id"])
    if not inf:
        raise HTTPException(status_code=410, detail="Underlying bot was deleted")

    previous = inf.get("system_instructions") or ""
    new_text = proposal["proposed_changes"] or ""
    if previous == new_text:
        raise HTTPException(
            status_code=409, detail="Proposed instructions equal current instructions"
        )

    # Atomic: write history first, then update bot. If the UPDATE fails we
    # have a paper trail of the intent (and the history row can be reversed
    # by hand). If history insert fails, we never touch the bot.
    history_row = await coach_repo.record_application(
        pool,
        bot_id=session["bot_id"],
        coach_conversation_id=coach_conversation_id,
        coach_message_id=str(proposal["id"]),
        previous_instructions=previous,
        new_instructions=new_text,
        applied_by=user_id,
    )
    await pool.execute(
        "UPDATE ai_influencers SET system_instructions = $1 WHERE id = $2",
        new_text,
        session["bot_id"],
    )

    return {
        "applied": True,
        "history_id": str(history_row["id"]),
        "previous_instructions": previous,
        "new_instructions": new_text,
        "applied_at": history_row["applied_at"].isoformat()
        if isinstance(history_row["applied_at"], datetime)
        else history_row["applied_at"],
    }


@router.get("/conversations/{coach_conversation_id}/messages")
async def list_coach_messages(coach_conversation_id: str, request: Request):
    user_id = get_current_user(request)
    pool = await get_pool()
    await _load_owned_session(pool, user_id, coach_conversation_id)
    messages = await coach_repo.list_messages(pool, coach_conversation_id)
    return {
        "coach_conversation_id": coach_conversation_id,
        "messages": [_format_message(m) for m in messages],
        "total": len(messages),
    }
