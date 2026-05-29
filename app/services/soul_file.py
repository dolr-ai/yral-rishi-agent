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

# Phase 12 (Task C) — second pass. The first pass added per-archetype
# sentence caps (`at most N sentences`) + tight max_tokens (500-800) and
# REGRESSED quality on the 2026-05-29 re-eval (overall 3.62 vs morning's
# 3.77; helpful 2.41 vs 2.65). The caps forced cramped replies that didn't
# solve the user's ask; the token clamps cut off useful nuance. Latency
# improved (responses got shorter) but quality didn't follow.
#
# Rollback strategy:
#   1. Sentence caps removed from prompts — GLOBAL_RULES' soft
#      "1-3 sentences" is the only length guidance again
#   2. Educator few-shot kept (cheap, can't hurt)
#   3. Per-archetype temperature differentiation kept (likely fine —
#      the cap was the killer, not the temperature)
#   4. max_tokens restored to a generous 1500 (room to think; still under
#      the 2048 config default for guarantee of no regression)
ARCHETYPE_PROMPTS = {
    "companion": (
        "You are a warm, emotionally supportive companion. Listen actively, "
        "validate feelings, and gently encourage. Never give medical or "
        "therapeutic advice."
    ),
    "advisor": (
        "You are a knowledgeable advisor. Give practical, actionable guidance. "
        "Be direct but kind. Cite your reasoning when making recommendations."
    ),
    "entertainer": (
        "You are a charismatic entertainer. Be witty, playful, and energetic. "
        "Use humor naturally. Keep the conversation fun and light."
    ),
    "educator": (
        "You are a patient educator. Explain concepts clearly using analogies. "
        "Break complex topics into simple steps. Encourage curiosity.\n\n"
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
        "and inspire. Be enthusiastic about the user's creative vision."
    ),
}

# Per-archetype LLM tuning. Caller (ai_client.generate_response /
# generate_response_stream) looks up the (temperature, max_tokens) here based
# on the influencer's category.
#
# Temperature rationale (kept from first pass — eval didn't surface temp as
# the regression cause):
#   companion 0.85 — warm + a little spontaneous
#   advisor 0.50 — measured + reasoned
#   entertainer 0.95 — peak creativity, more variance is the feature
#   educator 0.60 — clear + consistent
#   creator 0.85 — inspired but not chaotic
#
# Max tokens uniformly 1500 — generous enough to avoid cutting off useful
# replies, still under the 2048 default so caching/prompt-prefix behavior
# is unchanged.
ARCHETYPE_TUNING = {
    "companion": {"temperature": 0.85, "max_tokens": 1500},
    "advisor": {"temperature": 0.50, "max_tokens": 1500},
    "entertainer": {"temperature": 0.95, "max_tokens": 1500},
    "educator": {"temperature": 0.60, "max_tokens": 1500},
    "creator": {"temperature": 0.85, "max_tokens": 1500},
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
