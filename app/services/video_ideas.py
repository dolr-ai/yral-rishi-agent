"""Phase 22.3 — nightly video-idea generator.

Mirror of `services/quality_scorer.py` shape. For each active bot
(influencer with ≥1 message in the last 7 days), pull the latest ~30
messages + the bot's archetype + system_instructions, ask
internal_vllm to emit 5 short {hook, idea_text} pairs as JSON, write
one batch row per idea via `video_idea_repo.insert_batch`.

Hot path is the creator's GET /api/v1/influencers/{id}/video-ideas —
which reads the latest batch in a single index-backed query and never
calls the LLM. Cold-start (no batch yet) calls `generate_for_one_bot`
on-demand once, then returns. The nightly cron keeps the batch fresh.

Cost budget — rough estimate:
  ~50 active bots × 1 LLM call (≤2k input + ~400 output tokens each)
  At ~3-5s per internal_vllm response → <5 min wall-clock for the pass
  $0 marginal cost (internal_vllm compute share)
"""

import asyncio
import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)


# Loop schedule (mirrors quality_scorer cadence).
VIDEO_IDEAS_INTERVAL_SEC = 24 * 60 * 60
INITIAL_DELAY_SEC = 20 * 60  # 20 min after startup so the cluster warms up

# Per-bot generation knobs.
RECENT_MESSAGES_FOR_CONTEXT = 30
IDEAS_PER_BATCH = 5
ACTIVE_BOT_WINDOW_DAYS = 7  # only generate for bots with traffic this recent

# Single per-batch concurrency cap. The provider-level semaphore in
# llm_registry handles the actual fan-out; this just paces the calls we
# emit per pass.
BOT_CONCURRENCY = 3


GENERATION_PROMPT = """You are generating short-form video ideas for an AI influencer to post.

INFLUENCER PROFILE:
- Display name: {bot_name}
- Archetype: {bot_archetype}
- Soul File (system_instructions):
\"\"\"
{system_instructions}
\"\"\"

RECENT CONVERSATIONS the bot had with users (anonymized — newest at the bottom):
{recent_convs}

Your job: produce {n} short-form video ideas this influencer could film and post. Each idea must feel native to the influencer's voice + recent conversations — NOT generic content. Hook lines should be punchy + under 8 words.

Output ONLY a JSON array (no markdown fences, no preamble) of exactly {n} objects, each with this shape:

  [
    {{"hook": "...", "idea_text": "..."}},
    {{"hook": "...", "idea_text": "..."}},
    ...
  ]

- hook: <= 8 words, the opening line a viewer sees on the chip
- idea_text: 1-2 sentences (~20-40 words) describing what the video covers

Reply now."""


def _format_recent_messages(rows: list[dict]) -> str:
    """Render recent messages as ROLE: text lines, oldest first so the
    LLM sees temporal flow. Cap each line so total tokens stay bounded."""
    if not rows:
        return "(no recent messages — the bot is new or quiet)"
    # rows come in newest-first from the SELECT; reverse for chronological.
    lines = []
    for m in reversed(rows):
        role = m.get("role") or "?"
        text = (m.get("content") or "").strip()
        if not text:
            continue
        lines.append(f"  {role}: {text[:180]}")
    return "\n".join(lines) if lines else "(no readable content)"


def _extract_idea_array(text: str) -> list[dict] | None:
    """Tolerant JSON-array extractor.

    Three strategies, tried in order:
      1. Strict: find [..] and json.loads it. Same pattern as
         services/wizard.py:_extract_json. Works when the LLM returns
         a clean array, no wrapping prose.
      2. Truncation-tolerant: if strict parse fails, try chopping off
         everything after the LAST complete `}` and re-closing the
         array. Recovers usable ideas from a max_tokens-truncated
         response (the 2026-06-04 cold-start bug — `max_tokens=1024`
         truncated mid-string on Devanagari output, costing a usable
         batch). Belt-and-suspenders even after the cap bump.
      3. Fail: return None so the caller logs + falls back.
    """
    import json

    start, end = text.find("["), text.rfind("]") + 1

    def _validate(parsed) -> list[dict] | None:
        if not isinstance(parsed, list):
            return None
        cleaned: list[dict] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            hook = (item.get("hook") or "").strip()
            idea = (item.get("idea_text") or "").strip()
            if hook and idea:
                cleaned.append({"hook": hook, "idea_text": idea})
        return cleaned if cleaned else None

    # Strategy 1: strict
    if start >= 0 and end > start:
        try:
            return _validate(json.loads(text[start:end]))
        except json.JSONDecodeError:
            pass

    # Strategy 2: truncation-tolerant. Find the last complete `}`
    # AFTER the array opening `[`, then synthesize the array close.
    # Handles "...idea N-1}, {idea N partial>>truncated" by dropping
    # the partial and keeping ideas 1..N-1.
    if start < 0:
        return None
    last_close = text.rfind("}")
    if last_close <= start:
        return None
    repaired = text[start : last_close + 1] + "]"
    try:
        return _validate(json.loads(repaired))
    except json.JSONDecodeError:
        return None


