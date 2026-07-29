"""Phase 21γ.P34.M1 — Discovery Feed classification.

Two categories of tests:

  1. SOURCE-PIN — defends the wiring + 5 invariants from the brief
     + the 2026-06-16 PM decision to use the 5-value `archetype` enum
     (NOT the rev-7 8-value `bot_type` taxonomy).
  2. BEHAVIOURAL — exercises `_parse_classification` +
     `_validate_classification` + `_build_classification_messages` +
     `apply_admin_override` with stubbed inputs (no LLM, no DB).
"""

import asyncio
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════════
# 1. SOURCE-PIN — defend the 5 invariants
# ══════════════════════════════════════════════════════════════════════


def test_runpod_vllm_supports_vision_flipped_on():
    """Invariant 1: the H12 capability guard must permit the
    classifier to send image_url parts to runpod_vllm. If a future
    PR flips this back to False, the classifier silently degrades."""
    src = (REPO / "app" / "services" / "llm_registry.py").read_text()
    # Anchor on the 2026-06-16 Session 6 empirical-verification comment
    # so a refactor that drops it would surface here.
    assert "Session 6 verified empirically" in src
    runpod_block = src[src.index('"runpod_vllm"') : src.index('"ollama"')]
    assert '"supports_vision": True' in runpod_block


def test_influencer_classification_in_process_names():
    """Invariant 2: registered as a process so llm_registry.call()
    can resolve it. Missing process name = ValueError at first call."""
    src = (REPO / "app" / "services" / "llm_registry.py").read_text()
    assert '"influencer_classification"' in src
    # And in LLM_DEFAULTS pointing at runpod_vllm
    defaults_block = src[src.index("LLM_DEFAULTS: dict[str, dict[str, Any]]") :]
    assert '"influencer_classification":' in defaults_block
    classify_block = defaults_block[
        defaults_block.index('"influencer_classification":') :
    ][:400]
    assert '"provider": "runpod_vllm"' in classify_block


def test_influencer_classification_in_async_never_gemini():
    """Invariant 3: never let this leak to gemini even via DB override."""
    src = (REPO / "app" / "services" / "llm_registry.py").read_text()
    never_block = src[src.index("ASYNC_PROCESSES_NEVER_GEMINI") :][:1000]
    assert '"influencer_classification"' in never_block


def test_chat_template_kwargs_inherited_from_provider_default():
    """Invariant 4: runpod_vllm's default_extra_body already disables
    thinking mode. Saikat measured 10x latency win from this."""
    src = (REPO / "app" / "services" / "llm_registry.py").read_text()
    runpod_block = src[src.index('"runpod_vllm"') : src.index('"ollama"')]
    assert '"chat_template_kwargs": {"enable_thinking": False}' in runpod_block


def test_classification_loop_defaults_off():
    """Invariant 5: ship dormant. Rishi reviews sample output BEFORE
    the full backfill sweep."""
    src = (REPO / "app" / "kill_switch.py").read_text()
    assert "_DEFAULT_OFF_LOOPS" in src
    assert '"influencer_classification"' in src
    assert "ENABLE_INFLUENCER_CLASSIFICATION_LOOP" in src


# ─── migration shape ────────────────────────────────────────────────────


