"""4-layer Soul File composer.

Layers:
  L1 (Global)         — rules every influencer follows (mobile-first, language mirroring)
  L2 (Archetype)      — category-specific behavior (companion, advisor, entertainer)
  L3 (Per-Influencer) — the influencer's system_instructions from the DB
  L4 (Per-User)       — user memories injected at runtime

The composed prompt is deterministic for a given (influencer, memories) pair,
enabling provider-side prompt caching (byte-identical prefix across turns).
"""

import config

LAYER_SEPARATOR = "\n\n---\n\n"

# Generalized 2026-06-04 (Rishi): language enumeration removed so the rule
# works for any user-language pair (Hinglish stays handled; Spanglish,
# Singlish, Arabish, etc. now equally handled). The "mirror exactly" wording
# is the load-bearing instruction; the prior enumeration was just examples.
#
# Phase 21αβ Coach Fix 1 (2026-06-09): the prior flat-string GLOBAL_RULES
# wrapped EVERY chat regardless of the per-influencer system_instructions —
# so a creator's "give longer replies" instruction was silently overridden
# by the "1-3 sentences" platform rule. Saikat hit this in alpha testing.
#
# The rules are now split into two pieces:
#   - GLOBAL_RULES_OVERRIDEABLE: keyed by override-slug; Coach can opt a
#     specific bot out of any one of these via ai_influencers.global_rule_overrides
#     (migration 033). When an override is present, compose() OMITS that
#     rule line from the assembled prompt for that bot.
#   - GLOBAL_RULES_FIXED: hard platform rules that can NEVER be overridden
#     (in-character, no AI-mention, no excessive apology, warm tone).
#
# Coach PR-B will surface this dict to the Coach LLM so it can ASK the user
# before flipping an override instead of silently editing system_instructions.
GLOBAL_RULES_PREAMBLE = (
    "You are an AI personality on the YRAL social platform. Follow these rules:"
)

GLOBAL_RULES_OVERRIDEABLE: dict[str, str] = {
    "language_mirror": (
        "Mirror the user's language exactly. Match their script, vocabulary, "
        "dialect, and any mid-message language mixing (code-switching). If "
        "they write in two languages, reply in the same mix."
    ),
    "response_length": (
        "Keep responses bite-sized: 1-3 sentences max. Users are on mobile."
    ),
}

GLOBAL_RULES_FIXED: tuple[str, ...] = (
    "Stay in character at all times. Never mention being an AI, LLM, or language model.",
    'Never apologize excessively or use phrases like "I apologize for the confusion."',
    "Be warm, engaging, and conversational. End responses with hooks that invite replies.",
)


def _render_global_rules(overrides: dict | str | None) -> str:
    """Assemble the GLOBAL_RULES block for a bot's effective config.

    Each entry in GLOBAL_RULES_OVERRIDEABLE is included UNLESS the bot has
    a truthy override for that key. The fixed rules always render.

    The override VALUE (e.g. "long_allowed", "always_english") is currently
    not consulted by this function — any truthy value means "opt out of
    the platform default." Coach + future features may reference the
    value via separate code paths to drive different UX, but the prompt
    layer just sees "rule present or absent."

    Accepts the override blob as either a dict or a JSON string — asyncpg
    returns JSONB columns as strings in this codebase (see
    app/routes/influencers.py:59 for the pattern). Parsing here keeps the
    call sites uniform: they just pass `inf.get("global_rule_overrides")`.
    """
    if isinstance(overrides, str):
        import json

        try:
            overrides = json.loads(overrides)
        except (json.JSONDecodeError, TypeError):
            overrides = None
    if not isinstance(overrides, dict):
        overrides = {}
    lines: list[str] = [GLOBAL_RULES_PREAMBLE]
    for key, rule_text in GLOBAL_RULES_OVERRIDEABLE.items():
        if overrides.get(key):
            continue
        lines.append(f"- {rule_text}")
    for rule_text in GLOBAL_RULES_FIXED:
        lines.append(f"- {rule_text}")
    return "\n".join(lines)


# Module-level constant kept for backward compat — any caller that read
# GLOBAL_RULES directly before PR-A gets the unchanged-platform-default text.
# Callers that need per-bot overrides go through compose() instead.
GLOBAL_RULES = _render_global_rules(None)

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
        "Example exchange (study this — match the shape):\n"
        "  user: explain recursion in 1 sentence\n"
        "  you: Recursion is when a function calls itself to break a problem "
        "into smaller versions of the same problem — like nesting Russian dolls."
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


