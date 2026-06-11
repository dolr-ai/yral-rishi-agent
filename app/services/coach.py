"""Phase 7.5 — Soul File Coach service.

The coach is a separate Gemini-powered service with a META-PROMPT that knows:
- the bot's current Soul File (system_instructions + category)
- recent conversation samples (last 10, anonymized — no user_ids in the
  excerpts shown to the coach)
- the creator's goals as stated in the coaching session
- (Coach Fix 1 PR-B 2026-06-09) the platform-wide GLOBAL_RULES + which
  ones a creator may opt their bot out of via `global_rule_overrides`
- (Coach Bucket 2 PR-2 2026-06-11) when COACH_SECTIONED_V2_ENABLED is on
  AND the bot has non-empty system_instructions_sections, the Soul File
  is presented as an ordered list of sections; Coach proposes against
  ONE section per turn via the proposed_section_change shape.

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

import hashlib
import json
import logging
import re

import config
from services import llm_registry
from services.soul_file import GLOBAL_RULES_OVERRIDEABLE, _coerce_sections

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
{soul_file_block}

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
{section_rules}
Reply now."""


# Bucket 2 — when sections are active, Rule 4 changes to require a
# section-scoped proposal shape instead of a full-text rewrite. Appended
# to META_PROMPT as Rule 8 so the existing 1-7 numbering stays stable
# for the historical flat-text path.
SECTION_RULES_ADDENDUM = """
8. SECTIONED SOUL FILE (Bucket 2). This bot's Soul File is broken into typed sections — propose against ONE section per turn instead of rewriting the whole instructions. When you propose a sectioned edit, emit a single JSON block with EXACTLY this shape (no markdown fences, no commentary outside the block) — ALL FIVE fields inside `proposed_section_change` are REQUIRED:
   {{"summary": "...", "proposed_section_change": {{"section_id": "<id>", "section_heading": "<heading exactly as shown above>", "section_editable": true, "new_body": "<COMPLETE new body for that one section>", "previous_body_sha256": "<sha of body as YOU read it>"}}, "reasoning": "..."}}
   - section_id MUST be one of the ids shown above. Refuse to invent a new id.
   - section_heading MUST be a snapshot of the heading EXACTLY as shown above (mobile renders the badge "Coach proposed an edit to **<heading>**" from this).
   - section_editable MUST be a snapshot of the editable flag EXACTLY as shown above (mobile gates the Apply button on this).
   - new_body is the COMPLETE replacement for that section's body (not a diff).
   - previous_body_sha256 is a sha256 of the section body EXACTLY as shown above. The apply endpoint rejects proposals against drifted sections.
   - Refuse to propose against sections marked editable=false. Reply in plain text explaining the section is read-only.
   - Refuse to rewrite multiple sections in one turn. Propose against ONE; tell the creator you'll handle the others in follow-up turns.
   Sectioned proposals REPLACE Rule 4 (proposed_changes) when sections are active — never emit both shapes in the same turn."""


def section_body_sha256(body: str | None) -> str:
    """Canonical sha256 of a section's body. Used both at meta-prompt
    render time (to show Coach which sha to echo back) AND at /apply
    time (to compare Coach's claimed previous_body_sha256 against the
    live body). The contract: strip leading/trailing whitespace then
    sha256 the utf-8 bytes. Trimming makes the sha stable across the
    cosmetic whitespace LLMs sometimes add."""
    canonical = (body or "").strip().encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _format_soul_file_block(
    current_instructions: str, sections: list[dict] | str | None
) -> tuple[str, bool]:
    """Return (rendered_block, sectioned_mode_active).

    When COACH_SECTIONED_V2_ENABLED is on AND the bot has at least one
    section, the block lists each section with its id + editable flag
    + sha so Coach can echo previous_body_sha256 back into the
    proposed_section_change shape. Otherwise renders the flat text
    block exactly as the pre-Bucket 2 META_PROMPT did.
    """
    sections_list = _coerce_sections(sections)
    if config.COACH_SECTIONED_V2_ENABLED and sections_list:
        lines = ["- Current Soul File (sections — propose against ONE):"]
        for sec in sections_list:
            section_id = sec.get("id") or "(missing-id)"
            heading = (sec.get("heading") or "Untitled").strip()
            editable = bool(sec.get("editable", True))
            body = (sec.get("body") or "").strip()
            sha = section_body_sha256(body)
            lines.append("")
            lines.append(
                f"  == {heading} == [id={section_id}, editable={str(editable).lower()}, sha={sha[:12]}…]"
            )
            lines.append(f'  """\n  {body}\n  """')
            lines.append(f"  (full sha256 of body: {sha})")
        return "\n".join(lines), True
    # Flat-text path — same shape as pre-PR-2 META_PROMPT.
    return (
        f'- Current Soul File (system_instructions):\n  """\n  {current_instructions}\n  """',
        False,
    )


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


