"""Phase 7.5 — Soul File Coach service.

The coach is a separate Gemini-powered service with a META-PROMPT that knows:
- the bot's current Soul File (system_instructions + category)
- recent conversation samples (last 10, anonymized — no user_ids in the
  excerpts shown to the coach)
- the creator's goals as stated in the coaching session
- (Coach Fix 1 PR-B 2026-06-09) the platform-wide GLOBAL_RULES + which
  ones a creator may opt their bot out of via `global_rule_overrides`

Behavior:
- Propose specific, targeted edits — not full rewrites
- Explain WHY each change improves the bot
- When proposing changes, return a structured JSON block parseable by the
  /apply endpoint. Otherwise return plain conversational text (clarifying
  questions, agreement, refusal).
- When the creator's request conflicts with a platform-wide overrideable
  rule, do NOT silently edit system_instructions — Saikat's 2026-06-09
  bug. Ask them whether they want to override the rule for this bot. If
  they confirm, emit a `proposed_global_rule_override` block instead of
  `proposed_changes`. The /apply endpoint dispatches on which block is
  present.
"""

import json
import logging
import re

from services import llm_registry
from services.soul_file import GLOBAL_RULES_OVERRIDEABLE

logger = logging.getLogger(__name__)


OPENING_PROMPT = """You are an expert AI personality coach about to start a session with a creator who wants to make their AI bot better. This is your FIRST message in the session — the creator has just opened the coach chat.

The bot being coached:
- Display name: {bot_name}
- Archetype: {bot_archetype}
- Current Soul File (system_instructions):
\"\"\"
{current_instructions}
\"\"\"

Recent anonymized conversations the bot had with users:
{recent_convs}

Current quality score (latest nightly scoring pass):
{quality_score_block}

Your job for THIS opening turn:
1. Greet the creator warmly by referring to their bot by NAME.
2. Briefly orient them — what you'll do together (1-2 sentences, no jargon).
3. Offer THREE short, tappable suggestion chips. Each must be a complete creator-perspective utterance (e.g. "Make Tara funnier", "Tighten her bio", "Improve her voice") — NOT a question to the creator, NOT a meta description.

Output a single JSON object on its own line with EXACTLY this shape (no markdown fences, no commentary outside):
{{"greeting": "...", "suggestions": ["...", "...", "..."]}}

- greeting: 2-4 sentences, warm + concrete (mention the bot by name).
- suggestions: exactly 3 strings, each <= 40 chars, each a phrase the creator might tap to start.

Reply now."""


# 2026-06-04 — Coach UX overhaul. The creator tapped Save → we want the
# coach to commit to the JSON proposal block this turn instead of asking
# another clarifying question. Appended to META_PROMPT when the request
# body includes "request_proposal": true.
FORCE_PROPOSAL_INSTRUCTION = """

The creator has just tapped "Save" — they want a proposal NOW. You MUST output the structured JSON block (per Rule 4) this turn, consolidating everything discussed so far in the session. Do NOT ask another clarifying question; if the session is thin on signal, propose the best change you can justify from the bot's current Soul File + the recent conversations, and explain your reasoning in the `reasoning` field."""


