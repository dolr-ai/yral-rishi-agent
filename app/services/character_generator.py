import json
import logging
from typing import Optional

import config
from services import replicate
from services import llm_registry

logger = logging.getLogger(__name__)


class GeminiSafetyBlocked(Exception):
    pass


def _is_safety_block(exc: BaseException) -> bool:
    msg = str(exc)
    return (
        "blockReason=" in msg
        or "no candidates" in msg.lower()
        or "finishReason=SAFETY" in msg
    )


GENERATE_PROMPT = """You are an expert AI Character Architect. Transform the user's concept into high-fidelity System Instructions.

Structure the response using these sections:

1. [CORE IDENTITY]: Name, species, and background.
2. [LINGUISTIC STYLE]:
   - LANGUAGE SHIFTING: You must mirror the user's language and script exactly. If they code-switch between languages (e.g., Hinglish, Spanglish, Singlish, Arabish, Taglish), mirror the mix.
   - DIALECT: Use colloquial slang appropriate to the character's region/setting if the persona is casual (e.g., 'yaar' for an Indian persona, 'mate' for British, 'parça' for Brazilian Portuguese). Pick what fits the character — do not default to any one region.
   - TONE: Define the sentence rhythm (e.g., fast-paced, poetic, or respectful/formal).
3. [BEHAVIOR & RP]:
   - Do not use 'show, don't tell' by including physical actions in asterisks (e.g., smiles warmly).
   - Stay in-universe; never mention being an AI or a bot.
4. [MOBILE OPTIMIZATION]:
   - RESPONSE LENGTH: Keep replies 'Bite-Sized'. Aim for max 1-2 sentences per response.
   - Use paragraph breaks for readability on small screens.

STRICTURES:
- Written in Second Person ("You are...").
- Max 500 words total for these instructions.
- Ensure the character feels authentic and culturally grounded."""


VALIDATE_PROMPT = """You are a character validator. Analyze the given system instructions and generate metadata.

Rules:
- The character MUST NOT be sexually explicit or NSFW
- The character must be safe for all ages
- Generate a URL-friendly name (3-12 lowercase alphanumeric characters only)
- Generate a display name (human-readable)
- Generate a one-line description
- Generate an initial greeting message (match the character's linguistic profile — could be code-switched, regional, or single-language depending on the character)
- Generate 3-4 suggested starter messages (match the character's linguistic profile)
- Generate personality traits as key-value pairs
- Suggest a category
- Generate an image prompt for avatar creation

Return a JSON object with this exact schema:
{
  "is_valid": true/false,
  "reason": "reason if invalid, null if valid",
  "name": "urlslug",
  "display_name": "Display Name",
  "description": "One line description",
  "initial_greeting": "Hi! I'm...",
  "suggested_messages": ["msg1", "msg2", "msg3"],
  "personality_traits": {"energy_level": "high", "demeanor": "calm"},
  "category": "entertainment",
  "image_prompt": "portrait of..."
}"""


GREETING_PROMPT = """You are a Character Specialist. Based on the provided System Instructions, generate a high-engagement initial greeting and 4 starter messages.

Rules for the Initial Greeting:
1. [MIRROR LANGUAGE]: If the character's style includes a regional dialect, code-switching, or slang, the greeting MUST use it naturally.
2. [MOBILE-FIRST]: Keep the greeting under 20 words so it isn't cut off in chat previews.
3. [ACTIONABLE]: It should end with a question or a 'hook' that makes the user want to reply.
4. [RP ELEMENTS]: Include a small physical action in asterisks (e.g., waves, adjusts collar).

Rules for Starter Messages:
1. Provide 4 distinct options ranging from casual to deep/thematic.
2. Match the character's linguistic profile — use the dialect, code-switching pattern, or single-language style that the character's system instructions establish.

Character Name: {display_name}
System Instructions: {system_instructions}

Return a JSON object:
{{
  "initial_greeting": "Short, catchy greeting with physical action and language mirroring.",
  "suggested_messages": [
    "Message 1 (Casual/Daily)",
    "Message 2 (Problem/Conflict)",
    "Message 3 (Deep/Emotional)",
    "Message 4 (Playful/Banter)"
  ]
}}"""


VIDEO_PROMPT = """You are a Cinematic Director and LTX Prompt Engineer.
Based on the character's System Instructions, write a high-impact, single-flowing paragraph (4-8 sentences) for a 5-second video.

Follow these LTX Prompting Guide rules:
1. [ESTABLISH THE SHOT]: Start with the shot scale (e.g., Close-up, Medium shot) and the setting.
2. [SET THE SCENE]: Describe specific lighting, textures, and the atmosphere.
3. [CHARACTER & ACTION]: Describe the character's physical features and their core action in the present tense.
4. [CAMERA MOVEMENT]: Explicitly state how the camera moves.
5. [AUDIO & DIALOGUE]: Include ambient sounds and one short line of spoken dialogue.

Character: {display_name}
System Instructions: {system_instructions}

Return ONLY the flowing paragraph prompt. Do not use bullet points or labels."""


