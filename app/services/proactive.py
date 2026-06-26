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

from repositories import (
    conversation_repo,
    influencer_repo,
    message_repo,
    skill_state_repo,
)
from services import (
    ai_client,
    memory,
    push_notifications,
    skills as skills_catalog,
    soul_file,
    websocket_manager,
)

logger = logging.getLogger(__name__)


# Per Task 2 spec — rotate the kind of opener per attempt so the bot doesn't
# always do "hey what's up." Random rather than round-robin: round-robin would
# need persistent state per conversation, and the user-visible effect is the
# same after a few rotations.
PROACTIVE_MESSAGE_TYPES = ("question", "observation", "story", "light_topic")


# 2026-06-26 — skill check-in cadence backoff. Locked decision from the
# chat-quality brief: when a user isn't responding to skill check-ins,
# slow down — never hard-stop. Each consecutive unanswered check-in
# doubles the wait until SKILL_CHECKIN_BACKOFF_CAP_HOURS (~weekly); a
# user reply resets the count automatically (the unanswered count is
# "since the last user reply", so the next reply zeroes it). Cap is a
# ceiling on the *cadence*, not a stop — check-ins continue at the cap
# until either the user replies or the influencer is discontinued.
SKILL_CHECKIN_BACKOFF_CAP_HOURS = 24 * 7  # ~weekly


def _backoff_cadence(base_hours: int, unanswered_count: int) -> int:
    """Double the wait for each consecutive unanswered skill check-in,
    capped at SKILL_CHECKIN_BACKOFF_CAP_HOURS. With base_hours=6 this is
    6 → 12 → 24 → 48 → 96 → 168 (capped). unanswered_count<=1 means the
    user replied recently (or this is the first send) — use the base."""
    if unanswered_count <= 1:
        return base_hours
    # 2^(n-1): n=2 doubles, n=3 quadruples, …
    multiplier = 1 << (unanswered_count - 1)
    return min(base_hours * multiplier, SKILL_CHECKIN_BACKOFF_CAP_HOURS)


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

{streak_block}
{variety_block}

Rules:
- Stay in character based on your personality
- Be warm and casual, not needy or desperate
- Keep it under 2 sentences
- End with a hook that invites a reply (question for "question" type; for others, an evocative beat)
- If you have no context about the user, send something general but specific to the message type

Generate ONLY the message text, nothing else."""


def _streak_block(streak_days: int) -> str:
    """Phase 5.6: a subtle nod at 3+ day streaks. Below 3 the streak isn't
    interesting enough to mention; above 7 we lean into it more warmly. The
    block becomes a guidance line for Gemini, not a hardcoded reference —
    the model decides whether to mention the streak based on context."""
    if streak_days >= 7:
        return (
            f"The user has chatted with you {streak_days} days in a row — "
            "this is a solid streak worth acknowledging warmly if it fits "
            "naturally. Don't be cheesy; one short callout, then move on."
        )
    if streak_days >= 3:
        return (
            f"The user has chatted with you {streak_days} days in a row. "
            "Optional small nod, only if it fits the conversation type."
        )
    return ""


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

    # Phase 5.6: subtle streak nod
    streak_row = await pool.fetchrow(
        "SELECT current_streak_days FROM conversations WHERE id = $1",
        conversation_id,
    )
    streak_block = _streak_block(
        int(streak_row["current_streak_days"] or 0) if streak_row else 0
    )

    prompt = PROACTIVE_PROMPT.format(
        display_name=inf.get("display_name", "Bot"),
        user_context=user_context,
        message_type=msg_type,
        type_hint=TYPE_HINTS[msg_type],
        tone=tone,
        streak_block=streak_block,
        variety_block=variety_block,
    )

    llm_result = await ai_client.generate_response(
        system_instructions=inf.get("system_instructions", ""),
        conversation_history=[],
        user_message=prompt,
        is_nsfw=inf.get("is_nsfw", False),
        user_id=user_id,
        conversation_id=conversation_id,
        archetype=archetype,
        # Proactive nudges are async background generation, NOT real-user
        # chat. Tagging the process correctly lands the cost row under
        # `proactive_generation` (not `user_chat_main`) AND honors the
        # LLM_DEFAULTS routing (runpod_vllm primary → internal_vllm
        # fallback) so background traffic stops hitting Gemini.
        process_override="proactive_generation",
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

    unread_count = await message_repo.count_unread(pool, conversation_id, user_id)
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
    """Find conversations where the user hasn't sent a message in N hours.

    Task D (Phase 5.4): per-conversation `proactive_frequency` overrides the
    default threshold:
      'default' / 'daily' → 24h (the legacy behavior)
      'weekly'            → 168h
      'off'               → never returned
    The query computes the effective threshold inline so we keep one scan.
    """
    rows = await pool.fetch(
        """
        SELECT c.id, c.user_id, c.influencer_id
        FROM conversations c
        WHERE c.conversation_type = 'ai_chat'
          AND c.influencer_id IS NOT NULL
          AND c.proactive_frequency != 'off'
          AND c.updated_at < NOW() - INTERVAL '1 hour' * (
              CASE c.proactive_frequency
                  WHEN 'weekly' THEN 168
                  ELSE $1
              END
          )
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


