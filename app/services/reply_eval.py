"""Brief task 3 (2026-06-26) — L0 deterministic per-reply eval.

Pure-Python pass on every assistant reply. No LLM calls — the point
is "see problems automatically" without spend or latency. Wires into
chat.py's reply path as fire-and-forget via websocket_manager.spawn,
then persists one row per reply to `reply_evaluations` (migration 044).

What L0 catches today:

  - leak_flags: prompt-scaffolding bleed-through (THINK, Constraint
    checklist, "as an AI", planning blocks). Reply quality dies hard
    when the model leaks its prompt; L0 makes that visible at a
    glance.
  - repetition_score: 4-gram Jaccard overlap with the bot's last
    K=5 replies. Repetition is the most common "the model is stuck"
    failure mode. A score in [0..1] — higher = more overlap.
  - emoji_count + char_length + ends_in_question: low-cost
    descriptive features that the dashboard / future LLM-judge
    rubric can slice by.

L1+L2+L3 (LLM judges, golden set) build on top of these records.

Kill switch: `reply_eval_l0` (defaults OFF). Rishi flips after
migration 044 is applied and sample rows look right.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# Recent-history window for the repetition score. K=5 matches the
# brief; small enough to keep the SELECT cheap, large enough to catch
# "this bot is stuck on the same opening hook" pattern.
RECENT_REPLY_LIMIT = 5

# N-gram width for the repetition score. 4-gram catches phrase-level
# echoes ("how was your day"), 3-gram is too noisy on common bigrams,
# 5-gram misses paraphrased repetition.
NGRAM_N = 4

# Each entry is (flag_key, compiled_pattern). Patterns are tight
# enough to avoid false positives on normal prose but loose enough
# to catch the actual scaffolding leaks Session 6 found in Langfuse
# traces 2026-06-26. Add new patterns by appending here; the JSONB
# column accommodates new keys without a schema change.
_LEAK_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    # The bare word THINK on its own line or as a markdown header —
    # both shapes seen in production reply leaks. Case-sensitive to
    # avoid flagging the english verb in normal sentences.
    ("scaffolding_think", re.compile(r"(?m)^(?:\*\*)?THINK(?:\*\*)?:?\s*$")),
    # "Constraint checklist" / "**Constraint checklist:**" / variants —
    # the model parroting its own rule list back. Case-insensitive
    # because the leak takes many forms.
    ("scaffolding_constraint", re.compile(r"constraint\s+checklist", re.IGNORECASE)),
    # "**Plan for the response:**" — scaffolding from the agentic
    # planning chunk leaking into the reply.
    (
        "scaffolding_plan",
        re.compile(r"plan\s+for\s+the\s+response", re.IGNORECASE),
    ),
    # "This response is:" — meta-talk about the reply itself, almost
    # always scaffolding.
    (
        "scaffolding_meta",
        re.compile(r"^\s*this response is", re.IGNORECASE | re.MULTILINE),
    ),
    # "as an AI" / "as a language model" — the model breaking
    # character. Anchored to "as a/an" + variants to avoid false
    # positives on "Lisa as an AI persona…" style prose (which has
    # different surrounding context).
    ("as_an_ai", re.compile(r"\bas\s+an?\s+(?:ai|language\s+model)\b", re.IGNORECASE)),
)


@dataclass
class L0Evaluation:
    """Result of evaluate(). Mirrors the reply_evaluations columns
    one-for-one so the repo's insert() can splat fields in directly."""

    leak_flags: dict = field(default_factory=dict)
    repetition_score: float = 0.0
    emoji_count: int = 0
    char_length: int = 0
    ends_in_question: bool = False


def _ngrams(text: str, n: int = NGRAM_N) -> set[tuple[str, ...]]:
    """Word-level n-grams, lowercased. Empty / too-short text → empty
    set so Jaccard returns 0 cleanly (no division-by-zero)."""
    tokens = text.lower().split()
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    """Symmetric similarity in [0..1]. Both-empty = 0 (no signal)."""
    if not a or not b:
        return 0.0
    intersect = len(a & b)
    union = len(a | b)
    return intersect / union if union else 0.0


def _count_emoji(text: str) -> int:
    """Count code points whose Unicode category indicates a symbol /
    other-symbol (covers most emoji + dingbats). Cheap enough to run
    on every reply; precise enough for "did this reply have 10 emoji
    or 0?" — which is the question L0 actually answers."""
    count = 0
    for ch in text:
        cat = unicodedata.category(ch)
        # 'So' = Symbol, Other (covers most emoji); 'Sk' = Symbol,
        # Modifier (skin tones etc.).
        if cat in ("So", "Sk"):
            count += 1
    return count


def evaluate(text: str, recent_bot_replies: list[str]) -> L0Evaluation:
    """Pure-Python L0 pass. Caller is responsible for fetching the
    recent_bot_replies (we keep this function side-effect-free so it
    can be unit-tested without a pool).

    repetition_score is the maximum 4-gram Jaccard overlap between
    `text` and any single one of `recent_bot_replies`. Max-of-K
    catches "this bot reused the same opener as 3 turns ago" without
    requiring all K replies to match.
    """
    flags: dict = {}
    for key, pattern in _LEAK_PATTERNS:
        flags[key] = bool(pattern.search(text or ""))

    reply_ngrams = _ngrams(text or "")
    if reply_ngrams and recent_bot_replies:
        rep_score = max(
            _jaccard(reply_ngrams, _ngrams(prev)) for prev in recent_bot_replies
        )
    else:
        rep_score = 0.0

    return L0Evaluation(
        leak_flags=flags,
        repetition_score=float(rep_score),
        emoji_count=_count_emoji(text or ""),
        char_length=len(text or ""),
        ends_in_question=(text or "").rstrip().endswith("?"),
    )


async def run_and_persist(
    pool,
    *,
    message_id: str,
    bot_id: str,
    user_id: str,
    text: str,
) -> None:
    """Fetch the bot's recent replies, run evaluate(), persist one row
    to reply_evaluations. All failures are swallowed to logging — the
    eval is observability and must NEVER fail a chat send.

    Kill-switch gate: returns silently when reply_eval_l0 is OFF (the
    default until Rishi flips it post-deploy).
    """
    from kill_switch import is_enabled

    if not is_enabled("reply_eval_l0"):
        return

    try:
        from repositories import reply_eval_repo

        recent = await reply_eval_repo.recent_bot_reply_texts(
            pool, bot_id=bot_id, limit=RECENT_REPLY_LIMIT, exclude_message_id=message_id
        )
        result = evaluate(text, recent)
        await reply_eval_repo.insert(
            pool,
            message_id=message_id,
            bot_id=bot_id,
            user_id=user_id,
            text=text,
            evaluation=result,
        )
    except Exception as e:
        # L0 must never break the chat path. Log + continue; missing
        # rows surface as a dashboard tile drop, NOT a user-visible
        # error.
        logger.warning("reply_eval.run_and_persist skipped (%s)", e)
