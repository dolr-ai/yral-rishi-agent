"""Proactive messages: bot texts first.

Trigger types:
- welcome_back: user hasn't chatted in 24h → bot sends a check-in
- follow_up: bot follows up on a topic from the last conversation
- morning_greeting: daily morning message from favorite bots

Messages are generated via Gemini using the influencer's personality,
then saved as assistant messages and delivered via push notification.
"""

import asyncio
import logging

from repositories import message_repo, influencer_repo
from services import ai_client, push_notifications, websocket_manager, memory

logger = logging.getLogger(__name__)

PROACTIVE_PROMPT = """You are {display_name}. Generate a short, natural message to re-engage a user you haven't heard from in a while.

Context about this user:
{user_context}

Rules:
- Stay in character based on your personality
- Be warm and casual, not needy or desperate
- Reference something specific about the user if possible
- Keep it under 2 sentences
- End with a question or hook that invites a reply
- If you have no context about the user, send a general friendly check-in

Generate ONLY the message text, nothing else."""


async def generate_proactive_message(
    pool,
    influencer_id: str,
    user_id: str,
    conversation_id: str,
    trigger_type: str = "welcome_back",
) -> str | None:
    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf or inf.get("is_active") == "discontinued":
        return None

    memories = await memory.get_memories_for_prompt(pool, user_id, influencer_id)
    user_context = (
        "\n".join(f"- {k}: {v}" for k, v in memories.items())
        if memories
        else "No previous context available."
    )

    prompt = PROACTIVE_PROMPT.format(
        display_name=inf.get("display_name", "Bot"),
        user_context=user_context,
    )

    llm_result = await ai_client.generate_response(
        system_instructions=inf.get("system_instructions", ""),
        conversation_history=[],
        user_message=prompt,
        is_nsfw=inf.get("is_nsfw", False),
        user_id=user_id,
        conversation_id=conversation_id,
    )

    if llm_result.is_fallback:
        return None

    return llm_result.content


async def send_proactive_message(
    pool,
    influencer_id: str,
    user_id: str,
    conversation_id: str,
    trigger_type: str = "welcome_back",
):
    """Generate and deliver a proactive message from an influencer to a user."""
    content = await generate_proactive_message(
        pool, influencer_id, user_id, conversation_id, trigger_type
    )
    if not content:
        return None

    msg = await message_repo.create(
        pool,
        conversation_id=conversation_id,
        role="assistant",
        content=content,
        message_type="text",
        sender_id=influencer_id,
    )

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    display_name = inf.get("display_name", "AI") if inf else "AI"

    unread_count = await message_repo.count_unread(pool, conversation_id)
    asyncio.create_task(
        websocket_manager.broadcast_new_message(
            user_id=user_id,
            conversation_id=conversation_id,
            message={
                "id": msg["id"],
                "conversation_id": conversation_id,
                "role": "assistant",
                "content": content,
                "message_type": "text",
                "created_at": msg["created_at"].isoformat()
                if hasattr(msg["created_at"], "isoformat")
                else str(msg["created_at"]),
            },
            influencer={
                "id": influencer_id,
                "display_name": display_name,
                "avatar_url": inf.get("avatar_url") if inf else None,
                "is_online": True,
            },
            unread_count=unread_count,
        )
    )

    asyncio.create_task(
        push_notifications.send_new_message_notification(
            user_id=user_id,
            influencer_name=display_name,
            message_content=content,
            conversation_id=conversation_id,
            influencer_id=influencer_id,
        )
    )

    return msg


async def find_inactive_conversations(pool, hours: int = 24, limit: int = 100):
    """Find conversations where the user hasn't sent a message in N hours."""
    rows = await pool.fetch(
        """
        SELECT c.id, c.user_id, c.influencer_id
        FROM conversations c
        WHERE c.conversation_type = 'ai_chat'
          AND c.influencer_id IS NOT NULL
          AND c.updated_at < NOW() - INTERVAL '1 hour' * $1
          AND c.updated_at > NOW() - INTERVAL '1 hour' * ($1 * 3)
          AND NOT EXISTS (
              SELECT 1 FROM proactive_messages pm
              WHERE pm.conversation_id = c.id
              AND pm.status = 'delivered'
              AND pm.created_at > NOW() - INTERVAL '1 hour' * $1
          )
        ORDER BY c.updated_at DESC
        LIMIT $2
        """,
        hours,
        limit,
    )
    return [dict(r) for r in rows]
