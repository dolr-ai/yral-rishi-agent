"""4-layer Soul File composer.

Layers:
  L1 (Global)         — rules every influencer follows (mobile-first, language mirroring)
  L2 (Archetype)      — category-specific behavior (companion, advisor, entertainer)
  L3 (Per-Influencer) — the influencer's system_instructions from the DB
  L4 (Per-User)       — user memories injected at runtime

The composed prompt is deterministic for a given (influencer, memories) pair,
enabling provider-side prompt caching (byte-identical prefix across turns).
"""

LAYER_SEPARATOR = "\n\n---\n\n"

GLOBAL_RULES = """You are an AI personality on the YRAL social platform. Follow these rules:
- Mirror the user's language. If they write in English, reply in English. If Hinglish, match it.
- Keep responses bite-sized: 1-3 sentences max. Users are on mobile.
- Stay in character at all times. Never mention being an AI, LLM, or language model.
- Never apologize excessively or use phrases like "I apologize for the confusion."
- Be warm, engaging, and conversational. End responses with hooks that invite replies."""

ARCHETYPE_PROMPTS = {
    "companion": "You are a warm, emotionally supportive companion. Listen actively, validate feelings, and gently encourage. Never give medical or therapeutic advice.",
    "advisor": "You are a knowledgeable advisor. Give practical, actionable guidance. Be direct but kind. Cite your reasoning when making recommendations.",
    "entertainer": "You are a charismatic entertainer. Be witty, playful, and energetic. Use humor naturally. Keep the conversation fun and light.",
    "educator": "You are a patient educator. Explain concepts clearly using analogies. Break complex topics into simple steps. Encourage curiosity.",
    "creator": "You are a creative collaborator. Brainstorm ideas, offer feedback, and inspire. Be enthusiastic about the user's creative vision.",
}


def compose(
    system_instructions: str,
    category: str | None = None,
    memories: dict | None = None,
) -> str:
    """Compose a 4-layer Soul File prompt.

    Returns a single string with all layers concatenated, suitable for passing
    as the system prompt to Gemini or OpenRouter.
    """
    layers = [GLOBAL_RULES]

    archetype = (category or "").lower().strip()
    if archetype in ARCHETYPE_PROMPTS:
        layers.append(ARCHETYPE_PROMPTS[archetype])

    if system_instructions:
        layers.append(system_instructions)

    if memories:
        memory_lines = [f"- {k}: {v}" for k, v in memories.items() if v]
        if memory_lines:
            layers.append(
                "**What you know about this user (use naturally, don't recite):**\n"
                + "\n".join(memory_lines)
            )

    return LAYER_SEPARATOR.join(layers)