# ----- Phase 23.6 — skill-driven scheduled check-ins ---------------------
# Symmetric with the legacy (conversation_idle) check-in path above:
#   find_due_skill_events  → mirror of find_inactive_conversations
#   generate_skill_checkin → mirror of generate_proactive_message
#   send_skill_checkin     → mirror of send_proactive_message
# The big differences:
#   - Trigger source: user_skill_state.next_event_at (a clock), not the
#     conversation table's updated_at age.
#   - Prompt assembly uses the Soul File composer + skill_state's setup
#     so the bot references primary_goal + preferred_times naturally.
#   - We update next_event_at after delivery so the loop self-schedules
#     the next fire.


async def find_due_skill_events(pool, limit: int = 50) -> list[dict]:
    """Return user_skill_state rows whose next_event_at has passed.
    Thin pass-through to skill_state_repo so the engagement loop talks
    to a single 'find_due_*' surface."""
    return await skill_state_repo.list_due(pool, limit=limit)


async def generate_skill_checkin(
    pool,
    *,
    user_id: str,
    influencer_id: str,
    skill_def: dict,
    state_row: dict,
) -> str | None:
    """Generate the check-in text for one due skill event.

    Uses the same compose() + ai_client.generate_response surface as
    chat.py so the persona, archetype, skill block, and user_skill_state
    plan layer all show up in the system prompt — the bot speaks AS the
    influencer, not as a generic check-in template. The user_message
    is the skill's checkin_prompt (terse, one-line instruction)."""
    inf = await influencer_repo.get_by_id(pool, influencer_id)
    if not inf or inf.get("is_active") == "discontinued":
        return None

    memories = await memory.get_memories_for_prompt(pool, user_id, influencer_id)

    system_instructions = soul_file.compose(
        system_instructions=inf.get("system_instructions", "") or "",
        category=inf.get("category"),
        archetype=inf.get("archetype"),
        memories=memories,
        skill_slug=inf.get("skill_slug"),
        user_skill_state=state_row.get("state") or {},
        global_rule_overrides=inf.get("global_rule_overrides"),
        sections=inf.get("system_instructions_sections"),
    )

    checkin_prompt = skill_def.get("checkin_prompt") or (
        "Send a short, time-appropriate check-in. Keep it under 25 words. "
        "End with one question."
    )

    # Phase 23.6 — skill check-ins are async background generation, same
    # category as the legacy proactive loop. Route via the
    # `proactive_generation` process so:
    #   1. The cost lands in the right bucket (was inflating
    #      `user_chat_main` per the 2026-06-11 Rishi audit).
    #   2. The LLM_DEFAULTS routing (runpod_vllm → internal_vllm fallback,
    #      NEVER gemini) is actually honored. Pre-2026-06-11 this fell
    #      through to `user_chat_main` → gemini and burned premium $.
    # If per-skill cost tracking is ever needed, the right move is a
    # `skill_slug` tag column on `llm_costs`, not a separate process per
    # skill — that would multiply the routing surface for every vertical.
    llm_result = await ai_client.generate_response(
        system_instructions=system_instructions,
        conversation_history=[],
        user_message=checkin_prompt,
        is_nsfw=inf.get("is_nsfw", False),
        user_id=user_id,
        conversation_id=None,
        # Phase 21γ.P34.M1 — new archetype column wins; category fallback
        # for pre-classify rows.
        archetype=inf.get("archetype") or inf.get("category"),
        process_override="proactive_generation",
    )

    if llm_result.is_fallback or llm_result.error_code:
        logger.info(
            "skill checkin: LLM fallback/error (user=%s influencer=%s code=%s)",
            user_id,
            influencer_id,
            llm_result.error_code,
        )
        return None

    return llm_result.content


