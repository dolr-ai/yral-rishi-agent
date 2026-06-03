"""Phase 23 — Skills Framework V1.

Catalog of specialist skill definitions. Each skill is one Python dict
entry; adding a new vertical (daily_briefing, travel_advisor, etc.) is
one entry — no schema change.

V1 ships with ONE skill (`nutrition_coach`). See
docs/SKILLS-FEATURE-DESIGN.md for the broader vertical roadmap.

Mental model: an influencer is `archetype × skill`. Same skill can
pair with multiple archetypes; same archetype can wear multiple skills.
The catalog declares `compatible_archetypes` so the UI can hint
combinations that work (companion + nutrition_coach is flagged
incompatible because the companion prompt forbids medical advice).

Why a dict instead of a DB table: V1 has one skill maintained in code.
The day creators edit skills via the Soul File Coach UI (Phase 7.5
extension), this converts to a table. Until then, code is the symmetric
home — mirrors `soul_file.py:ARCHETYPE_PROMPTS`.
"""

# All skills follow the same shape. Future additions (daily_briefing,
# travel_advisor, etc.) drop one entry into SKILLS — no other changes.

SKILLS: dict[str, dict] = {
    "nutrition_coach": {
        "display_name": "Nutrition Coach",
        # Skill prompt block. Inserted as Layer 3 in soul_file.compose()
        # — sits AFTER the archetype prompt so its overrides win on
        # conflict (LLMs weight later instructions more heavily). The
        # block can include carve-outs to GLOBAL_RULES (e.g. allow
        # structured lists when delivering meal plans, even though the
        # global rule says "1-3 sentences"). NEVER edit GLOBAL_RULES
        # itself — that risks regressing non-skilled influencers.
        "system_prompt_block": (
            "You are a specialist nutrition coach. Your job is to help the user "
            "reach their nutrition goal through small, sustainable habits — not "
            "extreme rules. Be warm and non-judgmental about slips.\n\n"
            "Skill-specific rules (override the 1-3-sentence global rule when "
            "relevant):\n"
            "- When delivering a meal plan, weekly review, or grocery list, "
            "  structured bullet lists are appropriate even if the message is "
            "  longer than 3 sentences.\n"
            "- During check-ins, stay short: one warm question or nudge, max "
            "  20-25 words.\n"
            "- Never give medical advice. If the user mentions a condition "
            "  (diabetes, allergies, pregnancy, eating disorder), recommend "
            "  they consult a doctor or registered dietitian — gently, once.\n"
            "- Reference the user's primary_goal and preferred_times naturally; "
            "  don't recite them every turn."
        ),
        # First-turn onboarding prompt. The chat orchestrator detects
        # "skill_slug != null AND user_skill_state IS NULL" and replaces
        # the normal chat flow with this prompt for ONE turn.
        # The model emits a hidden <skill_state>{...}</skill_state> block
        # at the end of its reply; the route parses it and writes the
        # user_skill_state row. See "First-turn onboarding" section of
        # docs/SKILLS-FEATURE-DESIGN.md for the parser-hardening
        # requirements (strict parse, partial-extraction OK, one retry,
        # idempotency).
        "onboarding_prompt": (
            "This is the user's FIRST turn with you. Before anything else, "
            "ask them three things in ONE warm message: (1) their nutrition "
            "goal (e.g. lose 5kg, maintain, gain muscle), (2) any dietary "
            "restrictions or preferences (vegetarian, vegan, allergies), "
            "and (3) two preferred check-in times in 24h format (e.g. 13:00 "
            "and 21:00). After they reply, at the very end of your next "
            "message emit a hidden block of the form:\n"
            "<skill_state>{\n"
            '  "setup": {\n'
            '    "primary_goal": "lose 5kg",\n'
            '    "diet_type": "vegetarian",\n'
            '    "preferred_times": ["13:00", "21:00"]\n'
            "  }\n"
            "}</skill_state>\n"
            "with the parsed values. If the user only answered 2 of 3, "
            "emit what you got — partial setup is OK. Do NOT mention the "
            "hidden block to the user; mobile strips it before render."
        ),
        # state_schema is DOCUMENTATION — JSONB stays flexible. Lists
        # the canonical keys the onboarding parser populates (setup) and
        # the keys the proactive/chat loops mutate (runtime). Following
        # the split keeps the JSONB from becoming a junk drawer across
        # the broader skill catalog.
        "state_schema": {
            "setup": [
                "primary_goal",
                "diet_type",
                "target_weight",
                "current_weight",
                "preferred_times",
            ],
            "runtime": [
                "last_missed_checkin_at",
                "last_weekly_summary_at",
                "current_adherence_notes",
            ],
        },
        # Soft compatibility hint — UI uses this to filter the
        # archetype dropdown when a creator assigns a skill. Runtime
        # is NOT enforced (an admin SQL UPDATE can still pair an
        # incompatible archetype + skill; that's by design for
        # debugging / one-off experiments).
        "compatible_archetypes": ["advisor", "educator"],
        # proactive_kind drives which find_due_*() function the
        # engagement loop calls. scheduled_checkin = fires at fixed
        # times. scheduled_briefing = once-per-day digest. event_driven
        # = fires on external trigger (travel dates approaching, etc.).
        # V1 only implements scheduled_checkin.
        "proactive_kind": "scheduled_checkin",
        # Mobile push pipeline already routes by trigger_type. Naming
        # convention: skill_<slug>_<event>.
        "trigger_type": "skill_nutrition_checkin",
        "checkin_prompt": (
            "Generate a short, time-appropriate check-in for this user. "
            "Reference their primary_goal and one of their current "
            "adherence_notes if any. Keep it under 25 words. End with a "
            "question that invites a one-line reply."
        ),
        # Default cadence between check-ins. preferred_times in
        # user_skill_state overrides this for scheduled_checkin skills.
        "default_cadence_hours": 6,
        # Future-search hook (Phase 15). nutrition_coach doesn't need
        # web search; daily_briefing will. The flag is here so the
        # search-enabled phase can filter SKILLS by it without a
        # schema change.
        "requires_search": False,
    },
}


def get(slug: str) -> dict | None:
    """Return the skill definition for `slug`, or None if unknown.

    Centralized so the catalog can later swap from dict-lookup to a
    DB-backed read (Phase 7.5 extension) without changing call sites."""
    return SKILLS.get(slug)


def is_archetype_compatible(skill_slug: str, archetype: str | None) -> bool:
    """Soft compatibility check used by the influencer-create UI.
    Returns True if the skill declares the archetype as compatible,
    False otherwise (including if either side is unknown). Runtime
    enforcement is intentionally absent — see SKILLS["nutrition_coach"]
    docstring for why."""
    skill = SKILLS.get(skill_slug)
    if not skill:
        return False
    return (archetype or "").lower().strip() in skill.get("compatible_archetypes", [])
