"""Proactive messages: bot texts first.

Trigger types:
- welcome_back: user hasn't chatted in 24h → bot sends a check-in
- follow_up: bot follows up on a topic from the last conversation
- morning_greeting: daily morning message from favorite bots

Messages are generated via Gemini using the influencer's personality,
then saved as assistant messages and delivered via push notification.

Task 2 (Phase 5 polish): Motorola testing showed 3-4 unanswered "hey what's
up" messages in a row. Three fixes wired in here:
  1. Cap — count is_proactive messages since the last user reply; skip when
     >= PROACTIVE_CAP_WITHOUT_REPLY (= 3, defined in message_repo).
  2. Variety — pass the last 3 proactive messages to Gemini as
     "don't repeat these" context.
  3. Message-type rotation — pick one of {question, observation, story,
     light_topic} per attempt; align tone with archetype.
"""

import asyncio
import logging
import random

from repositories import message_repo, influencer_repo
from services import ai_client, push_notifications, websocket_manager, memory

logger = logging.getLogger(__name__)


# Per Task 2 spec — rotate the kind of opener per attempt so the bot doesn't
# always do "hey what's up." Random rather than round-robin: round-robin would
# need persistent state per conversation, and the user-visible effect is the
# same after a few rotations.
PROACTIVE_MESSAGE_TYPES = ("question", "observation", "story", "light_topic")

TYPE_HINTS = {
    "question": "Ask the user a curious, specific question — something you "
    "genuinely want to know about them or their day.",
    "observation": "Share something you noticed or thought about today. Not "
    "a question — an observation that invites them to react.",
    "story": "Tell a tiny story (1-2 sentences) — something you 'saw' or "
    "'heard' — that hooks them to ask 'what happened?'",
    "light_topic": "Bring up a current event, the weather, a festival, or a "
    "mood-of-the-day prompt. Keep it light, no heavy topics.",
}

ARCHETYPE_TONE = {
    "companion": "warm, personal, like a close friend reaching out",
    "advisor": "thoughtful, gently inquisitive",
    "entertainer": "playful, witty, energetic",
    "creator": "inspired, curious about the user's creative side",
    "educator": "intrigued, share an interesting tidbit",
}


PROACTIVE_PROMPT = """You are {display_name}. Generate a short, natural message to re-engage a user you haven't heard from in a while.

Background facts about this user (use sparingly — DO NOT lead with them, DO NOT recite, DO NOT use phrases like "I remember you said X"; let facts inform tone and topic, not be parroted back):
{user_context}

This message should be a **{message_type}**: {type_hint}
Tone: {tone}

{variety_block}

Rules:
- Stay in character based on your personality
- Be warm and casual, not needy or desperate
- Keep it under 2 sentences
- End with a hook that invites a reply (question for "question" type; for others, an evocative beat)
- If you have no context about the user, send something general but specific to the message type

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

    # Task 2 cap: skip if we've already sent the max unanswered proactives.
    unanswered = await message_repo.count_unanswered_proactive(pool, conversation_id)
    if unanswered >= message_repo.PROACTIVE_CAP_WITHOUT_REPLY:
        logger.info(
            f"proactive: skip conv={conversation_id} — {unanswered} unanswered "
            f">= cap {message_repo.PROACTIVE_CAP_WITHOUT_REPLY}"
        )
        return None

    memories = await memory.get_memories_for_prompt(pool, user_id, influencer_id)
    user_context = (
        "\n".join(f"- {k}: {v}" for k, v in memories.items())
        if memories
        else "No previous context available."
    )

    # Task 2 variety: pass the last 3 proactive messages as "don't repeat these"
    recent = await message_repo.recent_proactive_texts(pool, conversation_id, limit=3)
    if recent:
        recent_block = "\n".join(f'  • "{r}"' for r in recent)
        variety_block = (
            "Previous proactive messages you sent to this user (do NOT repeat "
            "themes, hooks, opening phrases, or topics from these; generate "
            "something distinctly different in approach and content):\n" + recent_block
        )
    else:
        variety_block = ""

    msg_type = random.choice(PROACTIVE_MESSAGE_TYPES)
    archetype = (inf.get("category") or "").lower().strip()
    tone = ARCHETYPE_TONE.get(archetype, "natural and conversational")

    prompt = PROACTIVE_PROMPT.format(
        display_name=inf.get("display_name", "Bot"),
        user_context=user_context,
        message_type=msg_type,
        type_hint=TYPE_HINTS[msg_type],
        tone=tone,
        variety_block=variety_block,
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
        is_proactive=True,
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