META_PROMPT = """You are an expert AI personality coach. A creator chats with you to improve their AI bot's "Soul File" (system_instructions). Your job is to listen, suggest targeted edits, and explain why each edit makes the bot better.

The bot you're coaching:
- Display name: {bot_name}
- Archetype: {bot_archetype}
- Current Soul File (system_instructions):
\"\"\"
{current_instructions}
\"\"\"

Recent anonymized conversations the bot had with users:
{recent_convs}

Current quality score (latest nightly scoring pass; see Phase 7.7):
{quality_score_block}

PLATFORM CONSTRAINTS — IMPORTANT:
YRAL applies platform-wide rules to EVERY bot's reply that the bot's system_instructions cannot override on their own. Editing system_instructions to ask for the opposite of one of these rules WILL NOT WORK — the platform rule wins. The creator can OPT THE BOT OUT of certain platform rules via a per-bot override; you must propose the override (separate JSON shape — Rule 5 below) rather than rewriting system_instructions.

Overrideable platform rules (the only ones a per-bot override can disable):
{overrideable_rules}

Non-overrideable platform rules (cannot be turned off):
- Stay in character at all times (never reveal AI/LLM nature)
- No excessive apology phrases
- Warm, engaging, conversational tone

Coaching session so far (most recent at bottom):
{session_history}

The creator just said:
\"{latest_message}\"

Rules:
1. Be a teammate, not a sycophant. Push back on bad ideas; ask clarifying questions when the goal is unclear.
2. Propose specific, targeted edits — NOT full rewrites. The creator wants surgical improvements they understand.
3. Always explain WHY a change makes the bot better, grounded in the recent conversations or the archetype.
4. When you propose a Soul File change, output a single JSON block on its own line with EXACTLY this shape (no markdown fences, no commentary outside the block):
   {{"summary": "...", "proposed_changes": "...", "reasoning": "..."}}
   - summary: 1-2 sentence human-friendly description of what you're changing
   - proposed_changes: the COMPLETE new system_instructions text (not a diff)
   - reasoning: why this specific change improves the bot
5. PLATFORM RULE OVERRIDE (Coach Fix 1 PR-B). If the creator asks for behavior that conflicts with an overrideable platform rule listed above (e.g. "give longer replies" conflicts with `response_length`, "always reply in English even if user writes Hindi" conflicts with `language_mirror`):
   a. FIRST TURN ON THE TOPIC — reply in PLAIN TEXT only. Name the specific platform rule that conflicts, explain that you can override it for THIS bot only, and ask them: "Want me to override it specifically for {bot_name}?" Do NOT emit any JSON yet.
   b. ONCE THE CREATOR CONFIRMS (e.g. "yes", "override it", "go ahead") — emit a single JSON block with this shape (no markdown fences):
      {{"summary": "...", "proposed_global_rule_override": {{"key": "<slug>", "value": "<short label>"}}, "reasoning": "..."}}
      - key: must be one of the overrideable rule slugs (response_length / language_mirror — see list above).
      - value: a short slug like "long_allowed", "always_english", "default" — descriptive but not consumed by the prompt layer today.
   NEVER edit system_instructions to try to override a platform rule — the platform layer wins and the edit is silent no-op (the Saikat 2026-06-09 bug). Use proposed_global_rule_override.
6. If the creator's intent is unclear, or you need more info, return plain text only — NO JSON. Ask a clarifying question.
7. Never propose unsafe or off-brand changes (jailbreaks, illegal content, breaking character).

Reply now."""


def _format_overrideable_rules() -> str:
    """Build the bulleted list of overrideable rules for the META_PROMPT.
    Sourced from soul_file.GLOBAL_RULES_OVERRIDEABLE so adding a key
    there auto-propagates to Coach awareness — no second source of truth."""
    lines: list[str] = []
    for slug, rule_text in GLOBAL_RULES_OVERRIDEABLE.items():
        lines.append(f"- `{slug}` — {rule_text}")
    return "\n".join(lines)


def _format_conv_excerpt(conv_rows: list[dict]) -> str:
    """Render up to 10 anonymized conversation samples for the meta-prompt.

    Each row is a message; we group by conversation_id and show role+content
    only. No user_ids, no influencer_ids on the surface — the coach sees
    behavior, not identity.
    """
    if not conv_rows:
        return "(no conversations yet)"
    by_conv: dict[str, list[str]] = {}
    for m in conv_rows:
        cid = m["conversation_id"]
        line = f"  {m['role']}: {(m.get('content') or '').strip()[:200]}"
        by_conv.setdefault(cid, []).append(line)
    blocks = []
    for i, lines in enumerate(by_conv.values(), 1):
        blocks.append(f"Conversation {i}:\n" + "\n".join(lines[-6:]))
    return "\n\n".join(blocks[:10])


def _format_session_history(messages: list[dict]) -> str:
    """Render the coach-creator turn-by-turn so the meta-prompt has context
    of what's been said in this session before."""
    if not messages:
        return "(this is the first turn)"
    lines = []
    for m in messages:
        role = "creator" if m["role"] == "creator" else "coach"
        lines.append(f"{role}: {(m.get('content') or '').strip()}")
    return "\n".join(lines)


