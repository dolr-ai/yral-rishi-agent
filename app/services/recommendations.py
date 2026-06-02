"""Phase 7.8 — creator recommendations.

Generates 2-3 specific, actionable Soul File recommendations for a bot,
grounded in (current Soul File, latest quality scores, sample bad
conversations from the past 7 days).

Output shape mirrors the Soul File Coach's `proposed_changes` so creators
can hand a recommendation straight to the coach to apply.
"""

import json
import logging

from services import llm_registry

logger = logging.getLogger(__name__)


RECOMMENDATIONS_PROMPT = """You are an expert AI personality coach. A creator wants 2-3 SPECIFIC, ACTIONABLE recommendations to improve their bot's Soul File.

Bot details:
- Display name: {bot_name}
- Archetype: {bot_archetype}
- Current Soul File (system_instructions):
\"\"\"
{current_instructions}
\"\"\"

Latest quality scores (nightly Phase 7.7 scoring):
{quality_score_block}

Recent sample replies from this bot (anonymized; user messages omitted for privacy):
{sample_replies_block}

Your job:
1. Identify 2-3 SPECIFIC weaknesses (not generic). Tie each to a quality-score signal OR a concrete observation in the sample replies.
2. For each weakness, propose a TARGETED edit to the Soul File — exact text to add, replace, or remove. Not a rewrite.
3. Explain WHY each change would help, grounded in the data.

Return ONLY a JSON array of EXACTLY this shape (no markdown fences, no commentary outside the JSON):
[
  {{
    "weakness": "1-2 sentence summary of what's weak, with a score / observation citation",
    "proposed_edit": "the exact text to add, replace, or remove in system_instructions",
    "reasoning": "why this specific edit will improve the bot, tied to the data"
  }},
  ...
]

Rules:
- 2-3 recommendations, no more, no fewer
- proposed_edit MUST be specific — not "be warmer" but exact text like 'Add a rule: "Respond in at most 3 sentences when discussing personal topics."'
- Skip "polish" recommendations (capitalization, formatting). Focus on behavior changes."""


def _format_quality_score_block(score: dict | None) -> str:
    if not score:
        return "(no score yet — this bot is new or hasn't been sampled)"
    return (
        f"  overall: {score['score_overall']:.2f}/5\n"
        f"  in_character: {score['score_in_character']:.2f}/5\n"
        f"  response_quality: {score['score_response_quality']:.2f}/5\n"
        f"  engagement: {score['score_engagement']:.2f}/5\n"
        f"  (sampled {score['sample_size']} turn pairs across "
        f"{score['last_n_conversations']} conversations)"
    )


def _format_sample_replies(samples: list[dict]) -> str:
    """Render a flat list of bot replies (anonymized) for the prompt."""
    if not samples:
        return "(no recent samples)"
    lines = []
    for i, s in enumerate(samples[:15], 1):
        content = (s.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"  {i}. {content[:240]}")
    return "\n".join(lines) if lines else "(no recent samples)"


def _parse_recommendations(text: str) -> list[dict] | None:
    """Pull a JSON array from the model's reply. Tolerant of leading/trailing
    prose because LLMs occasionally wrap the JSON even when told not to."""
    start = text.find("[")
    end = text.rfind("]") + 1
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, list) or not obj:
        return None
    # Sanity: each item has the three required keys
    out = []
    for item in obj:
        if not isinstance(item, dict):
            continue
        for k in ("weakness", "proposed_edit", "reasoning"):
            if k not in item or not isinstance(item[k], str) or not item[k].strip():
                break
        else:
            out.append(
                {
                    "weakness": item["weakness"].strip(),
                    "proposed_edit": item["proposed_edit"].strip(),
                    "reasoning": item["reasoning"].strip(),
                }
            )
    return out or None


async def generate_recommendations(
    bot_name: str,
    bot_archetype: str,
    current_instructions: str,
    quality_score: dict | None,
    sample_bot_replies: list[dict],
) -> list[dict]:
    """Returns a list of 2-3 {weakness, proposed_edit, reasoning} dicts.

    Empty list on failure or model refusal — the route turns that into a 200
    with `recommendations: []` + a hint, never a 5xx.
    """
    prompt = RECOMMENDATIONS_PROMPT.format(
        bot_name=bot_name or "this bot",
        bot_archetype=bot_archetype or "general",
        current_instructions=current_instructions or "(empty)",
        quality_score_block=_format_quality_score_block(quality_score),
        sample_replies_block=_format_sample_replies(sample_bot_replies),
    )

    try:
        response = await llm_registry.call(
            process="soul_file_recommendations",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a specialist AI personality coach. Output JSON ONLY. "
                        "Be specific. Skip polish; focus on behavior changes."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=2048,
        )
        response_text = response.content
    except Exception as e:
        logger.warning(f"recommendations: llm call failed: {e}")
        return []

    parsed = _parse_recommendations(response_text)
    return parsed or []
