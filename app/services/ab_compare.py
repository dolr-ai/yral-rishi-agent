"""Phase 7.6 — on-demand A/B comparison of soul file variants.

Reuses the same Gemini-as-judge rubric as Phase 7.7's nightly scorer, but
runs it on a fresh sample at request time and groups results by variant
label. Result: per-variant aggregate scores so the creator can see which
variant performs better before deciding which to promote.

Sample size matters: with fewer than MIN_PER_VARIANT pairs per side the
comparison is treated as "not enough data yet" so creators don't promote
on noise.
"""

import asyncio
import logging

from services.quality_scorer import _judge_pair

logger = logging.getLogger(__name__)

MIN_PER_VARIANT = 10
MAX_PER_VARIANT = 20
JUDGE_CONCURRENCY = 5


async def _score_pairs(archetype: str, pairs: list[dict]) -> dict | None:
    """Run the same per-pair judge as Phase 7.7, return aggregated scores
    (or None if no pair successfully judged)."""
    if not pairs:
        return None

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
    return {
        "n": n,
        "in_character": round(sum(s["in_character"] for s in scores) / n, 3),
        "response_quality": round(sum(s["response_quality"] for s in scores) / n, 3),
        "engagement": round(sum(s["engagement"] for s in scores) / n, 3),
        "overall": round(
            sum(
                (s["in_character"] + s["response_quality"] + s["engagement"]) / 3
                for s in scores
            )
            / n,
            3,
        ),
    }


async def compare(pool, bot_id: str, bot_archetype: str) -> dict:
    """Pull labeled pairs for A and B, judge both groups, return comparison.

    Returns:
      {
        "variant_a": { ... aggregate or null ... },
        "variant_b": { ... aggregate or null ... },
        "ready_to_decide": bool,  // both sides have >= MIN_PER_VARIANT
        "hint": str | null,
      }
    """
    from repositories import variant_repo

    sample_a = await variant_repo.sample_replies_by_variant(
        pool, bot_id, "a", limit=MAX_PER_VARIANT
    )
    sample_b = await variant_repo.sample_replies_by_variant(
        pool, bot_id, "b", limit=MAX_PER_VARIANT
    )

    if len(sample_a) < MIN_PER_VARIANT or len(sample_b) < MIN_PER_VARIANT:
        return {
            "variant_a": {"n": len(sample_a)},
            "variant_b": {"n": len(sample_b)},
            "ready_to_decide": False,
            "hint": (
                f"Need at least {MIN_PER_VARIANT} replies per variant; "
                f"current: A={len(sample_a)}, B={len(sample_b)}. "
                "Let users chat with this bot for a while longer."
            ),
        }

    archetype = (bot_archetype or "general").lower().strip() or "general"
    a_scores, b_scores = await asyncio.gather(
        _score_pairs(archetype, sample_a),
        _score_pairs(archetype, sample_b),
    )

    out = {
        "variant_a": a_scores or {"n": 0},
        "variant_b": b_scores or {"n": 0},
        "ready_to_decide": bool(a_scores and b_scores),
        "hint": None,
    }
    if a_scores and b_scores:
        delta = b_scores["overall"] - a_scores["overall"]
        winner = "b" if delta > 0.1 else "a" if delta < -0.1 else "tie"
        out["delta_overall"] = round(delta, 3)
        out["suggested_winner"] = winner
    return out