_PERMISSIVE_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
]

SAFETY_REFUSAL_PHRASES = [
    "i cannot create",
    "i can't create",
    "sexually suggestive",
    "inappropriate",
    "i cannot generate",
    "i can't generate",
    "not appropriate",
    "violates",
    "harmful",
]


def contains_safety_refusal(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in SAFETY_REFUSAL_PHRASES)


async def generate_system_instructions(concept: str) -> str | None:
    if not config.GEMINI_API_KEY:
        return None
    try:
        response = await llm_registry.call(
            process="character_generator",
            messages=[
                {"role": "system", "content": GENERATE_PROMPT},
                {"role": "user", "content": concept},
            ],
            temperature=config.GEMINI_TEMPERATURE,
            max_tokens=config.GEMINI_MAX_TOKENS,
            extra_body={"safetySettings": _PERMISSIVE_SAFETY_SETTINGS},
        )
        text = response.content
        if contains_safety_refusal(text):
            return None
        return text.strip()
    except ValueError as e:
        if _is_safety_block(e):
            raise GeminiSafetyBlocked(
                "Your concept was flagged as inappropriate. "
                "Try rewording without explicit, violent, or harmful themes."
            ) from e
        logger.exception("Failed to generate system instructions")
        return None
    except Exception:
        logger.exception("Failed to generate system instructions")
        return None


async def _generate_avatar(image_prompt: Optional[str]) -> Optional[str]:
    if not image_prompt:
        return None
    try:
        enhanced = f"Professional avatar portrait, high quality, {image_prompt}"
        return await replicate.generate_image(enhanced, aspect_ratio="1:1")
    except Exception:
        logger.exception("Avatar generation failed")
        return None


async def validate_and_generate_metadata(system_instructions: str) -> dict | None:
    if contains_safety_refusal(system_instructions):
        return {"is_valid": False, "reason": "Content was flagged as inappropriate"}

    if not config.GEMINI_API_KEY:
        return None

    try:
        response = await llm_registry.call(
            process="character_generator",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that returns valid JSON.",
                },
                {
                    "role": "user",
                    "content": f"{VALIDATE_PROMPT}\n\nSystem Instructions:\n{system_instructions}",
                },
            ],
            temperature=0.3,
            max_tokens=config.GEMINI_MAX_TOKENS,
            extra_body={"safetySettings": _PERMISSIVE_SAFETY_SETTINGS},
        )
        text = response.content
        if contains_safety_refusal(text):
            return {"is_valid": False, "reason": "Content was flagged as inappropriate"}

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            metadata = json.loads(text[start:end])
            metadata["avatar_url"] = await _generate_avatar(
                metadata.get("image_prompt")
            )
            return metadata
        return None
    except ValueError as e:
        if _is_safety_block(e):
            return {
                "is_valid": False,
                "reason": "Your concept was flagged as inappropriate.",
            }
        logger.exception("Failed to validate and generate metadata")
        return None
    except Exception:
        logger.exception("Failed to validate and generate metadata")
        return None


async def generate_initial_greeting(
    display_name: str,
    system_instructions: str,
) -> tuple[str, list[str]]:
    fallback_greeting = f"Hey! I'm {display_name}! How can I help you today?"
    fallback_suggestions = []

    if not config.GEMINI_API_KEY:
        return (fallback_greeting, fallback_suggestions)

    try:
        prompt = GREETING_PROMPT.format(
            display_name=display_name,
            system_instructions=system_instructions,
        )
        response = await llm_registry.call(
            process="character_generator",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that returns valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=config.GEMINI_MAX_TOKENS,
            extra_body={"safetySettings": _PERMISSIVE_SAFETY_SETTINGS},
        )
        text = response.content
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            return (
                data.get("initial_greeting", fallback_greeting),
                data.get("suggested_messages", fallback_suggestions),
            )
        return (fallback_greeting, fallback_suggestions)
    except Exception:
        logger.exception("Failed to generate greeting")
        return (fallback_greeting, fallback_suggestions)


async def generate_video_prompt(
    display_name: str,
    system_instructions: str,
) -> str | None:
    if not config.GEMINI_API_KEY:
        return None
    try:
        prompt = VIDEO_PROMPT.format(
            display_name=display_name,
            system_instructions=system_instructions,
        )
        response = await llm_registry.call(
            process="character_generator",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=config.GEMINI_MAX_TOKENS,
            extra_body={"safetySettings": _PERMISSIVE_SAFETY_SETTINGS},
        )
        text = response.content
        return text.strip() if text else None
    except Exception:
        logger.exception("Failed to generate video prompt")
        return None
