"""Coach Day 14 pivot — GET /api/v1/influencers/{bot_id}/system-prompt-preview.

Read-only transparency window. All edits go through Coach /apply; this
endpoint shows the bot owner EXACTLY what the LLM sees at chat time.

Source-pin tests (fastapi/httpx not in local venv — wire-level smoke
runs in CI/prod, matching the discipline established in
test_coach_bucket_2_pr3.py + test_llm_routing_admin.py).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROUTE = REPO / "app" / "routes" / "soul_file.py"


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── route shape + owner gate ───────────────────────────────────────────


def test_endpoint_registered_under_api_v1_influencers():
    """Sibling to /soul-file under the same router prefix — SYMMETRY
    with existing transparency endpoints."""
    src = ROUTE.read_text()
    assert '@router.get("/influencers/{bot_id}/system-prompt-preview")' in src


def test_endpoint_uses_shared_owner_gate():
    """Reuse `_load_owned_influencer` so 403/404 surface stays identical
    to GET/PUT /soul-file. No bespoke auth path."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    assert "await _load_owned_influencer(pool, user_id, bot_id)" in handler


# ─── response-key contract (mobile guard) ───────────────────────────────


def test_response_top_level_keys_match_mobile_contract():
    """Pin the EXACT top-level key set so mobile doesn't break on a
    silently-renamed field. Brief said: match exactly, do not deviate."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    for key in (
        '"bot_id":',
        '"bot_name":',
        '"archetype":',
        '"as_of":',
        '"layers":',
        '"skills_enabled":',
        '"applied_overrides":',
        '"composed_preview_text":',
    ):
        assert key in handler, f"top-level key missing: {key}"


def test_response_layers_nested_keys_match_contract():
    """Nested `layers` block — pin the L1/L2/L3/L3-fallback/L4 keys so
    a re-name doesn't silently leak past CI."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    for key in (
        '"L1_global_rules":',
        '"L2_archetype_block":',
        '"L3_personality_sections":',
        '"L3_flat_fallback":',
        '"L4_user_segment_template":',
    ):
        assert key in handler, f"layers key missing: {key}"


# ─── path 1: sectioned bot ──────────────────────────────────────────────


def test_path_1_sectioned_bot_populates_sections_and_nulls_fallback():
    """When the bot has non-empty system_instructions_sections, the
    L3_personality_sections list is populated AND L3_flat_fallback is
    explicitly null (never omitted) per the brief's "if a layer would
    be empty/null, return empty string for that key (never omit)" rule
    — null vs empty string here distinguishes "sectioned mode active"
    from "flat-text bot."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    # _coerce_sections_list (already in soul_file.py) handles JSONB
    # string-vs-list coercion + filters non-dict entries.
    assert '_coerce_sections_list(inf.get("system_instructions_sections"))' in handler
    # L3_flat_fallback set to None when sections present, else the
    # flat string — ruff may split the ternary across lines, so check
    # both halves of the conditional rather than the full one-liner.
    assert "if not sections" in handler
    assert "else None" in handler


# ─── path 2: legacy flat bot ────────────────────────────────────────────


def test_path_2_legacy_flat_bot_uses_flat_fallback_with_empty_sections():
    """A bot whose system_instructions_sections is `[]` (the today-state
    for 3,941 rows) returns sections [] AND L3_flat_fallback = the
    system_instructions string. Mobile renders the page either way."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    # The flat fallback reads from system_instructions (not name,
    # not description)
    assert 'inf.get("system_instructions") or ""' in handler
    # _coerce_sections_list returns [] for empty/malformed — the
    # ternary then gates the fallback string
    assert '"L3_personality_sections":' in handler


# ─── path 3: overrides defensive decode (PR #370 pattern) ───────────────


