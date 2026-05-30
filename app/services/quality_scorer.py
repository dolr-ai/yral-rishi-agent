"""Phase 7.7 — bot quality scorer.

Nightly background job that scores each AI influencer's recent conversations
using Gemini-as-judge with the same rubric family as the Phase 9 eval.

For each bot:
  1. Pull the last SAMPLE_CONVERSATIONS active conversations
  2. From each, extract up to TURN_PAIRS_PER_CONVERSATION (user, bot) pairs
  3. Judge each pair on three criteria (in_character, response_quality,
     engagement) plus an overall average
  4. Write one bot_quality_scores row per bot per run

Cost budget per night (rough):
  ~50 bots × 20 convs × 3 pairs = 3000 judge calls
  At ~1.5s each with concurrency 5 → ~15 min wall clock
  Gemini Flash judge ≈ free quota

If Gemini is down, the loop logs + retries on the next 24h tick.
"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

# Scoring constants — keep modest so prod cost stays bounded
SAMPLE_CONVERSATIONS = 20
TURN_PAIRS_PER_CONVERSATION = 3
JUDGE_CONCURRENCY = 5  # asyncio.gather chunks

# Background loop schedule
SCORING_INTERVAL_SEC = 24 * 60 * 60
INITIAL_DELAY_SEC = 15 * 60  # 15 min after startup so containers warm up

JUDGE_PROMPT = """Score this AI bot reply on 3 criteria (1-5 each).

Bot personality: {bot_archetype}
USER: {user_message}
BOT: {bot_message}

Criteria:
1. IN_CHARACTER (1-5): does the bot stay true to its archetype? No AI/LLM mentions?
2. RESPONSE_QUALITY (1-5): helpful, well-formed, appropriate length for mobile (1-3 sentences ideal)?
3. ENGAGEMENT (1-5): does the reply invite continuation (a hook, a question, an interesting beat)?