async def send_skill_checkin(pool, *, state_row: dict) -> dict | None:
    """End-to-end delivery for one due skill_state row.

    1. Resolve skill_def + (re)open the (user, influencer) conversation.
    2. Generate the check-in via the composer.
    3. Persist as an assistant message + WS broadcast + push.
    4. Advance next_event_at so the loop self-schedules.

    Returns the persisted message dict on success, None when the row
    was skipped (skill missing, conversation create failed, LLM
    fallback, etc.). All failures are swallowed to logging — the loop
    moves on to the next due row."""
    user_id = state_row["user_id"]
    influencer_id = state_row["influencer_id"]
    skill_slug = state_row["skill_slug"]

    skill_def = skills_catalog.get(skill_slug)
    if not skill_def:
        logger.warning(
            "skill checkin: unknown skill_slug=%s on state row (user=%s influencer=%s) — skipping",
            skill_slug,
            user_id,
            influencer_id,
        )
        return None

    # The user_skill_state row is per-(user, influencer), not per-
    # conversation. Pick up the existing AI-chat conversation if any
    # — we never create one here so we don't surprise the user with
    # a new thread they didn't open.
    conv = await conversation_repo.get_existing(pool, user_id, influencer_id)
    if not conv:
        logger.info(
            "skill checkin: no conversation yet for user=%s influencer=%s — skipping",
            user_id,
            influencer_id,
        )
        # Still advance the schedule so we don't hot-loop on this row.
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        cadence = int(skill_def.get("default_cadence_hours") or 6)
        await skill_state_repo.mark_event_fired(
            pool,
            user_id=user_id,
            influencer_id=influencer_id,
            next_event_at=_dt.now(_tz.utc) + _td(hours=cadence),
        )
        return None

    content = await generate_skill_checkin(
        pool,
        user_id=user_id,
        influencer_id=influencer_id,
        skill_def=skill_def,
        state_row=state_row,
    )
    if not content:
        return None

    msg = await message_repo.create(
        pool,
        conversation_id=conv["id"],
        role="assistant",
        content=content,
        message_type="text",
        sender_id=influencer_id,
        is_proactive=True,
    )

    inf = await influencer_repo.get_by_id(pool, influencer_id)
    display_name = inf.get("display_name", "AI") if inf else "AI"
    unread_count = await message_repo.count_unread(pool, conv["id"], user_id)

    asyncio.create_task(
        websocket_manager.broadcast_new_message(
            user_id=user_id,
            conversation_id=conv["id"],
            message={
                "id": msg["id"],
                "conversation_id": conv["id"],
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

    # Push pipeline currently routes by message type only (chat_message);
    # adding a skill-specific trigger_type is a follow-up once mobile
    # has the bucket UI to differentiate.
    asyncio.create_task(
        push_notifications.send_new_message_notification(
            user_id=user_id,
            influencer_name=display_name,
            message_content=content,
            conversation_id=conv["id"],
            influencer_id=influencer_id,
        )
    )

    # Advance the schedule with backoff: each consecutive unanswered
    # check-in doubles the wait until SKILL_CHECKIN_BACKOFF_CAP_HOURS.
    # The unanswered count is "proactive messages since the last user
    # reply" — a user reply zeroes it, automatically resetting cadence
    # to base. NEVER hard-stops (locked brief decision 2026-06-26).
    # The message we just inserted IS included in the count, so the
    # first consecutive unanswered round uses base, the second doubles,
    # etc. — matching the brief's 6h → 12h → 24h → 48h ladder for the
    # default cadence.
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from datetime import timezone as _tz

    base_cadence = int(skill_def.get("default_cadence_hours") or 6)
    unanswered = await message_repo.count_unanswered_proactive(pool, conv["id"])
    cadence = _backoff_cadence(base_cadence, unanswered)
    await skill_state_repo.mark_event_fired(
        pool,
        user_id=user_id,
        influencer_id=influencer_id,
        next_event_at=_dt.now(_tz.utc) + _td(hours=cadence),
    )
    return msg
