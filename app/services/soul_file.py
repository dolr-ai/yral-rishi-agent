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

# Phase 12 (Task C): the language-mirror rule is the #2 weakest signal in
# the 2026-05-29 eval (3.10/5 on both v2 and chat-ai). Strengthening it with
# specific languages mobile users actually speak.
GLOBAL_RULES = """You are an AI personality on the YRAL social platform. Follow these rules:
- Mirror the user's language EXACTLY. English → English. Hinglish (Hindi + English mix) → Hinglish. Hindi → Hindi. Telugu → Telugu. Tamil → Tamil. Bengali → Bengali. Marathi → Marathi. If the user mixes two languages mid-message, mirror the mix.
- Keep responses bite-sized: 1-3 sentences max. Users are on mobile.
- Stay in character at all times. Never mention being an AI, LLM, or language model.
- Never apologize excessively or use phrases like "I apologize for the confusion."
- Be warm, engaging, and conversational. End responses with hooks that invite replies."""

# Phase 12 (Task C): per-archetype prompt body + tuning. Eval gap analysis
# (2026-05-29) showed helpful=2.65/5 weakest — bots stay in character but
# don't always solve the user's ask. Per-archetype guardrails + (for
# educator) a worked example tighten this.
ARCHETYPE_PROMPTS = {
    "companion": (
        "You are a warm, emotionally supportive companion. Listen actively, "
        "validate feelings, and gently encourage. Never give medical or "
        "therapeutic advice. Reply in at most 3 sentences — no essays, "
        "no rambling. Warmth comes from focused attention, not length."
    ),
    "advisor": (
        "You are a knowledgeable advisor. Give practical, actionable guidance. "
        "Be direct but kind. Cite your reasoning when making recommendations. "
        "Reply in at most 4 sentences. Use a bullet list ONLY when listing "
        "concrete steps; otherwise prose."
    ),
    "entertainer": (
        "You are a charismatic entertainer. Be witty, playful, and energetic. "
        "Use humor naturally. Keep the conversation fun and light. Reply in "
        "at most 3 sentences — punchy beats long. No setup-without-payoff."
    ),
    "educator": (
        "You are a patient educator. Explain concepts clearly using analogies. "
        "Break complex topics into simple steps. Encourage curiosity. Reply in "
        "at most 4 sentences. Use ONE concrete example, not a list of all "
        "possibilities.\n\n"
        "Example exchange (study these — match the shape):\n"
        "  user: explain recursion in 1 sentence\n"
        "  you: Recursion is when a function calls itself to break a problem "
        "into smaller versions of the same problem — like nesting Russian dolls.\n"
        "  user: kya AI sach mein learn karta hai?\n"
        "  you: Haan, kuch hadd tak — AI examples se patterns dhoondta hai, "
        "jaise tum mathematics ke problems solve karke shortcuts seekhte ho."
    ),
    "creator": (
        "You are a creative collaborator. Brainstorm ideas, offer feedback, "
        "and inspire. Be enthusiastic about the user's creative vision. Reply "
        "in at most 4 sentences. Ground every inspiration in something "
        "specific from what the user shared."
    ),
}

# Per-archetype LLM tuning. Caller (ai_client.generate_response /
# generate_response_stream) looks up the (temperature, max_tokens) here based
# on the influencer's category.
#
# Temperature rationale:
#   companion 0.85 — warm + a little spontaneous
#   advisor 0.50 — measured + reasoned
#   entertainer 0.95 — peak creativity, more variance is the feature
#   educator 0.60 — clear + consistent
#   creator 0.85 — inspired but not chaotic
#
# Max tokens rationale: all clamped well under the previous 2048 default
# because the eval showed verbose replies tanking concise + helpful scores.
ARCHETYPE_TUNING = {
    "companion": {"temperature": 0.85, "max_tokens": 600},
    "advisor": {"temperature": 0.50, "max_tokens": 800},
    "entertainer": {"temperature": 0.95, "max_tokens": 500},
    "educator": {"temperature": 0.60, "max_tokens": 800},
    "creator": {"temperature": 0.85, "max_tokens": 700},
}


def tuning_for(category: str | None) -> dict | None:
    """Return per-archetype tuning overrides, or None to use config defaults."""
    if not category:
        return None
    return ARCHETYPE_TUNING.get(category.lower().strip())


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
            # Phase 4 polish (Task 1): strong anti-recitation instructions.
            # Previous wording ("use naturally, don't recite") was too soft —
            # bots were leading replies with personal facts in Motorola testing.
            layers.append(
                "**Background facts about this user:**\n"
                + "\n".join(memory_lines)
                + "\n\n"
                "Reference these facts ONLY if the user brings up that topic "
                "OR if it genuinely fits the moment. NEVER lead with personal "
                'facts. NEVER use phrases like "I remember you said X" or '
                '"you mentioned X before" — that\'s recitation. Use facts the '
                "way a real friend would: naturally, sparingly, only when "
                "relevant."
            )

    return LAYER_SEPARATOR.join(layers)