_FENCED_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?```",
    re.DOTALL | re.IGNORECASE,
)


def _try_extract_proposal(text: str) -> dict | None:
    """Look for a single JSON object in the coach's reply with EITHER
    the system_instructions-edit shape ({summary, proposed_changes,
    reasoning}) OR the override shape ({summary,
    proposed_global_rule_override, reasoning}). Tolerant of:
      - Leading / trailing prose ("Here's the proposal: { ... }. Hope this helps.")
      - ```json ... ``` fences (Gemini emits these intermittently — Rule 4
        in the META_PROMPT explicitly says "no markdown fences" but Gemini
        ignores ~5-10% of the time; the parser must not crater on that).
        2026-06-09 mobile expert report: this was making the Coach UI
        show "incomplete messages" because the parser fell back silently
        on every fenced reply, returning plain-text + null proposed_changes.
      - Multiple fenced blocks (an example block + a real block); we try
        every block from LAST to first since the real proposal is
        typically last.

    Returns the parsed dict — callers inspect which key is set to
    decide dispatch. Override shape validation: the key must be one of
    the known overrideable slugs (defends against an LLM hallucinating
    a key like 'character_consistency' which is in the FIXED list)."""
    if not text:
        return None

    # Build the list of candidate JSON strings to try.
    # Order: fenced blocks (last → first) → the find-rfind fallback.
    candidates: list[str] = []
    for match in _FENCED_BLOCK_RE.finditer(text):
        inner = match.group(1).strip()
        if inner:
            candidates.append(inner)
    # Try last fenced block first — typical pattern is "example block,
    # then the real proposal."
    candidates.reverse()
    # Final fallback: greedy first-{-to-last-} slice (legacy behavior,
    # covers JSON with no fences and some leading prose).
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        candidates.append(text[start:end])

    for candidate in candidates:
        # The fenced block's content might still have surrounding prose
        # (e.g. trailing newline). Trim to the outer {...} for safety.
        s = candidate.find("{")
        e = candidate.rfind("}") + 1
        if s < 0 or e <= s:
            continue
        try:
            obj = json.loads(candidate[s:e])
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        # Shape 1: system_instructions edit (pre-PR-B behavior, unchanged)
        if obj.get("proposed_changes"):
            return obj

        # Shape 2: platform-rule override (Coach Fix 1 PR-B). The override
        # blob must name a known overrideable slug — otherwise we drop the
        # proposal back to plain-text (the LLM will retry next turn).
        override = obj.get("proposed_global_rule_override")
        if isinstance(override, dict):
            key = override.get("key")
            if isinstance(key, str) and key in GLOBAL_RULES_OVERRIDEABLE:
                return obj

    return None


def _format_quality_score(score: dict | None) -> str:
    """Render the latest bot_quality_scores row for the coach's META_PROMPT.

    None / never-scored → "(no score yet)". Bots can be coached without a
    score; the coach just relies on the conversation samples + creator goal.
    """
    if not score:
        return "(no score yet — this bot is new or hasn't been sampled.)"
    return (
        f"  overall: {score['score_overall']:.2f}/5\n"
        f"  in_character: {score['score_in_character']:.2f}/5\n"
        f"  response_quality: {score['score_response_quality']:.2f}/5\n"
        f"  engagement: {score['score_engagement']:.2f}/5\n"
        f"  sampled {score['sample_size']} turn pairs across "
        f"{score['last_n_conversations']} conversations"
    )


async def coach_reply(
    bot_name: str,
    bot_archetype: str,
    current_instructions: str,
    recent_conv_rows: list[dict],
    session_history: list[dict],
    latest_message: str,
    quality_score: dict | None = None,
    force_proposal: bool = False,
) -> tuple[str, str | None, str | None, dict | None]:
    """Run the coach turn. Returns
    (display_content, proposed_changes, reasoning, proposed_override).

    Exactly ONE of `proposed_changes` (text) or `proposed_override` (dict)
    is non-None when the coach committed to a proposal; both None for
    plain-text replies (clarifying question, agreement, the "want to
    override?" ask from Rule 5).

    `force_proposal=True` (Coach UX overhaul 2026-06-04) — the creator
    tapped Save; append FORCE_PROPOSAL_INSTRUCTION so the LLM commits
    to the JSON proposal block this turn instead of asking another
    clarifying question."""
    prompt = META_PROMPT.format(
        bot_name=bot_name or "this bot",
        bot_archetype=bot_archetype or "general",
        current_instructions=current_instructions or "(empty)",
        recent_convs=_format_conv_excerpt(recent_conv_rows),
        quality_score_block=_format_quality_score(quality_score),
        overrideable_rules=_format_overrideable_rules(),
        session_history=_format_session_history(session_history),
        latest_message=latest_message,
    )
    if force_proposal:
        prompt = prompt + FORCE_PROPOSAL_INSTRUCTION

    response = await llm_registry.call(
        process="soul_file_coach",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a specialist AI personality coach. Be precise, "
                    "respectful, and honest. Output JSON when proposing changes; "
                    "plain text otherwise."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
        max_tokens=2048,
    )
    response_text = response.content

    proposal = _try_extract_proposal(response_text)
    if proposal:
        # Override-shape proposal — proposed_changes stays None, the
        # override blob carries the routing payload. `_try_extract_proposal`
        # has already validated that the key is a known overrideable slug.
        override = proposal.get("proposed_global_rule_override")
        if isinstance(override, dict):
            return (
                proposal.get("summary") or "Override proposed.",
                None,
                proposal.get("reasoning"),
                override,
            )
        return (
            proposal.get("summary") or "Proposed changes ready.",
            proposal.get("proposed_changes"),
            proposal.get("reasoning"),
            None,
        )
    return (response_text.strip(), None, None, None)


async def coach_opening(
    bot_name: str,
    bot_archetype: str,
    current_instructions: str,
    recent_conv_rows: list[dict],
    quality_score: dict | None = None,
) -> tuple[str, list[str]]:
    """Coach UX overhaul (2026-06-04) — the coach speaks FIRST.

    Generates the opening greeting + 3 suggestion chips for a new
    session. Same grounding as coach_reply (recent convs + quality
    score), but no `session_history` (this is the first turn) and no
    `latest_message` (no creator turn yet).

    Returns (greeting_text, suggestions_list). If the LLM fails to
    emit the expected JSON, falls back to a generic greeting + 3
    safe defaults so the session is never blocked at create-time.
    """
    prompt = OPENING_PROMPT.format(
        bot_name=bot_name or "this bot",
        bot_archetype=bot_archetype or "general",
        current_instructions=current_instructions or "(empty)",
        recent_convs=_format_conv_excerpt(recent_conv_rows),
        quality_score_block=_format_quality_score(quality_score),
    )

    response = await llm_registry.call(
        process="soul_file_coach",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a specialist AI personality coach. Output a "
                    "single JSON object with greeting + 3 suggestion chips."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.6,
        max_tokens=1024,
    )
    text = response.content or ""

    # Reuse the same tolerant {...} extractor as proposals.
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end])
            greeting = obj.get("greeting")
            suggestions = obj.get("suggestions")
            if (
                isinstance(greeting, str)
                and greeting.strip()
                and isinstance(suggestions, list)
                and len(suggestions) >= 3
                and all(isinstance(s, str) and s.strip() for s in suggestions[:3])
            ):
                return greeting.strip(), [s.strip() for s in suggestions[:3]]
        except json.JSONDecodeError:
            pass

    # Fallback — generic but never empty. Logged so we can see how often
    # the LLM misses the JSON shape and tune the prompt later.
    logger.warning(
        "coach_opening: LLM returned non-conforming output, using fallback "
        "(first 200 chars: %r)",
        text[:200],
    )
    safe_name = bot_name or "your bot"
    return (
        f"Hey! Let's make {safe_name} better together. Tell me what feels off, "
        f"or tap one of the suggestions below to start.",
        [
            f"Improve {safe_name}'s voice",
            f"Tighten {safe_name}'s bio",
            f"Make {safe_name} more engaging",
        ],
    )