def test_path_3_applied_overrides_uses_jsonb_string_decode_guard():
    """asyncpg returns JSONB as a raw string when no codec is registered
    on the pool. A `{**raw_str}` spread would raise TypeError (the
    Anastasia /apply 500 — PR #370). Pin the defensive-parse helper
    so we don't regress."""
    src = ROUTE.read_text()
    assert "_decode_overrides" in src
    # Helper covers both halves: try/except json.loads + isinstance(dict) guard
    fn_pos = src.find("def _decode_overrides(")
    fn = src[fn_pos : fn_pos + 800]
    assert "isinstance(raw, str)" in fn
    assert "_json.loads" in fn
    assert "JSONDecodeError" in fn
    assert "isinstance(raw, dict)" in fn


# ─── path 4: 403 on non-owner ───────────────────────────────────────────


def test_path_4_non_owner_returns_403():
    """Inherited via `_load_owned_influencer` — the shared owner-gate
    is the source of truth. Pin both halves: the helper exists AND the
    preview handler calls it."""
    src = ROUTE.read_text()
    helper_pos = src.find("async def _load_owned_influencer(")
    helper = src[helper_pos : helper_pos + 1000]
    assert "status_code=403" in helper
    assert "parent_principal_id" in helper


# ─── path 5: composed_preview_text strips memory + last-N ──────────────


def test_path_5_composed_preview_strips_memory_and_user_state():
    """compose() takes memory + user_skill_state. The preview strips
    them (memory=None, user_skill_state=None) so the preview is
    deterministic for the bot, independent of any specific user. Pin
    both args explicitly so a future refactor that forgets to stub one
    is caught."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    # The compose() kwargs include the explicit None stubs
    compose_pos = handler.find("_sf.compose(")
    compose_block = handler[compose_pos : compose_pos + 2000]
    assert "memories=None" in compose_block
    assert "user_skill_state=None" in compose_block


# ─── path 6: skills enabled ─────────────────────────────────────────────


def test_path_6_skills_enabled_shape_matches_contract():
    """skills_enabled entries carry id + name + description + prompt_block.
    The prompt_block must equal the EXACT text compose() consumes so
    mobile + the preview render line up byte-for-byte."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    # The dict assembled inside the skills_enabled.append() carries all 4 keys
    for key in ('"id":', '"name":', '"description":', '"prompt_block":'):
        assert key in handler, f"skill entry key missing: {key}"
    # The prompt_block is sourced from the SAME field compose() reads
    assert 'sk["system_prompt_block"]' in handler


def test_path_6_skills_prompt_block_inline_in_composed_preview():
    """Because composed_preview_text calls the SAME compose(...) with
    skill_slug threaded through, any skill's prompt_block appears
    inline. Source-pin the threading so a future refactor can't drop
    the skill_slug arg and quietly break the inline-skill guarantee."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    # skill_slug is read once + threaded into compose()
    assert 'skill_slug = inf.get("skill_slug")' in handler
    # And passed into compose() (not silently dropped)
    compose_pos = handler.find("_sf.compose(")
    compose_block = handler[compose_pos : compose_pos + 2000]
    assert "skill_slug=skill_slug" in compose_block


# ─── path 7: no-skill bot → empty list (never null/missing) ─────────────


def test_path_7_no_skills_returns_empty_list_not_null():
    """`skills_enabled: list[dict] = []` is the initial value; the
    append only fires when a real skill is registered. Pin the init
    so a future refactor can't convert the initial state to None and
    break mobile's "iterate skills_enabled" loop."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    assert "skills_enabled: list[dict] = []" in handler


# ─── caching + freshness ───────────────────────────────────────────────


def test_response_sets_no_store_cache_control():
    """Brief said: live data only, Cache-Control: no-store. A creator
    who flipped an override 10s ago must see it on next refresh — no
    proxy/CDN caching window."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    assert '"Cache-Control": "no-store"' in handler


def test_response_uses_utc_iso8601_as_of_timestamp():
    """`as_of` is the freshness fingerprint — mobile + the owner can
    see how recent the preview is. Pin UTC-tz so a future refactor
    doesn't accidentally drop tzinfo (which breaks JSON serialization
    of naive datetimes through FastAPI's default encoder)."""
    src = ROUTE.read_text()
    pos = src.find("async def get_system_prompt_preview(")
    handler = src[pos : pos + 4000]
    assert "datetime.now(timezone.utc).isoformat()" in handler