# Plain-English fallback the route + mobile show when the model emitted
# JSON-shaped output that failed to parse (truncated mid-stream, missing
# closing brace, etc.). Better than dumping raw `{"summary": "...`.
TRUNCATED_REPROMPT_TEXT = (
    "Sorry — my last reply got cut off before I could finish the proposal. "
    "Tell me again what you'd like to change about the bot and I'll redo it."
)


# JSON-shape markers used by the truncation detector. Includes the
# proposal shapes (Rule 4 / Rule 5) so we recognize fragments that LOOK
# proposal-ish but can't parse.
_JSON_SHAPE_MARKERS = (
    '"summary"',
    '"proposed_changes"',
    '"proposed_global_rule_override"',
    '"proposed_section_change"',
    '"section_id"',
    '"new_body"',
    '"previous_body_sha256"',
    '"reasoning"',
    '"key"',
    '"value"',
)


def _looks_like_truncated_proposal(text: str | None) -> bool:
    """Heuristic: does the response APPEAR to be a JSON proposal that
    failed mid-stream? Used by coach_reply when parse_proposal returns
    None — we'd rather surface a clean reprompt than dump the
    half-string to the creator.

    Conservative on TRUE — we only flag responses that contain at
    least one canonical proposal JSON key AND show structural damage
    (unbalanced braces or quotes). A plain-text reply with no JSON
    markers (clarifying question, agreement) returns False and falls
    through to the normal plain-text path."""
    if not text:
        return False
    if not any(marker in text for marker in _JSON_SHAPE_MARKERS):
        return False
    # Structural damage signals: unbalanced braces / unmatched quotes.
    opens = text.count("{")
    closes = text.count("}")
    if opens > closes:
        return True
    # Even number of quotes is the expected case; odd = unmatched.
    quotes = text.count('"')
    if quotes % 2 == 1:
        return True
    # All braces closed + balanced quotes + still failed to parse →
    # something else is wrong (escape errors, etc.); call it truncated
    # too because the route can't surface it meaningfully.
    return True


def _iter_json_candidates(text: str) -> list[dict]:
    """Single source of truth for JSON extraction from a Gemini reply.

    2026-06-11 PR-2 (Codex review §4 / plan §3 #5): both proposals
    AND openings go through this. Before, `_try_extract_proposal` had
    the fenced-block tolerance from PR #337 but `coach_opening`'s
    inline `text.find("{")` parser did NOT — that's why mobile saw
    generic greetings on bots with real content. The JSON was wrapped
    in ```json fences and the opener parser silently fell back.

    Returns ALL successfully-parsed dict objects in priority order:
      1. Fenced ```json ... ``` blocks, LAST fence first (the real
         block typically follows an example fence).
      2. The greedy first-{-to-last-} slice as the find-rfind fallback.

    Callers apply their shape validator on top (parse_proposal /
    parse_opening) and pick the first match. Conservative on success:
    malformed candidates are silently skipped, not raised."""
    if not text:
        return []

    raw: list[str] = []
    for match in _FENCED_BLOCK_RE.finditer(text):
        inner = match.group(1).strip()
        if inner:
            raw.append(inner)
    raw.reverse()  # last fence first
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        raw.append(text[start:end])

    parsed: list[dict] = []
    for candidate in raw:
        s = candidate.find("{")
        e = candidate.rfind("}") + 1
        if s < 0 or e <= s:
            continue
        try:
            obj = json.loads(candidate[s:e])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            parsed.append(obj)
    return parsed


def parse_proposal(text: str) -> dict | None:
    """Validate the proposal shape. Returns the first candidate that
    matches ANY of three shapes:
      - system_instructions edit: {summary, proposed_changes, reasoning}
      - platform-rule override: {summary,
        proposed_global_rule_override: {key, value}, reasoning}
        where `key` is in GLOBAL_RULES_OVERRIDEABLE.
      - sectioned edit (Bucket 2): {summary,
        proposed_section_change: {section_id, section_heading,
        section_editable, new_body, previous_body_sha256}, reasoning}
        section_id + new_body are the load-bearing fields.

    Returns None when no candidate matches — callers then surface the
    response as plain text (clarifying question / agreement / refusal)."""
    for obj in _iter_json_candidates(text):
        # Shape 1: system_instructions edit
        if obj.get("proposed_changes"):
            return obj
        # Shape 2: platform-rule override (Coach Fix 1 PR-B). The
        # override blob must name a known overrideable slug — otherwise
        # it falls back to plain-text (LLM retries on next turn).
        override = obj.get("proposed_global_rule_override")
        if isinstance(override, dict):
            key = override.get("key")
            if isinstance(key, str) and key in GLOBAL_RULES_OVERRIDEABLE:
                return obj
        # Shape 3 (Bucket 2): sectioned edit. section_id + new_body must
        # be non-empty strings — without either, /apply has nothing to
        # do. previous_body_sha256 is recommended but not parser-required
        # so a Coach-emitted blob that forgot the sha still parses; the
        # /apply endpoint enforces the concurrency check separately.
        section_change = obj.get("proposed_section_change")
        if isinstance(section_change, dict):
            section_id = section_change.get("section_id")
            new_body = section_change.get("new_body")
            if (
                isinstance(section_id, str)
                and section_id.strip()
                and isinstance(new_body, str)
                and new_body.strip()
            ):
                return obj
    return None


