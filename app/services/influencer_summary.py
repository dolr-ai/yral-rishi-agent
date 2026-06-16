"""Coach Fix 2 backend — plain-English bot-summary generation.

Generates 5-7 bullets describing what a bot does (personality + behavior
rules) from its EFFECTIVE prompt (GLOBAL + ARCHETYPE + per-influencer +
overrides). Cached on `ai_influencers.metadata` so repeat reads are
free; regenerated when the bot's `updated_at` advances past the cache.

Each bullet has an optional `override_target` pointing at one of the
overrideable platform rules (from soul_file.GLOBAL_RULES_OVERRIDEABLE)
so mobile can render a "this is a platform default — tap to override"
affordance.

The LLM call goes through llm_registry.call(process="soul_file_coach")
so it shares the routing / cost-tracking / observability the rest of
Coach uses. Default provider per LLM_DEFAULTS is gemini — a sync
creator-waiting path per `feedback_llm_defaults_sync_paths_use_gemini`.
"""

import json
import logging
from datetime import datetime, timezone

from services import llm_registry, soul_file

logger = logging.getLogger(__name__)


# 5-7 bullets keeps the UI scannable. The override_target field is
# nullable; only bullets that reflect a current platform default get
# tagged so mobile can offer a one-tap override CTA.
_SUMMARY_PROMPT = """You are summarizing an AI personality so a non-technical creator can understand it at a glance.

Below is the EFFECTIVE system prompt the bot uses on every chat. Read it and emit a JSON object with 5-7 plain-English bullets. Each bullet captures one behavior, vibe, or constraint — written so a creator who has never written a system prompt can understand it in one read.

Effective system prompt:
\"\"\"
{effective_prompt}
\"\"\"

Output format (single JSON object, no markdown fences, no commentary outside):
{{"bullets": [
    {{"text": "<plain-English description>", "category": "<personality|reply_length|language|tone|constraint|other>", "override_target": "<override-slug or null>"}},
    ...
]}}

Rules:
- 5 to 7 bullets total.
- "text" is one sentence, no jargon. Refer to the bot as "she/he/they" or by archetype, NOT "the bot" repeatedly.
- "category" is one of: personality, reply_length, language, tone, constraint, other.
- "override_target": if this bullet describes one of the OVERRIDEABLE platform rules below, set this field to the matching slug. Otherwise null.
- Order bullets from most distinctive (the bot's personality) to least (universal platform constraints).

Overrideable platform rule slugs you can tag (override_target):
{overrideable_slugs}

Output the JSON now."""


def _format_overrideable_slugs() -> str:
    lines: list[str] = []
    for slug, rule_text in soul_file.GLOBAL_RULES_OVERRIDEABLE.items():
        lines.append(f"- {slug}: {rule_text}")
    return "\n".join(lines)


def _validate_summary(parsed: object) -> dict | None:
    """Defense against LLM emitting nonsense. Returns the parsed dict
    if it conforms, else None — caller surfaces an error or retries."""
    if not isinstance(parsed, dict):
        return None
    bullets = parsed.get("bullets")
    if not isinstance(bullets, list) or not (3 <= len(bullets) <= 10):
        return None
    overrideable = set(soul_file.GLOBAL_RULES_OVERRIDEABLE.keys())
    cleaned: list[dict] = []
    for b in bullets:
        if not isinstance(b, dict):
            return None
        text = b.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        category = b.get("category") or "other"
        if not isinstance(category, str):
            category = "other"
        target = b.get("override_target")
        if target is not None and target not in overrideable:
            # Drop the bogus override target rather than rejecting the
            # whole bullet — text + category are still useful.
            target = None
        cleaned.append(
            {
                "text": text.strip(),
                "category": category,
                "override_target": target,
            }
        )
    return {"bullets": cleaned}


async def generate_for_influencer(inf: dict) -> dict:
    """Run the LLM + validate. Caller owns persistence + cache decisions.

    `inf` is an ai_influencers row dict — uses `system_instructions`,
    `category`, and `global_rule_overrides` to compose the effective
    prompt the LLM actually summarizes."""
    effective_prompt = soul_file.compose(
        system_instructions=inf.get("system_instructions") or "",
        category=inf.get("category"),
        archetype=inf.get("archetype"),
        global_rule_overrides=inf.get("global_rule_overrides"),
        sections=inf.get("system_instructions_sections"),
    )
    prompt = _SUMMARY_PROMPT.format(
        effective_prompt=effective_prompt,
        overrideable_slugs=_format_overrideable_slugs(),
    )
    response = await llm_registry.call(
        process="soul_file_coach",
        messages=[
            {
                "role": "system",
                "content": (
                    "You write plain-English summaries of AI personalities for "
                    "non-technical creators. Be specific and concise."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=800,
    )
    text = response.content
    # Tolerant of LLM wrapping JSON in prose
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise RuntimeError(f"summary LLM returned no JSON: {text[:200]}")
    try:
        parsed = json.loads(text[start:end])
    except json.JSONDecodeError as e:
        raise RuntimeError(f"summary LLM JSON parse failed: {e}") from e
    cleaned = _validate_summary(parsed)
    if cleaned is None:
        raise RuntimeError(f"summary LLM emitted malformed structure: {text[:200]}")
    cleaned["generated_at"] = datetime.now(timezone.utc).isoformat()
    return cleaned


def cache_is_fresh(inf: dict) -> dict | None:
    """Return the cached summary if it's newer than the bot's last
    update — else None (caller regenerates).

    Cache lives in `ai_influencers.metadata`:
      - plain_english_summary: the {bullets, generated_at} dict
      - summary_generated_at: ISO timestamp (parallel field for cheap
        comparison without parsing the bullets blob)

    Stale check uses `inf["updated_at"]` (the row column) vs
    `metadata.summary_generated_at`. If the bot was edited after the
    summary was generated, the summary is stale."""
    metadata = inf.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(metadata, dict):
        return None
    cached = metadata.get("plain_english_summary")
    if not isinstance(cached, dict):
        return None
    generated_at_iso = metadata.get("summary_generated_at")
    if not isinstance(generated_at_iso, str):
        return None
    try:
        generated_at = datetime.fromisoformat(generated_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    updated_at = inf.get("updated_at")
    if updated_at is None:
        # No updated_at on the row — treat cache as fresh; better than
        # regenerating on every call when we have no way to know it's stale.
        return cached
    if isinstance(updated_at, str):
        try:
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            return cached
    # Some rows come back as naive datetime; coerce to UTC for the compare.
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    if updated_at > generated_at:
        return None  # Stale — regenerate.
    return cached
