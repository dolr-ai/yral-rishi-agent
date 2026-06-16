"""Phase 21γ.P34.M1 — Discovery Feed bot classification.

One multimodal LLM call per AI influencer tags `gender` + `archetype`
from the bot's {avatar image, display name, system instructions,
description}. Updates `ai_influencers.gender` + `ai_influencers.archetype`
in place. ONLY LLM call in the entire Discovery Feed pipeline.

## Two surfaces (deliberate split)

1. **Sample classifier** — `classify_sample()` runs N bots WITHOUT
   writing to the DB. Used by `POST /admin/discovery/classify-sample`
   so Rishi can review 5 sample labels before the full backfill sweep.
2. **Backfill loop** — `classification_loop()` runs the full sweep at
   10/min throttle, writes labels to the DB. Default OFF
   (kill_switch `_DEFAULT_OFF_LOOPS`) until Rishi reviews the samples
   + flips `ENABLE_INFLUENCER_CLASSIFICATION_LOOP=true`.

## Why archetype (5 values) instead of bot_type (8 values)

Decision locked 2026-06-16 PM by Rishi. The rev-7 design's 8-value
`bot_type` conflated WHAT a bot covers (food/travel/weather/anime/…)
with HOW it talks (companion/advisor/entertainer/educator/creator).
They're orthogonal axes — same topic × different persona = different
bots — so 8 buckets lost ranking signal AND couldn't grow.

The two-column orthogonal model:
  - `category`  (UNCHANGED) — free-form, mobile-visible, grows freely.
  - `archetype` (NEW)       — locked 5-value enum mapped 1:1 to
                              ARCHETYPE_PROMPTS in soul_file.py.

Side effect: fixes the 93%-of-bots silent-archetype-skip bug at
soul_file.py:274 (case-insensitive string match against 5 magic
strings missed 3427/3684 active rows). A real column closes the gap.

## Safety properties

- **Vision-required** — provider must have `supports_vision=True`. We
  flipped `runpod_vllm.supports_vision: True` in this same PR after
  Session 6's 2026-06-16 empirical verification. A future provider
  flip via the admin dashboard would be caught by the H12 capability
  guard in `llm_routing_admin`.
- **`chat_template_kwargs={enable_thinking: False}`** — inherited from
  `runpod_vllm.default_extra_body` (see `llm_registry.PROVIDERS`).
  10× latency win: 3.4 s/bot vs ~34 s with reasoning mode.
- **Default OFF** — loop ships dormant. Rishi opts in after sample review.
- **Idempotent on the labels** — re-classifying a bot is fine; the
  UPDATE writes the same value unless the prompt drifts.
- **Admin override wins** — the loop only touches bots where BOTH
  `gender='unknown'` AND `archetype='unknown'`, so a manual override
  (direct SQL or `POST /admin/discovery/classify-override`) is preserved.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─── taxonomy (locked per Rishi 2026-06-16 PM) ──────────────────────────


VALID_GENDERS: frozenset[str] = frozenset({"male", "female", "neutral", "unknown"})

# 5 archetypes — must match `ARCHETYPE_PROMPTS` keys in
# `app/services/soul_file.py` EXACTLY. Do NOT expand to 8; the
# orthogonal model puts topic-style growth in the free-form `category`
# column, not here.
VALID_ARCHETYPES: frozenset[str] = frozenset(
    {
        "companion",
        "advisor",
        "entertainer",
        "educator",
        "creator",
        # "unknown" sentinel for pre-classify rows. Excluded from the
        # classifier output (the parser collapses unknown → None to
        # avoid overwriting a future better label) but valid as a
        # column value.
        "unknown",
    }
)

VALID_CONFIDENCES: frozenset[str] = frozenset({"high", "medium", "low"})


# ─── loop schedule ──────────────────────────────────────────────────────


# 10 bots per minute = 6s/bot pacing on top of the per-call LLM latency.
# Saikat's pod measured 3.4s/bot, so the throttle is the binding factor
# (a 100-bot backfill takes ~10 min wall-clock).
CLASSIFICATION_PER_MINUTE = 10
SECONDS_BETWEEN_CALLS = 60.0 / CLASSIFICATION_PER_MINUTE  # 6.0s

# Loop cadence + initial-startup delay. Once the full catalog is
# classified, subsequent passes only touch newly-created bots, so the
# 1-hour cadence keeps it cheap without a separate on-create trigger.
LOOP_INTERVAL_SEC = 60 * 60
INITIAL_DELAY_SEC = 5 * 60


# ─── prompt ─────────────────────────────────────────────────────────────


_PROMPT_INSTRUCTION = """You are classifying an AI influencer for a discovery feed.