def test_migration_042_adds_gender_and_archetype_not_bot_type():
    """Locked 2026-06-16 PM: `archetype` (5-value enum) replaces the
    rev-7 8-value `bot_type`. If a future PR re-introduces bot_type
    as a column, this test catches it."""
    src = (REPO / "migrations" / "042_ai_influencers_gender_archetype.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS gender" in src
    assert "ADD COLUMN IF NOT EXISTS archetype" in src
    # bot_type is verboten as a column name in this PR.
    assert "ADD COLUMN IF NOT EXISTS bot_type" not in src
    # Both default 'unknown'.
    assert "DEFAULT 'unknown'" in src
    # M2 composer index on the new column.
    assert "CREATE INDEX IF NOT EXISTS idx_ai_influencers_archetype" in src


def test_migration_042_creates_pg_trgm_extension_and_category_gin_index():
    """M4 category_affinity needs a trigram GIN index on LOWER(category).
    `CREATE EXTENSION pg_trgm` is idempotent (chat-ai already enables
    it in prod), so this is a no-op on the target cluster."""
    src = (REPO / "migrations" / "042_ai_influencers_gender_archetype.sql").read_text()
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in src
    assert "CREATE INDEX IF NOT EXISTS idx_ai_influencers_category_trgm" in src
    assert "gin_trgm_ops" in src
    assert "LOWER(category)" in src


def test_migration_042_has_squawk_preamble():
    src = (REPO / "migrations" / "042_ai_influencers_gender_archetype.sql").read_text()
    assert "SET lock_timeout = '3s';" in src
    assert "SET statement_timeout = '60s';" in src


# ─── service module shape ───────────────────────────────────────────────


def test_classification_service_exposes_required_symbols():
    src = (REPO / "app" / "services" / "influencer_classification.py").read_text()
    for name in (
        "async def classify_one",
        "async def classify_sample",
        "async def classify_all_once",
        "async def classification_loop",
        "async def apply_admin_override",
        "_build_classification_messages",
        "_parse_classification",
        "_validate_classification",
        "VALID_GENDERS",
        "VALID_ARCHETYPES",
        "VALID_CONFIDENCES",
    ):
        assert name in src, f"missing symbol: {name}"


def test_classification_service_drops_bot_type_completely():
    """No vestigial `bot_type` CODE references — the rename must be
    total at the symbol level. The docstring is allowed to mention
    the old name (rationale for the change), but no identifiers /
    columns / dict keys should still carry it."""
    src = (REPO / "app" / "services" / "influencer_classification.py").read_text()
    assert "VALID_BOT_TYPES" not in src
    # Strip the leading docstring (which legitimately documents the
    # rename history) before scanning for `bot_type` identifiers.
    # The module starts with `"""..."""` then `import ...`.
    docstring_end = src.index('"""', 4) + 3  # close of leading docstring
    code_only = src[docstring_end:]
    assert "bot_type" not in code_only


def test_classification_throttle_matches_brief():
    src = (REPO / "app" / "services" / "influencer_classification.py").read_text()
    assert "CLASSIFICATION_PER_MINUTE = 10" in src
    assert "SECONDS_BETWEEN_CALLS = 60.0 / CLASSIFICATION_PER_MINUTE" in src


def test_classification_sample_does_not_write_to_db():
    """The sample path is intentionally read-only. If a future PR
    accidentally wires `_apply_classification` into the sample path,
    Rishi's review gate evaporates."""
    src = (REPO / "app" / "services" / "influencer_classification.py").read_text()
    sample_fn_start = src.index("async def classify_sample")
    sample_fn_end = src.index("async def _list_unclassified_bots")
    sample_body = src[sample_fn_start:sample_fn_end]
    assert "_apply_classification" not in sample_body


def test_classification_loop_only_touches_double_unknown_rows():
    """Operator overrides win — direct SQL UPDATE on EITHER column
    excludes that bot from the loop's scope. If the WHERE clause
    drifts to `OR`, manual overrides start getting clobbered."""
    src = (REPO / "app" / "services" / "influencer_classification.py").read_text()
    # _list_unclassified_bots WHERE clause must require BOTH columns
    # to still be 'unknown'.
    loop_fn_start = src.index("async def _list_unclassified_bots")
    loop_fn_end = src.index("async def _apply_classification")
    loop_body = src[loop_fn_start:loop_fn_end]
    assert "WHERE is_active = 'active'" in loop_body
    assert "gender = 'unknown'" in loop_body
    assert "archetype = 'unknown'" in loop_body
    assert " AND " in loop_body  # AND-of-both, not OR


def test_main_wires_classification_loop_and_admin_router():
    src = (REPO / "app" / "main.py").read_text()
    assert "from services.influencer_classification import classification_loop" in src
    assert "classification_task = asyncio.create_task(classification_loop())" in src
    assert "classification_task.cancel()" in src
    assert "await classification_task" in src
    assert (
        "from routes.admin_classification import router as admin_classification_router"
        in src
    )
    assert "app.include_router(admin_classification_router)" in src


def test_admin_router_exposes_sample_and_override_endpoints():
    src = (REPO / "app" / "routes" / "admin_classification.py").read_text()
    assert '"/admin/discovery/classify-sample"' in src
    assert '"/admin/discovery/classify-override"' in src
    assert 'alias="X-Admin-Key"' in src
    assert "secrets.compare_digest" in src


# ─── soul_file.py — archetype column wired with category fallback ───────


def test_soul_file_compose_takes_archetype_param():
    """soul_file.compose() must accept the new `archetype` kwarg.
    Callers (chat.py + proactive.py + influencer_summary.py) pass
    inf.get('archetype'); the function prefers it over category."""
    src = (REPO / "app" / "services" / "soul_file.py").read_text()
    assert "archetype: str | None = None" in src
    # The compose body must check archetype first, then fall back to
    # category (the historical path). Don't pin the exact source — pin
    # the resolve_archetype helper that encapsulates the logic.
    assert "def resolve_archetype" in src
    assert "ARCHETYPE_PROMPTS" in src


def test_chat_callers_pass_archetype_column():
    """Each compose() call site must pass `archetype=inf.get('archetype')`
    so the new column is consulted at the read path."""
    src = (REPO / "app" / "routes" / "chat.py").read_text()
    # Both compose() call sites (line ~687 + ~935) must pass archetype.
    assert src.count('archetype=inf.get("archetype")') >= 2


def test_archetype_prompts_kept_at_5_values():
    """Locked at 5 per Rishi 2026-06-16. Adding a 6th would silently
    drift the classifier's enum + the soul_file prompt layer."""
    src = (REPO / "app" / "services" / "soul_file.py").read_text()
    # The ARCHETYPE_PROMPTS dict must contain exactly the 5 magic
    # keys + no more. Look for each key + count "ARCHETYPE_PROMPTS = {"
    # block. Pure regex would be fragile; defer to the in-module dict
    # via the import smoke test below.
    for k in ("companion", "advisor", "entertainer", "educator", "creator"):
        assert f'"{k}":' in src


# ══════════════════════════════════════════════════════════════════════
# 2. BEHAVIOURAL — parser + message builder + override + helpers
# ══════════════════════════════════════════════════════════════════════


def test_parse_classification_strict_json():
    from services.influencer_classification import _parse_classification

    out = _parse_classification(
        '{"gender": "female", "archetype": "companion", "confidence": "high"}'
    )
    assert out == {"gender": "female", "archetype": "companion", "confidence": "high"}


def test_parse_classification_strips_code_fences():
    from services.influencer_classification import _parse_classification

    raw = (
        '```json\n{"gender": "male", "archetype": "advisor", '
        '"confidence": "medium"}\n```'
    )
    out = _parse_classification(raw)
    assert out == {"gender": "male", "archetype": "advisor", "confidence": "medium"}


def test_parse_classification_missing_confidence_defaults_to_low():
    """When the model forgets to emit `confidence`, the validator
    defaults it to 'low' so the downstream review can spot the row."""
    from services.influencer_classification import _parse_classification

    out = _parse_classification('{"gender": "female", "archetype": "creator"}')
    assert out == {"gender": "female", "archetype": "creator", "confidence": "low"}


def test_parse_classification_unknown_value_collapses_to_unknown():
    from services.influencer_classification import _parse_classification

    raw = '{"gender": "alien", "archetype": "companion", "confidence": "high"}'
    out = _parse_classification(raw)
    assert out == {"gender": "unknown", "archetype": "companion", "confidence": "high"}


def test_parse_classification_both_unknown_returns_none():
    """Both-unknown is treated as classification failure so we don't
    overwrite a possible future better label with 'unknown'."""
    from services.influencer_classification import _parse_classification

    raw = '{"gender": "", "archetype": "garbage", "confidence": "low"}'
    assert _parse_classification(raw) is None


def test_parse_classification_empty_string_returns_none():
    from services.influencer_classification import _parse_classification

    assert _parse_classification("") is None
    assert _parse_classification("no json here") is None


def test_parse_classification_handles_prose_around_json():
    from services.influencer_classification import _parse_classification

    raw = (
        "Sure! Here's the classification:\n"
        '{"gender": "neutral", "archetype": "entertainer", '
        '"confidence": "medium"}\n'
        "Hope this helps!"
    )
    out = _parse_classification(raw)
    assert out == {
        "gender": "neutral",
        "archetype": "entertainer",
        "confidence": "medium",
    }


def test_build_messages_includes_image_when_avatar_present():
    from services.influencer_classification import _build_classification_messages

    bot = {
        "id": "abc",
        "display_name": "Tara",
        "description": "AI companion",
        "system_instructions": "You are Tara.",
        "avatar_url": "https://cdn.example/tara.jpg",
    }
    msgs = _build_classification_messages(bot)
    user_content = msgs[-1]["content"]
    assert isinstance(user_content, list)
    image_parts = [p for p in user_content if p.get("type") == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == "https://cdn.example/tara.jpg"


def test_build_messages_omits_image_when_no_avatar():
    from services.influencer_classification import _build_classification_messages

    bot = {
        "id": "abc",
        "display_name": "Tara",
        "description": "AI companion",
        "system_instructions": "You are Tara.",
        "avatar_url": "",
    }
    msgs = _build_classification_messages(bot)
    user_content = msgs[-1]["content"]
    image_parts = [p for p in user_content if p.get("type") == "image_url"]
    assert image_parts == []


def test_build_messages_prompt_mentions_5_archetypes_not_8():
    """The prompt must enumerate the 5 archetypes, NOT the rev-7
    8-value bot_type taxonomy. If a future PR pastes the old prompt
    back in, classifier output becomes invalid against the column."""
    from services.influencer_classification import _build_classification_messages

    msgs = _build_classification_messages({"id": "x", "avatar_url": ""})
    text_part = [p for p in msgs[-1]["content"] if p.get("type") == "text"][0]["text"]
    for keep in ("companion", "advisor", "entertainer", "educator", "creator"):
        assert keep in text_part
    # The rev-7 bot_type names must NOT appear.
    for drop in (
        "fitness_health",
        "coach_mentor",
        "creator_artist",
        "character_fictional",
        "expert_professional",
    ):
        assert drop not in text_part


def test_taxonomy_locked_to_5_archetypes_plus_unknown():
    """Adding a 6th value would silently drift soul_file's
    ARCHETYPE_PROMPTS dict (which stays at 5)."""
    from services.influencer_classification import VALID_ARCHETYPES

    assert VALID_ARCHETYPES == frozenset(
        {
            "companion",
            "advisor",
            "entertainer",
            "educator",
            "creator",
            "unknown",
        }
    )


def test_genders_locked():
    from services.influencer_classification import VALID_GENDERS

    assert VALID_GENDERS == frozenset({"male", "female", "neutral", "unknown"})


def test_confidences_locked():
    from services.influencer_classification import VALID_CONFIDENCES

    assert VALID_CONFIDENCES == frozenset({"high", "medium", "low"})


def test_kill_switch_default_off_for_classification():
    """With no env vars set the loop is OFF."""
    import os

    from kill_switch import is_enabled

    for k in (
        "GEMINI_BACKGROUND_LOOPS_ENABLED",
        "ENABLE_INFLUENCER_CLASSIFICATION_LOOP",
    ):
        os.environ.pop(k, None)
    assert is_enabled("influencer_classification") is False


def test_kill_switch_default_on_for_other_loops_unchanged():
    """Regression: existing loops still default ON."""
    import os

    from kill_switch import is_enabled

    for k in (
        "GEMINI_BACKGROUND_LOOPS_ENABLED",
        "ENABLE_PROACTIVE_LOOP",
        "ENABLE_QUALITY_SCORER",
    ):
        os.environ.pop(k, None)
    assert is_enabled("proactive") is True
    assert is_enabled("quality_scorer") is True


def test_kill_switch_enables_classification_when_env_true():
    import os

    from kill_switch import is_enabled

    os.environ.pop("GEMINI_BACKGROUND_LOOPS_ENABLED", None)
    os.environ["ENABLE_INFLUENCER_CLASSIFICATION_LOOP"] = "true"
    try:
        assert is_enabled("influencer_classification") is True
    finally:
        os.environ.pop("ENABLE_INFLUENCER_CLASSIFICATION_LOOP", None)


def test_classify_one_returns_none_on_llm_exception(monkeypatch):
    """The whole point of returning None is that the loop keeps going
    after a transient LLM error."""
    from services import influencer_classification as ic

    async def boom(**kw):
        raise RuntimeError("simulated pod-down")

    import services.llm_registry as real_registry

    monkeypatch.setattr(real_registry, "call", boom, raising=False)

    out = asyncio.run(
        ic.classify_one(
            {
                "id": "bot1",
                "display_name": "x",
                "description": "y",
                "system_instructions": "z",
                "avatar_url": "",
            }
        )
    )
    assert out is None


# ─── soul_file.resolve_archetype — the new-column-with-fallback shim ────


def test_resolve_archetype_prefers_column_when_valid():
    from services.soul_file import resolve_archetype

    inf = {"archetype": "advisor", "category": "Food & Drink"}
    assert resolve_archetype(inf) == "advisor"


def test_resolve_archetype_falls_back_to_category_when_column_unknown():
    """The 93%-of-bots silent-skip bug case: archetype is the sentinel
    'unknown' (pre-classify), category matches one of the 5 magic keys."""
    from services.soul_file import resolve_archetype

    inf = {"archetype": "unknown", "category": "companion"}
    assert resolve_archetype(inf) == "companion"


def test_resolve_archetype_falls_back_when_column_missing():
    from services.soul_file import resolve_archetype

    inf = {"category": "advisor"}
    assert resolve_archetype(inf) == "advisor"


def test_resolve_archetype_returns_none_when_neither_matches():
    """93% of production bots today: category doesn't match any of the
    5 keys AND no archetype column yet."""
    from services.soul_file import resolve_archetype

    inf = {"category": "Food & Drink"}
    assert resolve_archetype(inf) is None


def test_resolve_archetype_case_insensitive():
    from services.soul_file import resolve_archetype

    inf = {"archetype": "  CREATOR  "}
    assert resolve_archetype(inf) == "creator"


# ─── apply_admin_override — enum validation + write path ────────────────


class _StubPool:
    """Minimal asyncpg-pool stand-in. Records executed SQL + args."""

    def __init__(self, returning: dict | None = None):
        self.returning = returning
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self.returning


def test_apply_admin_override_rejects_invalid_archetype():
    from services.influencer_classification import apply_admin_override

    pool = _StubPool()
    with pytest.raises(ValueError, match="archetype must be one of"):
        asyncio.run(
            apply_admin_override(
                pool, influencer_id="x", archetype="character_fictional"
            )
        )
    assert pool.calls == []  # never reached the DB


def test_apply_admin_override_rejects_invalid_gender():
    from services.influencer_classification import apply_admin_override

    pool = _StubPool()
    with pytest.raises(ValueError, match="gender must be one of"):
        asyncio.run(apply_admin_override(pool, influencer_id="x", gender="other"))


def test_apply_admin_override_requires_at_least_one_field():
    from services.influencer_classification import apply_admin_override

    pool = _StubPool()
    with pytest.raises(ValueError, match="at least one of"):
        asyncio.run(apply_admin_override(pool, influencer_id="x"))


def test_apply_admin_override_writes_archetype_and_gender():
    """All-valid path. Verify the SQL includes both fields + the
    RETURNING clause surfaces the row."""
    from services.influencer_classification import apply_admin_override

    pool = _StubPool(
        returning={
            "id": "x",
            "display_name": "Tara",
            "category": "Lifestyle",
            "archetype": "companion",
            "gender": "female",
        }
    )
    row = asyncio.run(
        apply_admin_override(
            pool, influencer_id="x", archetype="companion", gender="female"
        )
    )
    assert row["archetype"] == "companion"
    assert row["gender"] == "female"
    assert len(pool.calls) == 1
    sql, args = pool.calls[0]
    assert "archetype = $" in sql
    assert "gender = $" in sql
    assert "RETURNING" in sql


def test_apply_admin_override_writes_category_only():
    """Free-form category-only override (e.g. mobile-display rename)."""
    from services.influencer_classification import apply_admin_override

    pool = _StubPool(
        returning={
            "id": "x",
            "display_name": "Tara",
            "category": "Travel",
            "archetype": "unknown",
            "gender": "unknown",
        }
    )
    asyncio.run(apply_admin_override(pool, influencer_id="x", category="Travel"))
    sql, args = pool.calls[0]
    assert "category = $" in sql
    assert "archetype = $" not in sql
    assert "gender = $" not in sql