Return ONLY JSON: {{"in_character": N, "response_quality": N, "engagement": N}}"""


async def _judge_pair(
    bot_archetype: str, user_message: str, bot_message: str
) -> dict | None:
    """Run one Gemini-as-judge call. Returns parsed scores or None on failure."""
    from services import ai_client

    prompt = JUDGE_PROMPT.format(
        bot_archetype=bot_archetype or "general",
        user_message=(user_message or "")[:500],
        bot_message=(bot_message or "")[:500],
    )
    try:
        result = await ai_client.generate_response(
            system_instructions=(
                "You are a strict AI response quality judge. Return only "
                "valid JSON. Reserve 5 for genuinely excellent; 3 is acceptable."
            ),
            conversation_history=[],
            user_message=prompt,
            is_nsfw=False,
        )
    except Exception as e:
        logger.debug(f"quality_scorer judge failed: {e}")
        return None
    text = result.content
    start, end = text.find("{"), text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    # Sanity guard — all three keys present and in range
    for k in ("in_character", "response_quality", "engagement"):
        if k not in obj or not isinstance(obj[k], (int, float)):
            return None
        if not (1 <= obj[k] <= 5):
            return None
    return obj


async def _extract_turn_pairs(pool, conversation_id: str, max_pairs: int) -> list[dict]:
    """Pull at most max_pairs adjacent (user, bot-assistant) pairs from
    a conversation, newest first. System messages and is_proactive bot
    messages are skipped — proactive bots don't have a user message to
    pair with and would skew the score."""
    rows = await pool.fetch(
        """
        SELECT role, content, is_proactive, created_at
        FROM messages
        WHERE conversation_id = $1
          AND role IN ('user', 'assistant')
        ORDER BY created_at DESC
        LIMIT 40
        """,
        conversation_id,
    )
    if not rows:
        return []
    # Walk newest → oldest. Bot replies that aren't proactive pair with
    # the immediately-preceding user message.
    rows = [dict(r) for r in rows]
    pairs: list[dict] = []
    for i, m in enumerate(rows):
        if m["role"] != "assistant" or m.get("is_proactive"):
            continue
        # Find the most recent user message strictly before this bot reply
        for j in range(i + 1, len(rows)):
            cand = rows[j]
            if cand["role"] == "user" and cand["content"]:
                pairs.append(
                    {
                        "user_message": cand["content"],
                        "bot_message": m["content"] or "",
                    }
                )
                break
        if len(pairs) >= max_pairs:
            break
    return pairs


async def _sample_bot_conversations(pool, bot_id: str, n: int) -> list[str]:
    """Return up to n conversation IDs with this bot, most recently active first."""
    rows = await pool.fetch(
        """
        SELECT id FROM conversations
        WHERE influencer_id = $1
          AND conversation_type = 'ai_chat'
        ORDER BY updated_at DESC
        LIMIT $2
        """,
        bot_id,
        n,
    )
    return [r["id"] for r in rows]


async def _score_one_bot(pool, bot: dict) -> dict | None:
    """Pull samples, judge them, return aggregated scores for one bot.

    Returns None if there were no scoreable turn pairs (new bot / no traffic).
    """
    bot_id = bot["id"]
    archetype = (bot.get("category") or "").lower().strip() or "general"

    conv_ids = await _sample_bot_conversations(pool, bot_id, SAMPLE_CONVERSATIONS)
    if not conv_ids:
        return None

    # Gather pairs across all sampled conversations
    pair_lists = await asyncio.gather(
        *(
            _extract_turn_pairs(pool, cid, TURN_PAIRS_PER_CONVERSATION)
            for cid in conv_ids
        )
    )
    pairs: list[dict] = []
    for plist in pair_lists:
        pairs.extend(plist)
    if not pairs:
        return None

    # Judge with bounded concurrency
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)

    async def _judge_with_sem(pair):
        async with sem:
            return await _judge_pair(
                archetype, pair["user_message"], pair["bot_message"]
            )

    scores = await asyncio.gather(*(_judge_with_sem(p) for p in pairs))
    scores = [s for s in scores if s]
    if not scores:
        return None

    n = len(scores)

    def _avg(key):
        return sum(s[key] for s in scores) / n

    in_char = _avg("in_character")
    quality = _avg("response_quality")
    engage = _avg("engagement")
    overall = (in_char + quality + engage) / 3.0

    return {
        "bot_id": bot_id,
        "score_overall": round(overall, 3),
        "score_in_character": round(in_char, 3),
        "score_response_quality": round(quality, 3),
        "score_engagement": round(engage, 3),
        "last_n_conversations": len(conv_ids),
        "sample_size": n,
    }


async def score_all_bots_once(pool) -> dict:
    """One full pass. Returns a stats dict (used by the loop + tests)."""
    from repositories import quality_score_repo

    bots = await pool.fetch(
        """
        SELECT id, category FROM ai_influencers
        WHERE is_active = 'active'
        """
    )
    scored = 0
    skipped = 0
    for bot in bots:
        try:
            scored_dict = await _score_one_bot(pool, dict(bot))
        except Exception as e:
            logger.warning(f"quality_scorer: error scoring bot {bot['id']}: {e}")
            skipped += 1
            continue
        if not scored_dict:
            skipped += 1
            continue
        try:
            await quality_score_repo.insert(pool, **scored_dict)
            scored += 1
        except Exception as e:
            logger.warning(f"quality_scorer: insert failed for {bot['id']}: {e}")
            skipped += 1
    return {"scored": scored, "skipped": skipped, "total_bots": len(bots)}


async def scoring_loop():
    """Run score_all_bots_once every 24h after an initial delay."""
    from database import get_pool
    from kill_switch import is_enabled

    await asyncio.sleep(INITIAL_DELAY_SEC)
    while True:
        try:
            # Emergency kill-switch — skip the Gemini-calling scoring
            # pass entirely. The wakeup cadence stays so re-enabling
            # is a single env flip.
            if not is_enabled("quality_scorer"):
                await asyncio.sleep(SCORING_INTERVAL_SEC)
                continue
            pool = await get_pool()
            t0 = asyncio.get_event_loop().time()
            stats = await score_all_bots_once(pool)
            elapsed = asyncio.get_event_loop().time() - t0
            logger.info(
                f"quality_scorer: scored {stats['scored']}/{stats['total_bots']} bots "
                f"(skipped {stats['skipped']}) in {elapsed:.1f}s"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"quality_scorer pass failed (non-fatal): {e}")
        await asyncio.sleep(SCORING_INTERVAL_SEC)
