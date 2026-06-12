"""Phase 23.1-23.4 — Skills foundation source-pin tests.

Migrations 029 + 030, skills catalog, soul_file skill layer,
skill_state_repo. Live integration verified separately after deploy.
"""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


# ─── migrations ───────────────────────────────────────────────────────────


def test_migration_029_creates_user_skill_state_table():
    src = _read("migrations/029_user_skill_state.sql")
    assert "CREATE TABLE IF NOT EXISTS user_skill_state" in src
    for col in (
        "user_id",
        "influencer_id",
        "skill_slug",
        "state",
        "next_event_at",
        "last_event_at",
        "status",
    ):
        assert col in src, f"migration 029 missing column {col!r}"
    # UNIQUE constraint per design (one row per user+influencer)
    assert "UNIQUE (user_id, influencer_id)" in src


def test_migration_029_indexes_match_hot_queries():
    src = _read("migrations/029_user_skill_state.sql")
    # Proactive loop's due-event scan
    assert "idx_user_skill_state_due" in src
    assert "WHERE status = 'active'" in src and "next_event_at IS NOT NULL" in src


def test_migration_029_documents_rule_9():
    src = _read("migrations/029_user_skill_state.sql")
    assert "pg_dump" in src
    assert "Rule 9" in src


def test_migration_030_adds_skill_slug_to_ai_influencers():
    src = _read("migrations/030_ai_influencers_skill_slug.sql")
    assert "ALTER TABLE ai_influencers" in src
    assert "ADD COLUMN IF NOT EXISTS skill_slug TEXT" in src


# ─── skills catalog ───────────────────────────────────────────────────────


def test_skills_catalog_ships_nutrition_coach():
    src = _read("app/services/skills.py")
    assert '"nutrition_coach":' in src
    # All required keys per design doc
    for key in (
        "display_name",
        "system_prompt_block",
        "onboarding_prompt",
        "state_schema",
        "compatible_archetypes",
        "proactive_kind",
        "trigger_type",
        "checkin_prompt",
        "default_cadence_hours",
        "requires_search",
    ):
        assert f'"{key}":' in src, f"missing key {key!r} in nutrition_coach"


def test_skills_state_schema_has_setup_runtime_split():
    """state_schema is documentation — convention is split into setup
    (onboarding) and runtime (mutated by system). Pin both halves so
    a future skill author follows the same shape."""
    src = _read("app/services/skills.py")
    assert '"setup":' in src
    assert '"runtime":' in src


def test_skills_nutrition_coach_archetype_compatibility():
    """nutrition_coach declares advisor + educator compatible; NOT
    companion (the companion archetype prompt forbids medical advice)."""
    src = _read("app/services/skills.py")
    schema_pos = src.find('"nutrition_coach":')
    body = src[schema_pos : schema_pos + 5000]
    assert '"advisor"' in body
    assert '"educator"' in body


def test_skills_module_has_get_helper():
    """get(slug) is the catalog-lookup primitive used by soul_file and
    routes. Pin it so a future "convert dict to DB table" refactor
    can swap the implementation without changing call sites."""
    src = _read("app/services/skills.py")
    assert "def get(slug: str)" in src


def test_skills_module_has_compatibility_helper():
    src = _read("app/services/skills.py")
    assert "def is_archetype_compatible(" in src


# ─── soul_file.compose skill layer ────────────────────────────────────────


def test_soul_file_compose_accepts_skill_kwargs():
    """compose() gained skill_slug + user_skill_state kwargs. Both
    optional — non-skilled influencers (the majority today) keep the
    same signature behavior."""
    src = _read("app/services/soul_file.py")
    assert "skill_slug: str | None = None" in src
    assert "user_skill_state: dict | None = None" in src


def test_soul_file_compose_layer_order_skill_after_archetype():
    """Layer order per design: GLOBAL → ARCHETYPE → SKILL → PER_INF →
    USER_STATE → MEMORIES. Skill AFTER archetype so its carve-outs win."""
    src = _read("app/services/soul_file.py")
    fn_start = src.find("def compose(")
    body = src[fn_start : fn_start + 5000]
    # The archetype lookup must come before the skill lookup
    archetype_pos = body.find("ARCHETYPE_PROMPTS[archetype]")
    skill_pos = body.find("_skills.get(skill_slug)")
    assert archetype_pos > 0 and skill_pos > 0
    assert archetype_pos < skill_pos