async def generate_for_one_bot(pool, bot: dict) -> list[dict]:
    """Run the per-bot LLM call + write the batch. Returns the inserted
    rows (may be empty on LLM failure or parse failure)."""
    from repositories import video_idea_repo
    from services import llm_registry

    bot_id = bot["id"]
    today = date.today()

    # Recent message context — last ~30 msgs across all this bot's
    # conversations. NOT filtered by role; the model uses both sides.
    rows = await pool.fetch(
        """
        SELECT m.role, m.content, m.created_at
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.id
        WHERE c.influencer_id = $1
        ORDER BY m.created_at DESC
        LIMIT $2
        """,
        bot_id,
        RECENT_MESSAGES_FOR_CONTEXT,
    )

    prompt = GENERATION_PROMPT.format(
        bot_name=bot.get("display_name") or bot.get("name") or "this bot",
        bot_archetype=(bot.get("category") or "general"),
        system_instructions=(bot.get("system_instructions") or "(empty)")[:2000],
        recent_convs=_format_recent_messages([dict(r) for r in rows]),
        n=IDEAS_PER_BATCH,
    )

    try:
        response = await llm_registry.call(
            process="video_idea_generation",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Return only a valid JSON array of objects. "
                        "No markdown fences. No prose outside the array."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
            # Devanagari (and other multi-byte scripts) consume ~3x tokens
            # vs Latin per visible character. The first cold-start in prod
            # (2026-06-04, Rishi's principal) truncated mid-JSON-string at
            # 1024 tokens because the 5-idea response was Hindi text. 4096
            # gives ample headroom for any reasonable batch + script combo.
            max_tokens=4096,
        )
    except Exception as e:
        logger.warning("video_ideas: LLM call failed for bot %s: %s", bot_id, e)
        return []

    ideas = _extract_idea_array(response.content or "")
    if not ideas:
        logger.warning(
            "video_ideas: parse failed for bot %s (first 200 chars: %r)",
            bot_id,
            (response.content or "")[:200],
        )
        return []

    # Trim/pad to IDEAS_PER_BATCH so rank assignment is predictable.
    ideas = ideas[:IDEAS_PER_BATCH]
    try:
        inserted = await video_idea_repo.insert_batch(
            pool,
            influencer_id=bot_id,
            batch_date=today,
            ideas=ideas,
        )
    except Exception:
        logger.exception("video_ideas: insert_batch failed for bot %s", bot_id)
        return []

    return inserted


async def _list_active_bots(pool) -> list[dict]:
    """Influencers with ≥1 message in the last ACTIVE_BOT_WINDOW_DAYS
    days. Skips discontinued bots. Returns the minimal columns the
    prompt needs.

    2026-06-26: split the original DISTINCT-ON-after-3-way-JOIN into
    two index-friendly probes. The old query materialized
    `ai_influencers ⋈ conversations ⋈ messages` then sorted it for the
    DISTINCT ON — the sort exceeded work_mem and spilled, hitting the
    Patroni container's 64 MiB /dev/shm cap (Sentry #144 DiskFullError).

    Step 1 fetches distinct active influencer_ids from the recent
    messages window — pure index traversal, no DISTINCT sort over the
    full join. Step 2 hydrates the bot fields for that small id list."""
    id_rows = await pool.fetch(
        """
        SELECT DISTINCT c.influencer_id AS id
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.created_at > NOW() - INTERVAL '%d days'
          AND c.influencer_id IS NOT NULL
        """
        % ACTIVE_BOT_WINDOW_DAYS,
    )
    if not id_rows:
        return []
    ids = [r["id"] for r in id_rows]
    rows = await pool.fetch(
        """
        SELECT i.id, i.name, i.display_name, i.category,
               i.system_instructions
        FROM ai_influencers i
        WHERE i.is_active = 'active'
          AND i.id = ANY($1::varchar[])
        ORDER BY i.id
        """,
        ids,
    )
    return [dict(r) for r in rows]


async def generate_all_once(pool) -> dict:
    """One full pass. Skips bots that already have today's batch_date
    so the loop is safe to re-run after a partial run."""
    from repositories import video_idea_repo

    bots = await _list_active_bots(pool)
    today = date.today()

    # Filter out bots that already have today's batch.
    to_generate: list[dict] = []
    for bot in bots:
        if await video_idea_repo.bot_has_batch_for_date(pool, bot["id"], today):
            continue
        to_generate.append(bot)

    sem = asyncio.Semaphore(BOT_CONCURRENCY)
    generated = 0
    skipped = 0

    async def _one(bot: dict):
        nonlocal generated, skipped
        async with sem:
            try:
                inserted = await generate_for_one_bot(pool, bot)
                if inserted:
                    generated += 1
                else:
                    skipped += 1
            except Exception:
                logger.exception("video_ideas: per-bot exception bot=%s", bot.get("id"))
                skipped += 1

    await asyncio.gather(*(_one(b) for b in to_generate))

    return {
        "generated": generated,
        "skipped": skipped,
        "total_active_bots": len(bots),
        "had_existing_batch": len(bots) - len(to_generate),
    }


async def video_ideas_loop():
    """Run generate_all_once every 24h after an initial delay."""
    from database import get_pool
    from kill_switch import is_enabled

    await asyncio.sleep(INITIAL_DELAY_SEC)
    while True:
        try:
            if not is_enabled("video_ideas"):
                await asyncio.sleep(VIDEO_IDEAS_INTERVAL_SEC)
                continue
            pool = await get_pool()
            t0 = datetime.now(timezone.utc)
            stats = await generate_all_once(pool)
            elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
            logger.info(
                "video_ideas: generated %d/%d active bots "
                "(skipped %d, existing %d) in %.1fs",
                stats["generated"],
                stats["total_active_bots"],
                stats["skipped"],
                stats["had_existing_batch"],
                elapsed,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Use logger.exception so the traceback lands in logs (per
            # streak_tracker logging hygiene fix 2026-06-04).
            logger.exception("video_ideas pass failed (non-fatal) — retrying next tick")
        await asyncio.sleep(VIDEO_IDEAS_INTERVAL_SEC)