def parse_opening(text: str) -> tuple[str, list[str]] | None:
    """Validate the opening shape: {greeting: str, suggestions: list[str]}
    with ≥3 non-empty suggestion strings. Returns
    (greeting, suggestions[:3]) on success, None on miss.

    Mirrors parse_proposal — shared candidate extractor means a fenced
    opening parses the same as a fenced proposal. Pre-PR-2 the opener
    used a naive text.find('{') parser that broke on ```json fences
    (5-10% of Gemini openings), producing the generic fallback greeting
    even on bots with real history."""
    for obj in _iter_json_candidates(text):
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
    return None


def _try_extract_proposal(text: str) -> dict | None:
    """Back-compat shim. New code should call parse_proposal directly."""
    return parse_proposal(text)


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
    sections: list[dict] | str | None = None,
) -> tuple[str, str | None, str | None, dict | None, dict | None]:
    """Run the coach turn. Returns
    (display_content, proposed_changes, reasoning, proposed_override,
    proposed_section_change).

    Exactly ONE of `proposed_changes` (text) / `proposed_override` (dict)
    / `proposed_section_change` (dict) is non-None when the coach
    committed to a proposal; all three None for plain-text replies
    (clarifying question, agreement, the "want to override?" ask from
    Rule 5).

    `force_proposal=True` (Coach UX overhaul 2026-06-04) — the creator
    tapped Save; append FORCE_PROPOSAL_INSTRUCTION so the LLM commits
    to the JSON proposal block this turn instead of asking another
    clarifying question.

    `sections` (Bucket 2) — the bot's system_instructions_sections JSONB.
    When `config.COACH_SECTIONED_V2_ENABLED` is True AND the bot has at
    least one section, the META_PROMPT renders the sectioned block + adds
    SECTION_RULES_ADDENDUM. Otherwise falls back to the flat-text block
    (today's pre-Bucket-2 behavior). Coach decides which proposal shape
    to emit; the parser accepts whichever it gets."""
    soul_file_block, sectioned_mode = _format_soul_file_block(
        current_instructions or "(empty)", sections
    )
    prompt = META_PROMPT.format(
        bot_name=bot_name or "this bot",
        bot_archetype=bot_archetype or "general",
        soul_file_block=soul_file_block,
        recent_convs=_format_conv_excerpt(recent_conv_rows),
        quality_score_block=_format_quality_score(quality_score),
        overrideable_rules=_format_overrideable_rules(),
        session_history=_format_session_history(session_history),
        latest_message=latest_message,
        section_rules=SECTION_RULES_ADDENDUM if sectioned_mode else "",
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
        # 2026-06-11 PR-1: bumped 2048 → 4096. The 2048 cap was
        # truncating long-reasoning replies mid-stream — creator saw
        # "...." answers that ended mid-thought (Rishi's Motorola
        # complaint from yesterday's dev report). 4096 leaves headroom
        # for the full proposal JSON block + multi-sentence reasoning.
        # Gemini Flash returns most coach replies well under 1500
        # tokens; the cap is a safety belt, not a typical limit.
        max_tokens=4096,
    )
    response_text = response.content

    proposal = _try_extract_proposal(response_text)
    if proposal:
        # Bucket 2 — sectioned edit. Section-shape proposals carry their
        # own validation in parse_proposal; here we just route.
        section_change = proposal.get("proposed_section_change")
        if isinstance(section_change, dict):
            return (
                proposal.get("summary") or "Section change proposed.",
                None,
                proposal.get("reasoning"),
                None,
                section_change,
            )
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
                None,
            )
        return (
            proposal.get("summary") or "Proposed changes ready.",
            proposal.get("proposed_changes"),
            proposal.get("reasoning"),
            None,
            None,
        )
    # 2026-06-11 PR-1: if the LLM emitted JSON-shaped output that
    # FAILED to parse (truncated mid-stream, opening { but no closing
    # }), don't dump the raw half-JSON to the creator — it shows up
    # as `{"summary": "...` and looks like a bug to a non-technical
    # user. Detect the partial-JSON signal and substitute a clean
    # "let me redo that" reprompt instead.
    if _looks_like_truncated_proposal(response_text):
        logger.warning(
            "coach_reply: response looks truncated/partial JSON (len=%d); "
            "surfacing reprompt instead of raw fragment",
            len(response_text or ""),
        )
        return (TRUNCATED_REPROMPT_TEXT, None, None, None, None)
    return (response_text.strip(), None, None, None, None)


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

    # 2026-06-11 PR-2: use the shared parse_opening validator built on
    # _iter_json_candidates — same fenced-block tolerance the proposal
    # extractor uses. Pre-refactor the opener had its own naive
    # text.find('{') parser that broke on ```json fences (~5-10% of
    # Gemini openings), producing the generic fallback greeting even
    # on bots with real history (plan §3 #5).
    parsed = parse_opening(text)
    if parsed is not None:
        return parsed

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