def test_soul_file_compose_user_state_includes_setup_and_runtime():
    """user_skill_state layer must render BOTH setup and runtime halves
    so the bot grounds its reply in the full plan, not just onboarding."""
    src = _read("app/services/soul_file.py")
    fn_start = src.find("def compose(")
    body = src[fn_start : fn_start + 5000]
    assert '"setup"' in body and '"runtime"' in body
    # 2026-06-12 SSOT extraction: the literal "Your current plan…" text
    # moved out of compose() into module-level USER_SEGMENT_PLAN_TEMPLATE
    # so the preview endpoint can render the same wording. compose() now
    # formats the constant with plan_lines. Pin BOTH halves so the
    # extraction can't drift.
    assert "Your current plan for this user" in src
    assert "USER_SEGMENT_PLAN_TEMPLATE.format(plan_lines=" in body


def test_soul_file_compose_skill_layer_no_global_rules_edit():
    """GLOBAL_RULES must stay byte-identical post-Phase-23 so
    non-skilled influencers (the majority) aren't affected. Pin the
    canonical first line."""
    src = _read("app/services/soul_file.py")
    assert "You are an AI personality on the YRAL social platform" in src


# ─── repository ───────────────────────────────────────────────────────────


def test_skill_state_repo_has_required_ops():
    src = _read("app/repositories/skill_state_repo.py")
    for op in (
        "async def get(",
        "async def upsert(",
        "async def list_due(",
        "async def mark_event_fired(",
        "async def pause(",
        "async def resume(",
    ):
        assert op in src, f"skill_state_repo missing {op!r}"


def test_skill_state_repo_upsert_merges_state_jsonb():
    """state JSONB merges on conflict (|| operator) so a runtime
    update doesn't blow away the onboarding-collected setup half.
    The day a proactive tick writes runtime.last_event_at, setup
    survives untouched."""
    src = _read("app/repositories/skill_state_repo.py")
    assert "state || EXCLUDED.state" in src


def test_skill_state_repo_list_due_uses_partial_index_predicate():
    """list_due's WHERE matches the partial index in migration 029.
    Without this match, Postgres would do a seq scan."""
    src = _read("app/repositories/skill_state_repo.py")
    fn_start = src.find("async def list_due(")
    body = src[fn_start : fn_start + 2000]
    assert "status = 'active'" in body
    assert "next_event_at IS NOT NULL" in body
    assert "next_event_at <= NOW()" in body


# ─── llm_registry process ─────────────────────────────────────────────────


def test_registry_has_no_skill_specific_processes():
    """Skills are pure Soul File composition — the skill identity lives
    in the prompt layers, NOT in routing. Adding a new skill must
    require zero registry changes. Pin the absence of any skill-named
    process so a future "let me just add nutrition_coach_chat for
    cleaner accounting" PR runs into this test instead of growing
    the registry surface."""
    src = _read("app/services/llm_registry.py")
    forbidden = (
        "skill_chat",
        "nutrition_coach_chat",
        "english_coach_chat",
        "daily_briefing_chat",
        "travel_advisor_chat",
        "real_estate_advisor_chat",
    )
    for name in forbidden:
        assert name not in src, (
            f"{name!r} found in llm_registry.py — skills route through "
            "user_chat_main; per-skill cost tracking goes on llm_costs "
            "as a skill_slug tag, not as a new process."
        )


def test_registry_explicitly_documents_skill_routing_decision():
    """The reasoning for why skills don't get their own process must
    be visible in the registry source (the place a future contributor
    will look when tempted to add one). Don't bury this in a doc."""
    src = _read("app/services/llm_registry.py")
    # Phrasing-tolerant pin: both 'user_chat_main' and a skill mention
    # appear in the explanatory comment block we just added.
    assert "skilled influencers" in src.lower() or "skill content" in src.lower()
    assert "user_chat_main" in src
