"""Tiered user memory: extract, store, and retrieve user facts.

Replaces the flat conversation.metadata.memories approach with a dedicated
user_memories table. Memories are per (user, influencer) pair with categories.
Global memories (influencer_id=NULL) apply across all conversations.
"""

import asyncio
import json
import logging

from repositories import memory_repo
from services import embeddings, session_memory
from services.ai_client import _call_gemini
import config

logger = logging.getLogger(__name__)

# Phase 4 polish (memory recitation fix, Task 1): inject at most 3 memories
# per turn so the bot doesn't lead with personal facts. Fetch a bigger buffer
# from semantic_search and filter overused keys (per-conversation history
# tracked in Redis via session_memory) before taking the top 3.
SEMANTIC_TOP_K = 3
SEMANTIC_SEARCH_BUFFER = 10  # over-fetch so the filter has slack

# Phase 4.6: identity facts (name, age, where the user lives, what they do)
# don't change per-influencer — the user IS the same person across every bot
# they chat with. Storing these with influencer_id=NULL means they appear in
# every bot's context, and we only keep one row per (user, key) regardless of
# which bot they were learned from.
GLOBAL_CATEGORIES = {"identity"}

EXTRACTION_PROMPT = """Extract factual information about the user from this conversation exchange.

CATEGORIES (use exactly these):
- identity: name, age, gender, location, occupation, language
- preferences: favorite_food, hobbies, interests, music_taste
- goals: fitness_goal, career_goal, learning_goal
- context: relationship_status, family, pets, living_situation
- emotional: current_mood, stress_level, recent_events

Recent exchange:
User: {user_message}
Assistant: {assistant_response}

Rules:
- Return ONLY a JSON array of objects: [{{"category": "identity", "key": "name", "value": "Rahul"}}]
- Only extract EXPLICIT facts the user stated, not inferences
- Keep values concise (under 50 chars)
- If no new information, return empty array: []"""


async def extract_and_store(
    pool,
    user_id: str,
    influencer_id: str,
    user_message: str,
    assistant_response: str,
    message_id: str | None = None,
    is_nsfw: bool = False,
):
    # Emergency kill-switch (2026-05-30) — this function fires from
    # chat.send_message via asyncio.create_task AFTER the user's reply
    # lands. Skipping is safe: the user never sees the memory call,
    # they just don't get personalization improvement from this turn.
    from kill_switch import is_enabled

    if not is_enabled("memory_extraction"):
        return
    if not config.GEMINI_API_KEY:
        return

    prompt = EXTRACTION_PROMPT.format(
        user_message=user_message,
        assistant_response=assistant_response,
    )

    try:
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        system_instruction = {
            "parts": [{"text": "Return valid JSON only. No markdown, no explanation."}]
        }

        response_text, _ = await _call_gemini(
            contents=contents,
            system_instruction=system_instruction,
            temperature=0.1,
            max_tokens=1024,
        )

        start = response_text.find("[")
        end = response_text.rfind("]") + 1
        if start < 0 or end <= start:
            return

        memories = json.loads(response_text[start:end])
        if not isinstance(memories, list):
            return

        for mem in memories:
            if not isinstance(mem, dict):
                continue
            category = mem.get("category", "").strip()
            key = mem.get("key", "").strip()
            value = mem.get("value", "").strip()
            if not category or not key or not value:
                continue

            # Compute embedding inline (best-effort; failure stores NULL and
            # backfill picks it up next pass). This is on the post-message
            # background task — not the hot path — so latency here doesn't
            # affect user-visible response time.
            emb_text = embeddings.memory_to_embed_text(category, key, value)
            embedding = await embeddings.embed_text(emb_text)

            # Phase 4.6: identity facts cross influencers; everything else
            # stays per-(user, influencer).
            storage_influencer_id = (
                None if category in GLOBAL_CATEGORIES else influencer_id
            )

            await memory_repo.upsert(
                pool,
                user_id=user_id,
                influencer_id=storage_influencer_id,
                category=category,
                key=key,
                value=value,
                source_message_id=message_id,
                embedding=embedding,
            )

    except Exception as e:
        logger.warning(f"Memory extraction failed (non-fatal): {e}")


async def get_memories_for_prompt(
    pool,
    user_id: str,
    influencer_id: str,
    query_embedding: list[float] | None = None,
    conversation_id: str | None = None,
) -> dict:
    """Get memories formatted for the soul file composer.

    Phase 4.5: when query_embedding is provided, semantic search runs across
    ALL the user's memories regardless of which influencer the row came from.
    Distance ranking gatekeeps relevance.

    Phase 4 polish (Task 1): we over-fetch SEMANTIC_SEARCH_BUFFER results,
    drop any keys that have appeared >= MEMORY_REPEAT_LIMIT times in the last
    MEMORY_HISTORY_DEPTH turns (Redis-tracked per conversation), then take the
    top SEMANTIC_TOP_K (=3). The chosen keys get recorded back to Redis
    fire-and-forget so the next turn's filter sees them.

    Without a query (proactive flow, no current message), we fall back to
    per-(user, influencer) + global identity memories — the pre-Phase-4.5
    behavior. Cross-conversation recall + variety filter only kick in when
    there's a query to anchor relevance against.
    """
    if not query_embedding:
        memories = await memory_repo.get_all_for_user(pool, user_id, influencer_id)
        return {f"{m['category']}_{m['key']}": m["value"] for m in memories}

    raw = await memory_repo.semantic_search(
        pool, user_id, query_embedding, top_k=SEMANTIC_SEARCH_BUFFER
    )

    overused: set[str] = set()
    if conversation_id:
        overused = await session_memory.recently_overused_keys(user_id, conversation_id)

    result: dict[str, str] = {}
    for mem in raw:
        key = f"{mem['category']}_{mem['key']}"
        if key in overused:
            continue
        result[key] = mem["value"]
        if len(result) >= SEMANTIC_TOP_K:
            break

    if conversation_id and result:
        asyncio.create_task(
            session_memory.record_memory_keys_used(
                user_id, conversation_id, list(result.keys())
            )
        )

    return result
