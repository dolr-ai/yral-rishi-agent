"""Phase 7.9 — 5-minute bot creation wizard.

Three LLM-driven steps:
1. INTAKE — given a creator's 1-2 sentence concept, generate 3-5 targeted
   intake questions tailored to that concept.
2. DRAFT — once all questions are answered, generate a polished Soul File
   draft (system_instructions + display_name + category + initial_greeting).
3. PREVIEW — generate a sample 5-turn conversation between the draft bot
   and a synthetic test user so the creator can see real output before
   committing.

Each LLM call returns structured JSON. The parser is tolerant of wrapping
prose because Gemini occasionally violates the JSON-only instruction.
"""

import json
import logging

from services import llm_registry
from services.ai_client import generate_response

logger = logging.getLogger(__name__)


INTAKE_PROMPT = """You are an expert AI personality coach helping a creator build a new bot. The creator just told you:

\"{concept}\"

Generate 3-5 intake questions tailored to THIS concept. Each question should pull out a SPECIFIC piece of personality, backstory, voice, or do/don't that will make the bot distinctive. Cover at least:
- Personality archetype (companion / advisor / entertainer / educator / creator)
- 1-2 unique backstory hooks that distinguish this bot from generic ones
- Conversation style (formal / casual / code-switched / single-language / regional dialect — whatever fits this character)
- One concrete thing the bot WOULD say
- One thing the bot WOULDN'T say
- The opening message users see on first chat

Skip questions whose answer is obvious from the concept itself.

Return ONLY a JSON array of objects with EXACTLY this shape:
[
  {{"key": "snake_case_short_id", "question": "the question text", "rationale": "1-line why this matters for this bot"}},
  ...
]

3-5 items, no more, no fewer."""


DRAFT_PROMPT = """You are an expert AI personality coach. A creator has answered intake questions about a new bot they want to build. Use their answers to write a polished Soul File.

Concept: \"{concept}\"

Intake answers:
{answers_block}

Your job:
1. Pick the best-fitting archetype (companion / advisor / entertainer / educator / creator).
2. Write a 4-8 sentence system_instructions that captures personality, voice, do's and don'ts. Concrete, not generic. Mobile-friendly (the bot should reply in 1-3 sentences).
3. Pick a display_name (≤ 30 chars; the creator can rename later).
4. Write an initial_greeting (≤ 200 chars; first thing users see).

Return ONLY a JSON object with EXACTLY this shape:
{{
  "system_instructions": "...",
  "display_name": "...",
  "category": "companion | advisor | entertainer | educator | creator",
  "initial_greeting": "..."
}}"""


PREVIEW_PROMPT = """You are simulating a 5-turn conversation between a NEW bot and a curious user, so the bot's CREATOR can see how it'll behave.

The bot:
- display_name: {display_name}
- category: {category}
- system_instructions:
\"\"\"
{system_instructions}
\"\"\"

Simulate a realistic exchange — the user asks something open-ended, the bot replies in character, etc. 5 user turns total, each followed by a bot reply.

Return ONLY a JSON array of EXACTLY 10 message objects (user, bot, user, bot, ...) with EXACTLY this shape:
[
  {{"role": "user", "content": "..."}},
  {{"role": "bot", "content": "..."}},
  ... (10 total)
]"""


def _extract_json(text: str, expect_list: bool) -> object | None:
    """Pull the JSON array (or object) from `text`, tolerating wrapping prose."""
    if expect_list:
        start, end = text.find("["), text.rfind("]") + 1
    else:
        start, end = text.find("{"), text.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


async def generate_intake_questions(concept: str) -> list[dict]:
    """Step 1: return 3-5 {key, question, rationale} dicts.

    Returns [] on parse failure — the route should fall back to a fixed
    intake list in that case.
    """
    prompt = INTAKE_PROMPT.format(concept=concept)
    try:
        response = await llm_registry.call(
            process="ai_influencer_wizard_simulation",
            messages=[
                {
                    "role": "system",
                    "content": "Return only valid JSON. No markdown fences.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
            max_tokens=2048,
        )
        text = response.content
    except Exception as e:
        logger.warning(f"wizard.generate_intake_questions failed: {e}")
        return []

    parsed = _extract_json(text, expect_list=True)
    if not isinstance(parsed, list):
        return []
    out = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        question = item.get("question")
        if not isinstance(key, str) or not isinstance(question, str):
            continue
        out.append(
            {
                "key": key.strip(),
                "question": question.strip(),
                "rationale": (item.get("rationale") or "").strip(),
            }
        )
        if len(out) >= 5:
            break
    return out


async def generate_draft(concept: str, answers: dict) -> dict | None:
    """Step 2: given concept + creator answers, return a draft Soul File.

    Returns dict with keys {system_instructions, display_name, category,
    initial_greeting} or None on failure.
    """
    answers_block = "\n".join(f"  - {k}: {v}" for k, v in (answers or {}).items())
    if not answers_block:
        answers_block = "(no answers — fall back to the concept alone)"
    prompt = DRAFT_PROMPT.format(concept=concept, answers_block=answers_block)
    try:
        response = await llm_registry.call(
            process="ai_influencer_wizard_simulation",
            messages=[
                {
                    "role": "system",
                    "content": "Return only valid JSON. No markdown fences.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=2048,
        )
        text = response.content
    except Exception as e:
        logger.warning(f"wizard.generate_draft failed: {e}")
        return None

    parsed = _extract_json(text, expect_list=False)
    if not isinstance(parsed, dict):
        return None
    required = ("system_instructions", "display_name", "category", "initial_greeting")
    for k in required:
        if not isinstance(parsed.get(k), str) or not parsed[k].strip():
            return None
    cat = parsed["category"].strip().lower()
    if cat not in {"companion", "advisor", "entertainer", "educator", "creator"}:
        cat = "companion"
    return {
        "system_instructions": parsed["system_instructions"].strip(),
        "display_name": parsed["display_name"].strip()[:30],
        "category": cat,
        "initial_greeting": parsed["initial_greeting"].strip()[:200],
    }


async def generate_preview(
    display_name: str,
    category: str,
    system_instructions: str,
) -> list[dict]:
    """Step 3: synthesize a 5-turn sample conversation. Returns 10 messages
    (user, bot, user, bot, …) or [] on failure."""
    prompt = PREVIEW_PROMPT.format(
        display_name=display_name,
        category=category,
        system_instructions=system_instructions,
    )
    # The simulation runs through generate_response so it goes through the
    # same archetype tuning + soul file composition the production chat does.
    try:
        result = await generate_response(
            system_instructions=(
                "You are simulating a chat. Output JSON ONLY. No commentary."
            ),
            conversation_history=[],
            user_message=prompt,
            is_nsfw=False,
            archetype=category,
        )
    except Exception as e:
        logger.warning(f"wizard.generate_preview failed: {e}")
        return []
    if getattr(result, "is_fallback", False):
        return []

    parsed = _extract_json(result.content, expect_list=True)
    if not isinstance(parsed, list):
        return []
    out = []
    for m in parsed:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "bot") or not isinstance(content, str):
            continue
        out.append({"role": role, "content": content.strip()})
        if len(out) >= 10:
            break
    return out