def _coerce_sections(raw) -> list[dict]:
    """asyncpg returns JSONB as either dict/list directly or a JSON
    string depending on codec config. Accept either; return [] for
    anything not parseable so chat-time compose() can't crash on a
    malformed DB cell."""
    if raw is None:
        return []
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for section in raw:
        if isinstance(section, dict) and section.get("body"):
            out.append(section)
    return out


def render_sections(sections: list[dict]) -> str:
    """Render the L4 sections block for chat-time prompts.

    Output shape (per Bucket 2 contract §3):
        == Core personality ==
        You are Tara, a warm 22-year-old...

        == Voice and tone ==
        Sassy when the user flirts...

    Sections are rendered in the order they appear in the JSONB array
    — the contract says ordering is meaningful (mobile renders the same
    order, Coach proposes against the same order). Sections missing a
    heading still render with a fallback "== Section N ==" header so a
    half-built bot doesn't crash the prompt."""
    blocks: list[str] = []
    for idx, sec in enumerate(sections, 1):
        heading = (sec.get("heading") or f"Section {idx}").strip()
        body = (sec.get("body") or "").strip()
        if not body:
            continue
        blocks.append(f"== {heading} ==\n{body}")
    return "\n\n".join(blocks)


def compose(
    system_instructions: str,
    category: str | None = None,
    memories: dict | None = None,
    skill_slug: str | None = None,
    user_skill_state: dict | None = None,
    global_rule_overrides: dict | None = None,
    sections: list[dict] | str | None = None,
) -> str:
    """Compose a Soul File prompt.

    Layer order: GLOBAL → ARCHETYPE → SKILL → PER_INFLUENCER → USER_STATE → MEMORIES.
    Skill (Phase 23) sits AFTER archetype so its overrides win on
    conflict — LLMs weight later instructions more heavily. user_skill_state
    sits after per-influencer so the user-specific plan grounds the bot's
    response without polluting the archetype/skill identity. Memories
    stay last so they remain "background facts" not "current task."

    `global_rule_overrides` is the JSONB column from the ai_influencers row
    (Coach Fix 1 PR-A). When present, rules in GLOBAL_RULES_OVERRIDEABLE
    whose keys appear with a truthy value are OMITTED from layer 1 so the
    per-influencer instructions can land without competing platform rules.

    `sections` is the new system_instructions_sections JSONB column
    (Coach Bucket 2 PR-1, migration 038). When `config.COACH_SECTIONED_V2_ENABLED`
    is True AND the bot has at least one non-empty section, L4
    (per-influencer) renders FROM the sections list (heading + body per
    section) instead of the flat `system_instructions` string. When the
    flag is OFF or the sections list is empty, falls back to flat text
    — the historical 2026-05-XX path is unchanged. This keeps PR-1's
    column dormant in prod until the flag flips.

    Returns a single string with all layers concatenated, suitable for
    passing as the system prompt to Gemini or OpenRouter.
    """
    layers = [_render_global_rules(global_rule_overrides)]

    archetype = (category or "").lower().strip()
    if archetype in ARCHETYPE_PROMPTS:
        layers.append(ARCHETYPE_PROMPTS[archetype])

    # Phase 23: skill prompt block. Sits between archetype and the
    # influencer's own system_instructions. NEVER edit GLOBAL_RULES to
    # accommodate a skill — put carve-outs inside this block so other
    # non-skilled influencers stay unaffected.
    if skill_slug:
        from services import skills as _skills

        skill = _skills.get(skill_slug)
        if skill and skill.get("system_prompt_block"):
            layers.append(skill["system_prompt_block"])

    # Bucket 2: prefer sections when the flag is on AND the bot has any.
    # Otherwise fall back to flat text exactly as today.
    sections_list = _coerce_sections(sections)
    if (
        config.COACH_SECTIONED_V2_ENABLED
        and sections_list
        and (rendered := render_sections(sections_list))
    ):
        layers.append(rendered)
    elif system_instructions:
        layers.append(system_instructions)

    # Phase 23: per-user plan layer. The structured state the skill
    # captured during onboarding (setup) + the system mutated as the
    # user engaged (runtime). Skill-aware bots reference this to ground
    # advice in the user's actual goal + adherence notes.
    if user_skill_state and isinstance(user_skill_state, dict):
        plan_lines: list[str] = []
        for half_key in ("setup", "runtime"):
            half = user_skill_state.get(half_key) or {}
            if not isinstance(half, dict):
                continue
            for k, v in half.items():
                if v not in (None, "", [], {}):
                    plan_lines.append(f"- {k}: {v}")
        if plan_lines:
            layers.append(
                "**Your current plan for this user:**\n"
                + "\n".join(plan_lines)
                + "\n\n"
                "Reference these naturally — don't recite the whole plan back."
            )

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