Look at the avatar image AND read the text below. Output ONE valid JSON object with exactly these three keys + nothing else:

{
  "gender": "<one of: male | female | neutral | unknown>",
  "archetype": "<one of: companion | advisor | entertainer | educator | creator>",
  "confidence": "<one of: high | medium | low>"
}

Rules:
- "gender" describes the bot's persona/identity as presented, NOT a user demographic. Use "neutral" for cartoons / abstract / non-human characters, "unknown" only when the image AND text give no signal.
- "archetype" is the persona STYLE (how the bot talks), independent of the topic it covers. Pick ONE:
    * companion   = warm, emotionally supportive, listening + validating
    * advisor     = practical, direct, actionable guidance + reasoning
    * entertainer = witty, playful, energetic, humor-driven
    * educator    = patient, clear explanations, breaks complex into simple
    * creator     = creative collaborator, brainstorming, inspiring
  If two archetypes fit, pick the one the system prompt + initial greeting leans on most heavily. The same topic can be ANY archetype — a fitness bot can be advisor OR companion OR entertainer.
- "confidence" — how confident are you in BOTH labels combined: "high" = strong, "medium" = reasonable, "low" = weak signal.
- Respond with the JSON object ONLY. No markdown fences. No prose."""


def _build_classification_messages(bot: dict) -> list[dict]:
    """Build the OpenAI-style messages payload with avatar image_url +
    text context. Mirrors `_build_user_content` shape from ai_client.py
    so the existing runpod_vllm path picks it up unchanged."""
    name = (bot.get("display_name") or bot.get("name") or "(unnamed)").strip()
    description = (bot.get("description") or "").strip()[:600]
    system_instructions = (bot.get("system_instructions") or "").strip()[:1500]

    text_block = (
        f"{_PROMPT_INSTRUCTION}\n\n"
        f"BOT NAME: {name}\n\n"
        f"BOT DESCRIPTION: {description or '(empty)'}\n\n"
        f"BOT SYSTEM PROMPT (truncated):\n{system_instructions or '(empty)'}"
    )

    user_content: list[dict] = [{"type": "text", "text": text_block}]

    # Avatar — optional but heavily preferred. Skipping the image when
    # the bot has none is fine; gender for those rows usually lands at
    # "unknown" anyway.
    avatar = (bot.get("avatar_url") or "").strip()
    if avatar:
        user_content.insert(0, {"type": "image_url", "image_url": {"url": avatar}})

    return [
        {
            "role": "system",
            "content": (
                "Respond with ONLY a JSON object matching the requested "
                "shape. No prose, no fences."
            ),
        },
        {"role": "user", "content": user_content},
    ]


# ─── parse + sanitize the LLM output ────────────────────────────────────


def _parse_classification(text: str) -> dict | None:
    """Tolerant JSON parse. Strategies:
      1. Fenced code-block strip — handles ```json ... ``` wrappers.
      2. Strict: first `{` through last `}`.
    Returns None on any structural failure so the caller can log + skip."""
    if not text:
        return None

    # Strip code fences if the model added them despite the instruction.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            obj = json.loads(fenced.group(1))
            return _validate_classification(obj)
        except json.JSONDecodeError:
            pass

    # Strict: first opening brace through last closing brace.
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end])
            return _validate_classification(obj)
        except json.JSONDecodeError:
            return None
    return None


def _validate_classification(obj: object) -> dict | None:
    """Coerce parsed JSON to the locked taxonomy. Out-of-set values
    collapse to 'unknown' rather than failing — better to mark uncertain
    than throw away a partial label."""
    if not isinstance(obj, dict):
        return None
    gender = str(obj.get("gender", "")).strip().lower()
    archetype = str(obj.get("archetype", "")).strip().lower()
    confidence = str(obj.get("confidence", "")).strip().lower()
    if gender not in VALID_GENDERS:
        gender = "unknown"
    if archetype not in VALID_ARCHETYPES:
        archetype = "unknown"
    if confidence not in VALID_CONFIDENCES:
        # Confidence absent or garbage — default 'low' so we capture
        # the row's existence but the downstream review can spot it.
        confidence = "low"
    # If BOTH labels ended up unknown, count it as a failed
    # classification. Saves writing a no-op UPDATE that overwrites a
    # possible future better label with 'unknown'.
    if gender == "unknown" and archetype == "unknown":
        return None
    return {"gender": gender, "archetype": archetype, "confidence": confidence}


# ─── per-bot classification ─────────────────────────────────────────────


async def classify_one(bot: dict) -> dict | None:
    """Run the LLM + parse. Returns the classification dict or None on
    any failure (LLM error, parse error, both-unknown)."""
    from services import llm_registry

    messages = _build_classification_messages(bot)
    try:
        response = await llm_registry.call(
            process="influencer_classification",
            messages=messages,
            temperature=0.0,  # deterministic classification
            max_tokens=128,  # JSON object is ~50 tokens; pad for safety
        )
    except Exception as e:
        logger.warning(
            "influencer_classification: LLM call failed for bot %s: %s",
            bot.get("id"),
            e,
        )
        return None

    parsed = _parse_classification(response.content or "")
    if parsed is None:
        logger.warning(
            "influencer_classification: parse failed bot=%s (first 200: %r)",
            bot.get("id"),
            (response.content or "")[:200],
        )
        return None
    return parsed


# ─── sample (no-DB-write) — feeds the admin endpoint ────────────────────


async def classify_sample(pool, limit: int = 5) -> list[dict]:
    """Run the classifier on `limit` bots without persisting. Returns
    a list of `{id, display_name, avatar_url, current_*, proposed_*}`
    rows Rishi can review before flipping the loop on.

    Picks unclassified bots first (so the sample reflects what the
    backfill will see); falls back to any active bot if all are
    already classified."""
    rows = await pool.fetch(
        """
        SELECT id, name, display_name, description, system_instructions,
               avatar_url, category, gender, archetype
        FROM ai_influencers
        WHERE is_active = 'active'
          AND (gender = 'unknown' OR archetype = 'unknown')
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )
    # Fallback if everyone is already classified
    if not rows:
        rows = await pool.fetch(
            """
            SELECT id, name, display_name, description, system_instructions,
                   avatar_url, category, gender, archetype
            FROM ai_influencers
            WHERE is_active = 'active'
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )

    results: list[dict] = []
    for r in rows:
        bot = dict(r)
        labels = await classify_one(bot)
        results.append(
            {
                "id": bot["id"],
                "display_name": bot.get("display_name"),
                "avatar_url": bot.get("avatar_url"),
                "category": bot.get("category"),
                "current_gender": bot.get("gender"),
                "current_archetype": bot.get("archetype"),
                "proposed_classification": labels,
            }
        )
        # Throttle even sample calls so the operator doesn't accidentally
        # spike the runpod pod by hammering /classify-sample.
        await asyncio.sleep(SECONDS_BETWEEN_CALLS)
    return results


# ─── backfill loop (writes to DB) ───────────────────────────────────────


async def _list_unclassified_bots(pool) -> list[dict]:
    """Active bots where BOTH labels are still the default 'unknown'.
    An operator override of either column (manual SQL UPDATE or the
    admin override endpoint) takes that row out of the loop's scope —
    desired property."""
    rows = await pool.fetch(
        """
        SELECT id, name, display_name, description, system_instructions,
               avatar_url
        FROM ai_influencers
        WHERE is_active = 'active'
          AND gender = 'unknown'
          AND archetype = 'unknown'
        ORDER BY created_at DESC
        """
    )
    return [dict(r) for r in rows]


async def _apply_classification(pool, bot_id: str, labels: dict) -> None:
    """UPDATE in place. Idempotent — re-running classification on a
    bot with the same labels is a no-op write. `confidence` from the
    classifier is NOT persisted today (it's informational for the
    review; if we ever want low-confidence rows flagged in the DB we
    add a column then)."""
    await pool.execute(
        """
        UPDATE ai_influencers
        SET gender = $2, archetype = $3
        WHERE id = $1
        """,
        bot_id,
        labels["gender"],
        labels["archetype"],
    )


async def classify_all_once(pool) -> dict:
    """One throttled sweep over unclassified bots. Returns {classified,
    failed, total} for the loop log."""
    bots = await _list_unclassified_bots(pool)
    classified = 0
    failed = 0
    for bot in bots:
        try:
            labels = await classify_one(bot)
        except Exception:
            logger.exception(
                "influencer_classification: per-bot exception bot=%s",
                bot.get("id"),
            )
            failed += 1
            await asyncio.sleep(SECONDS_BETWEEN_CALLS)
            continue
        if labels is None:
            failed += 1
        else:
            try:
                await _apply_classification(pool, bot["id"], labels)
                classified += 1
            except Exception:
                logger.exception(
                    "influencer_classification: UPDATE failed bot=%s",
                    bot.get("id"),
                )
                failed += 1
        await asyncio.sleep(SECONDS_BETWEEN_CALLS)
    return {"classified": classified, "failed": failed, "total": len(bots)}


async def classification_loop():
    """Run classify_all_once every LOOP_INTERVAL_SEC. Gated on the
    `influencer_classification` kill switch (DEFAULT OFF until Rishi
    reviews the sample output)."""
    from database import get_pool
    from kill_switch import is_enabled

    await asyncio.sleep(INITIAL_DELAY_SEC)
    while True:
        try:
            if not is_enabled("influencer_classification"):
                await asyncio.sleep(LOOP_INTERVAL_SEC)
                continue
            pool = await get_pool()
            t0 = datetime.now(timezone.utc)
            stats = await classify_all_once(pool)
            elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
            logger.info(
                "influencer_classification: %d classified, %d failed, "
                "%d total in %.1fs",
                stats["classified"],
                stats["failed"],
                stats["total"],
                elapsed,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "influencer_classification pass failed (non-fatal) — retrying next tick"
            )
        await asyncio.sleep(LOOP_INTERVAL_SEC)


# ─── admin override path (write-through, validates the 5-value enum) ────


async def apply_admin_override(
    pool,
    *,
    influencer_id: str,
    archetype: str | None = None,
    gender: str | None = None,
    category: str | None = None,
) -> dict:
    """Operator override surface. Validates `archetype` against the
    5-value enum (+ 'unknown'); rejects anything else. `gender` against
    the 4-value enum. `category` is free-form — any non-empty string
    accepted.

    Raises `ValueError` on a bad enum value so the route layer can
    translate to a 422.

    Returns the updated row as a dict.
    """
    set_parts: list[str] = []
    args: list = [influencer_id]
    if archetype is not None:
        if archetype not in VALID_ARCHETYPES:
            raise ValueError(
                f"archetype must be one of {sorted(VALID_ARCHETYPES)}; "
                f"got {archetype!r}"
            )
        args.append(archetype)
        set_parts.append(f"archetype = ${len(args)}")
    if gender is not None:
        if gender not in VALID_GENDERS:
            raise ValueError(
                f"gender must be one of {sorted(VALID_GENDERS)}; got {gender!r}"
            )
        args.append(gender)
        set_parts.append(f"gender = ${len(args)}")
    if category is not None:
        if not category.strip():
            raise ValueError("category must be a non-empty string when provided")
        # Free-form; just length-bound to match the column.
        args.append(category.strip()[:100])
        set_parts.append(f"category = ${len(args)}")
    if not set_parts:
        raise ValueError(
            "at least one of (archetype, gender, category) must be provided"
        )
    sql = (
        "UPDATE ai_influencers SET "
        + ", ".join(set_parts)
        + " WHERE id = $1 "
        + "RETURNING id, display_name, category, archetype, gender"
    )
    row = await pool.fetchrow(sql, *args)
    if row is None:
        raise LookupError(f"influencer not found: {influencer_id!r}")
    return dict(row)
