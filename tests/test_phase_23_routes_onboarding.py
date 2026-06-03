"""Phase 23.5 — source-pin tests for skill routes + parser + chat hooks.

Mirrors test_phase_23_foundation.py shape: read files, string-pin the
invariants. No httpx, no DB. The integration value comes from Rishi's
Motorola test once Kareena is assigned (23.7).

What we pin here:
  - app/routes/skills.py has the 3 endpoints (GET/POST/PATCH)
  - app/services/skill_parser.py has the streaming-safe filter
  - app/routes/chat.py wires the hook on BOTH paths (POST + stream)
  - Influencer SELECTs include skill_slug post-migration 030
  - main.py registers skills_router
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# --- Routes ---------------------------------------------------------------


def test_routes_skills_exists_with_three_endpoints():
    src = _read("app/routes/skills.py")
    assert 'prefix="/api/v1/skills"' in src
    # GET state, POST state, PATCH preferences — pin the path + verb.
    assert '@router.get("/{influencer_id}/state")' in src
    assert '@router.post("/{influencer_id}/state")' in src
    assert '@router.patch("/{influencer_id}/preferences")' in src


def test_routes_skills_404s_on_missing_skill_slug():
    """Routes must 404 when the influencer has no skill — there's nothing
    to operate on. Pin via the helper that enforces it."""
    src = _read("app/routes/skills.py")
    assert "_require_skilled_influencer" in src
    # The helper raises 404 on missing skill_slug; pin both branches.
    assert "Influencer not found" in src
    assert "Influencer has no skill assigned" in src
    assert "Skill not found in catalog" in src


def test_routes_skills_patch_merges_setup_safely():
    """PATCH /preferences must NOT clobber the existing setup half. Pin
    the Python-side merge so the upsert's top-level state||EXCLUDED
    preserves the unchanged setup keys."""
    src = _read("app/routes/skills.py")
    assert 'merged_setup = {**(existing["state"].get("setup") or {}), **prefs}' in src


def test_main_registers_skills_router():
    src = _read("app/main.py")
    assert "from routes.skills import router as skills_router" in src
    assert "app.include_router(skills_router)" in src


# --- Parser ---------------------------------------------------------------


def test_skill_parser_exists():
    src = _read("app/services/skill_parser.py")
    assert "def parse_skill_state_block(" in src
    assert "class SkillStateStreamFilter" in src


def test_parser_strips_block_even_on_json_failure():
    """Parser must always return a cleaned content (so mobile never sees
    the literal tag), even when the JSON inside the block is malformed."""
    from app.services.skill_parser import parse_skill_state_block

    # Malformed JSON inside a well-formed block:
    text = "Hi! <skill_state>{not valid json</skill_state> bye"
    parsed, cleaned = parse_skill_state_block(text)
    assert parsed is None
    assert "<skill_state>" not in cleaned
    assert "Hi!" in cleaned and "bye" in cleaned


def test_parser_accepts_only_top_level_object():
    from app.services.skill_parser import parse_skill_state_block

    parsed, _ = parse_skill_state_block(
        'before <skill_state>{"setup":{"primary_goal":"lose 5kg"}}</skill_state> after'
    )
    assert parsed == {"setup": {"primary_goal": "lose 5kg"}}

    # Array → rejected as parse failure (still strips block)
    parsed_arr, cleaned_arr = parse_skill_state_block(
        "<skill_state>[1,2,3]</skill_state>"
    )
    assert parsed_arr is None
    assert "<skill_state>" not in cleaned_arr


def test_parser_handles_markdown_fence():
    """Some models wrap JSON in ```json … ``` even when the system prompt
    says no. The parser must tolerate the fence."""
    from app.services.skill_parser import parse_skill_state_block

    text = '<skill_state>```json\n{"setup": {"primary_goal": "x"}}\n```</skill_state>'
    parsed, _ = parse_skill_state_block(text)
    assert parsed == {"setup": {"primary_goal": "x"}}


def test_stream_filter_holds_back_partial_tag():
    """Stream filter must NOT leak the first character of <skill_state>
    when it could be the start of the tag. Critical UX guarantee:
    mobile never sees the literal block."""
    from app.services.skill_parser import SkillStateStreamFilter

    f = SkillStateStreamFilter()
    out = []
    # Feed tokens that, concatenated, form: "Hello <skill_state>{...}</skill_state>"
    for tok in [
        "Hel",
        "lo ",
        "<sk",
        "ill",
        "_st",
        "ate",
        '>{"setup":{"a":1}}',
        "</skill_state>",
    ]:
        emit = f.feed(tok)
        if emit:
            out.append(emit)
    out.append(f.flush())
    rendered = "".join(out)
    assert "<skill_state>" not in rendered
    assert "Hello " in rendered  # pre-tag text reached the client
    parsed, _ = f.parse()
    assert parsed == {"setup": {"a": 1}}


def test_stream_filter_passthrough_when_no_tag():
    """Without a tag in the stream, every byte must pass through —
    non-skilled influencers must see no behavior change."""
    from app.services.skill_parser import SkillStateStreamFilter

    f = SkillStateStreamFilter()
    pieces = ["Hello", " world", "!"]
    out = []
    for tok in pieces:
        out.append(f.feed(tok))
    out.append(f.flush())
    assert "".join(out) == "Hello world!"


# --- Chat hooks -----------------------------------------------------------


def test_chat_wires_skill_layer_on_both_endpoints():
    """Both POST /messages (non-streaming) and /messages/stream must
    call _prepare_skill_layer. Symmetric coverage."""
    src = _read("app/routes/chat.py")
    # _prepare_skill_layer should be called from the non-stream path
    # and the streaming path. Two call sites:
    assert src.count("_prepare_skill_layer(pool, inf, user_id, influencer_id)") >= 2


def test_chat_passes_skill_kwargs_to_compose():
    """compose() must receive skill_slug + user_skill_state kwargs from
    both call sites."""
    src = _read("app/routes/chat.py")
    assert 'skill_slug=skill_ctx["skill_slug"]' in src
    assert "user_skill_state=(" in src


def test_chat_strips_skill_block_from_nonstreaming_content():
    """Non-streaming path must strip the <skill_state> block from
    llm_result.content before persisting + returning."""
    src = _read("app/routes/chat.py")
    assert "skill_parser.parse_skill_state_block(" in src
    # Replace via dataclasses.replace because LlmResponse is frozen.
    assert "_dc_replace(llm_result, content=cleaned)" in src


def test_chat_uses_stream_filter_for_streaming():
    """Streaming path must use SkillStateStreamFilter — mobile UX
    requires the block be suppressed mid-stream, not just stripped after."""
    src = _read("app/routes/chat.py")
    assert "SkillStateStreamFilter()" in src
    assert "stream_filter.feed(value)" in src
    assert "stream_filter.flush()" in src
    assert "stream_filter.parse()" in src


def test_chat_persists_only_in_onboarding_mode():
    """Subsequent turns must NOT overwrite state from a stray tag — only
    the first onboarding turn writes. Pin the gate."""
    src = _read("app/routes/chat.py")
    # Both call sites must guard the persist by onboarding_mode.
    assert src.count('skill_ctx["onboarding_mode"]') >= 2


def test_chat_skill_failure_falls_through_safely():
    """A skill lookup exception must not 500 the chat endpoint. Pin the
    try/except envelope in _prepare_skill_layer."""
    src = _read("app/routes/chat.py")
    # The helper has its own try/except returning the empty skill_ctx.
    helper_pos = src.find("async def _prepare_skill_layer(")
    assert helper_pos != -1
    helper_end = src.find("\n\nasync def ", helper_pos + 1)
    helper_body = src[helper_pos:helper_end]
    assert "except Exception:" in helper_body
    assert "falling back to non-skilled flow" in helper_body


# --- Repo: skill_slug in SELECT ------------------------------------------


def test_influencer_repo_selects_skill_slug():
    """After migration 030, ai_influencers has a skill_slug column.
    Every SELECT chat.py touches must include it — otherwise the
    skill layer would see skill_slug as None for skilled influencers."""
    src = _read("app/repositories/influencer_repo.py")
    # Spot-check the 4 user-facing SELECTs (id, name, id_or_name, list_all)
    # all carry skill_slug. Lazy: count occurrences ≥ 4.
    assert src.count("skill_slug") >= 4


def test_influencer_repo_no_unconverted_selects():
    """Symmetry rule. Every SELECT that lists `metadata` on
    ai_influencers must also list skill_slug — both are columns the
    chat path inspects. INSERTs are exempt (they specify the columns
    a row is created with; skill is assigned post-create via admin)."""
    src = _read("app/repositories/influencer_repo.py")
    lines = src.split("\n")
    for i, line in enumerate(lines):
        if ", metadata" not in line:
            continue
        window = "\n".join(lines[max(0, i - 12) : i + 4])
        # Only enforce on SELECT windows. INSERT INTO windows are exempt.
        if "SELECT" not in window or "INSERT INTO" in window:
            continue
        assert "skill_slug" in window, (
            f"line {i + 1}: SELECT with metadata missing skill_slug:\n{window}"
        )
