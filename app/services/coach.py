"""Phase 7.5 — Soul File Coach service.

The coach is a separate Gemini-powered service with a META-PROMPT that knows:
- the bot's current Soul File (system_instructions + category)
- recent conversation samples (last 10, anonymized — no user_ids in the
  excerpts shown to the coach)
- the creator's goals as stated in the coaching session

Behavior:
- Propose specific, targeted edits — not full rewrites
- Explain WHY each change improves the bot
- When proposing changes, return a structured JSON block parseable by the
  /apply endpoint. Otherwise return plain conversational text (clarifying
  questions, agreement, refusal).
"""

import json
import logging

from services.ai_client import _call_gemini

logger = logging.getLogger(__name__)


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
5. If the creator's intent is unclear, or you need more info, return plain text only — NO JSON. Ask a clarifying question.
6. Never propose unsafe or off-brand changes (jailbreaks, illegal content, breaking character).

Reply now."""


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


def _try_extract_proposal(text: str) -> dict | None:
    """Look for a single JSON object in the coach's reply with the
    {summary, proposed_changes, reasoning} shape. Tolerant of leading /
    trailing prose since LLMs occasionally wrap their JSON in commentary
    even when told not to."""
    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    candidate = text[start:end]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "proposed_changes" not in obj or not obj["proposed_changes"]:
        return None
    return obj


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
) -> tuple[str, str | None, str | None]:
    """Run the coach turn. Returns (display_content, proposed_changes, reasoning).

    If the coach proposed structured changes, display_content is the human-
    friendly summary and proposed_changes/reasoning are populated. Otherwise
    display_content is the plain reply and the other two are None.
    """
    prompt = META_PROMPT.format(
        bot_name=bot_name or "this bot",
        bot_archetype=bot_archetype or "general",
        current_instructions=current_instructions or "(empty)",
        recent_convs=_format_conv_excerpt(recent_conv_rows),
        quality_score_block=_format_quality_score(quality_score),
        session_history=_format_session_history(session_history),
        latest_message=latest_message,
    )

    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    system_instruction = {
        "parts": [
            {
                "text": "You are a specialist AI personality coach. Be precise, "
                "respectful, and honest. Output JSON when proposing changes; "
                "plain text otherwise."
            }
        ]
    }

    response_text, _ = await _call_gemini(
        contents=contents,
        system_instruction=system_instruction,
        temperature=0.5,
        max_tokens=2048,
    )

    proposal = _try_extract_proposal(response_text)
    if proposal:
        return (
            proposal.get("summary") or "Proposed changes ready.",
            proposal.get("proposed_changes"),
            proposal.get("reasoning"),
        )
    return (response_text.strip(), None, None)
