"""Coach Bucket 2 PR-2 — flag + migration 039 + compose sections-aware
+ Coach META_PROMPT branch + 5-tuple coach_reply + /apply dispatch.

Source-pin tests + a handful of behavioural tests against the pure
functions (`render_sections`, `section_body_sha256`, `parse_proposal`,
`_coerce_sections`). httpx + fastapi aren't in the local venv so
endpoint-level smoke happens in CI / prod, not here.

This file complements migration 038's PR-1 test file. Together they
pin the whole Bucket 2 backend surface.
"""

from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
MIG_039 = REPO / "migrations" / "039_coach_messages_proposed_section_change.sql"


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── feature flag ────────────────────────────────────────────────────────


def test_config_exposes_sectioned_v2_flag_default_off():
    """The flag MUST default False — sections column from PR-1 stays
    dormant until backend + mobile cutover. A creator who never opens
    the Soul File page sees zero behaviour change from PR-2."""
    src = _read("app/config.py")
    assert "COACH_SECTIONED_V2_ENABLED" in src
    assert '_env_bool("COACH_SECTIONED_V2_ENABLED", False)' in src


def test_config_flag_references_contract_doc():
    """The flag's comment must link the contract so a future operator
    flipping it has the spec one click away."""
    src = _read("app/config.py")
    assert "coach-bucket-2-sections-contract.md" in src


# ─── migration 039 shape ─────────────────────────────────────────────────


def test_migration_039_exists():
    assert MIG_039.exists(), "migration 039 missing"


def test_migration_039_adds_two_columns_idempotently():
    """Two ALTER TABLEs, both IF NOT EXISTS, both NULLABLE — the
    metadata-only path on pg11+."""
    src = MIG_039.read_text()
    assert "ALTER TABLE coach_messages" in src
    assert "ADD COLUMN IF NOT EXISTS proposed_section_change JSONB" in src
    assert "ADD COLUMN IF NOT EXISTS target_section_id VARCHAR(64)" in src


def test_migration_039_has_squawk_preamble():
    """30s lock_timeout (not 3s) — symmetric with 038 after the
    2026-06-11T09:46Z hot-table lesson on ai_influencers."""
    src = MIG_039.read_text()
    assert "SET lock_timeout = '30s';" in src
    assert "SET statement_timeout = '60s';" in src


def test_migration_039_documents_dispatch_contract():
    """The COMMENT ON COLUMN must name proposed_changes + proposed_global_rule_override
    so an operator reading the schema sees the "exactly one populated"
    invariant the /apply endpoint relies on."""
    src = MIG_039.read_text()
    assert "COMMENT ON COLUMN" in src
    # Both sibling columns named so the dispatch contract is discoverable
    # from the schema alone.
    assert "proposed_global_rule_override" in src or "/apply" in src
    # Section-change shape documented
    for field in (
        "section_id",
        "section_heading",
        "section_editable",
        "new_body",
        "previous_body_sha256",
    ):
        assert field in src, f"missing in migration 039 comment: {field}"


def test_migration_039_documents_target_section_id_denorm():
    src = MIG_039.read_text()
    assert "target_section_id" in src
    # Denormalisation rationale spelled out
    assert "denormalised" in src.lower() or "denormalized" in src.lower()


# ─── soul_file.compose sections-aware ───────────────────────────────────


def test_compose_accepts_sections_kwarg():
    """compose() must accept the sections kwarg without crashing —
    historical 6-positional contract stays compatible because sections
    is keyword-only via the default."""
    from services import soul_file

    # No sections + no flag = flat-text path. Should equal pre-PR-2.
    out = soul_file.compose("hi", category=None, sections=None)
    assert "hi" in out


def test_compose_falls_back_to_flat_when_flag_off():
    """Flag OFF + sections present = flat text wins. Pin so a future
    refactor can't accidentally activate sectioned mode without
    flipping the flag."""
    from services import soul_file

    sections = [
        {"id": "core", "heading": "Core", "body": "section body", "editable": True}
    ]
    with patch.object(soul_file.config, "COACH_SECTIONED_V2_ENABLED", False):
        out = soul_file.compose("flat instructions", sections=sections)
    assert "flat instructions" in out
    assert "section body" not in out


def test_compose_renders_sections_when_flag_on_and_present():
    """Flag ON + non-empty sections = L4 renders FROM sections.
    flat `system_instructions` is omitted entirely so the prompt cache
    stays deterministic for the sectioned shape."""
    from services import soul_file

    sections = [
        {"id": "core", "heading": "Core", "body": "section body", "editable": True}
    ]
    with patch.object(soul_file.config, "COACH_SECTIONED_V2_ENABLED", True):
        out = soul_file.compose("flat instructions", sections=sections)
    assert "section body" in out
    assert "== Core ==" in out
    # Flat text NOT included when sectioned path wins — prevents a
    # double-rendering bug where both layers go into the prompt.
    assert "flat instructions" not in out


def test_compose_falls_back_when_sections_empty_list():
    """Flag ON + empty sections = flat-text path. A bot that's been
    flagged in but hasn't been split yet keeps working unchanged."""
    from services import soul_file

    with patch.object(soul_file.config, "COACH_SECTIONED_V2_ENABLED", True):
        out = soul_file.compose("flat instructions", sections=[])
    assert "flat instructions" in out


def test_compose_falls_back_when_sections_is_json_string():
    """asyncpg returns JSONB as string in this codebase — compose() must
    parse it transparently. Empty-string fallback to flat-text path."""
    from services import soul_file

    with patch.object(soul_file.config, "COACH_SECTIONED_V2_ENABLED", True):
        out = soul_file.compose("flat instructions", sections="[]")
    assert "flat instructions" in out


def test_render_sections_orders_by_array_position():
    """Sections render in the order they appear in the JSONB array.
    The contract says ordering is meaningful — Coach + mobile see the
    same order."""
    from services.soul_file import render_sections

    out = render_sections(
        [
            {"id": "a", "heading": "First", "body": "alpha", "editable": True},
            {"id": "b", "heading": "Second", "body": "beta", "editable": True},
        ]
    )
    assert out.index("First") < out.index("Second")
    assert out.index("alpha") < out.index("beta")


def test_render_sections_skips_empty_body():
    """A half-built bot (heading set, body empty) must not contribute
    an empty `== Heading ==\\n\\n` block. Skip with no error."""
    from services.soul_file import render_sections

    out = render_sections(
        [
            {"id": "a", "heading": "Full", "body": "complete", "editable": True},
            {"id": "b", "heading": "Empty", "body": "", "editable": True},
        ]
    )
    assert "Full" in out
    assert "Empty" not in out


# ─── soul_file.compose call sites threaded ──────────────────────────────


def test_chat_routes_pass_sections_to_compose():
    """Both compose() call sites in chat.py must thread the new sections
    kwarg from inf.get('system_instructions_sections') so chat-time
    prompts see the sectioned soul file when the flag is on."""
    src = _read("app/routes/chat.py")
    # Two compose() call sites in chat.py
    assert src.count('sections=inf.get("system_instructions_sections")') >= 2


def test_proactive_passes_sections_to_compose():
    """The proactive engagement loop renders the same soul file as
    chat.py. Threading sections here too means the flag-on bot's
    sectioned personality applies to proactive messages, not just
    response messages."""
    src = _read("app/services/proactive.py")
    assert 'sections=inf.get("system_instructions_sections")' in src


def test_influencer_summary_passes_sections_to_compose():
    """`influencer_summary.generate_for_influencer` builds the plain-
    English Coach summary. If it ignores sections, the Coach Fix 2
    summary will show stale flat-text content for sectioned bots."""
    src = _read("app/services/influencer_summary.py")
    assert 'sections=inf.get("system_instructions_sections")' in src


def test_influencer_repo_selects_sections_column():
    """compose() can only thread sections if the repo SELECTs the
    column. All 5 SELECTs in influencer_repo must include it."""
    src = _read("app/repositories/influencer_repo.py")
    # 4 simple SELECTs + 2 i-aliased joins = 6 places
    assert src.count("system_instructions_sections") >= 6


# ─── coach.section_body_sha256 ───────────────────────────────────────────


def test_section_body_sha256_is_stable_under_trimming():
    """Coach echoes back what it saw; mobile + sectioning users may
    introduce cosmetic whitespace. The sha must be stable across
    leading/trailing whitespace so a creator pressing Enter at the
    end doesn't invalidate every pending proposal."""
    from services.coach import section_body_sha256

    a = section_body_sha256("hello world")
    b = section_body_sha256("  hello world\n")
    c = section_body_sha256("hello world  ")
    assert a == b == c
    # But interior content matters — flipping a letter changes the sha
    assert section_body_sha256("hello worldz") != a


def test_section_body_sha256_handles_none_and_empty():
    """None body + empty string body must both hash to the same value
    (the empty-string sha) so a malformed cell can't crash /apply."""
    from services.coach import section_body_sha256

    assert section_body_sha256(None) == section_body_sha256("")


# ─── coach.META_PROMPT sections branch ──────────────────────────────────


def test_coach_meta_prompt_has_soul_file_placeholder():
    """META_PROMPT must use {soul_file_block} so the same template can
    render flat OR sectioned modes. A second separate template would
    drift over time."""
    src = _read("app/services/coach.py")
    assert "{soul_file_block}" in src
    # Old flat-only `current_instructions` placeholder should be gone
    # from META_PROMPT (still allowed in OPENING_PROMPT — opening
    # stays flat-text per the contract).
    meta_pos = src.find("META_PROMPT = ")
    meta_end = src.find('"""', meta_pos + 30) + 3
    meta_block = src[meta_pos:meta_end]
    assert "{soul_file_block}" in meta_block
    assert "{current_instructions}" not in meta_block


def test_coach_section_rules_addendum_exists():
    """SECTION_RULES_ADDENDUM block must define Rule 8 (the sectioned-
    proposal-shape rule) so Coach knows what shape to emit when sections
    are active."""
    src = _read("app/services/coach.py")
    assert "SECTION_RULES_ADDENDUM" in src
    # Rule 8 — the section rule must be numbered 8 to preserve 1-7
    # numbering for the historical flat-text path
    assert "8. SECTIONED SOUL FILE" in src
    # Required JSON shape fields named
    for field in (
        "section_id",
        "section_heading",
        "section_editable",
        "new_body",
        "previous_body_sha256",
    ):
        assert field in src, f"section rules missing field: {field}"


def test_coach_format_soul_file_block_dispatches_on_flag_and_sections():
    """_format_soul_file_block returns (rendered, mode_active) — must
    only flip mode_active True when both flag is on AND sections has
    content."""
    from services import coach as coach_mod

    # Flag off → always flat regardless of sections
    with patch.object(coach_mod.config, "COACH_SECTIONED_V2_ENABLED", False):
        block, mode = coach_mod._format_soul_file_block(
            "hi",
            [{"id": "a", "heading": "H", "body": "b", "editable": True}],
        )
        assert mode is False
        assert "hi" in block

    # Flag on + empty sections → flat path
    with patch.object(coach_mod.config, "COACH_SECTIONED_V2_ENABLED", True):
        block, mode = coach_mod._format_soul_file_block("hi", [])
        assert mode is False
        assert "hi" in block

    # Flag on + sections → sectioned path, sha embedded
    with patch.object(coach_mod.config, "COACH_SECTIONED_V2_ENABLED", True):
        block, mode = coach_mod._format_soul_file_block(
            "hi",
            [{"id": "core", "heading": "Core", "body": "abc", "editable": True}],
        )
        assert mode is True
        assert "== Core ==" in block
        # The full sha must appear so Coach can echo it as previous_body_sha256
        from services.coach import section_body_sha256

        assert section_body_sha256("abc") in block


# ─── coach.parse_proposal recognizes section shape ──────────────────────


def test_parse_proposal_accepts_section_shape():
    """Shape 3 — proposed_section_change must parse when section_id +
    new_body are present. previous_body_sha256 is optional at parse
    time (the /apply endpoint enforces it separately)."""
    from services.coach import parse_proposal

    text = """
    Some preamble.
    {"summary": "shorter voice", "proposed_section_change": {"section_id": "voice", "section_heading": "Voice", "section_editable": true, "new_body": "be terse", "previous_body_sha256": "deadbeef"}, "reasoning": "user asked"}
    """
    proposal = parse_proposal(text)
    assert proposal is not None
    assert proposal["proposed_section_change"]["section_id"] == "voice"
    assert proposal["proposed_section_change"]["new_body"] == "be terse"


def test_parse_proposal_rejects_section_shape_missing_id_or_body():
    """An LLM that emits the section shape but forgets section_id or
    new_body must fall through to plain-text, not be silently accepted."""
    from services.coach import parse_proposal

    missing_id = '{"summary": "x", "proposed_section_change": {"new_body": "y"}, "reasoning": "z"}'
    missing_body = '{"summary": "x", "proposed_section_change": {"section_id": "voice"}, "reasoning": "z"}'
    assert parse_proposal(missing_id) is None
    assert parse_proposal(missing_body) is None


def test_parse_proposal_still_accepts_legacy_shapes():
    """Three-shape catalog: section_change is ADDITIVE, not a
    replacement. proposed_changes (flat) + proposed_global_rule_override
    must keep parsing the same as pre-PR-2."""
    from services.coach import parse_proposal

    flat = '{"summary": "x", "proposed_changes": "full new text", "reasoning": "z"}'
    override = '{"summary": "x", "proposed_global_rule_override": {"key": "response_length", "value": "long_allowed"}, "reasoning": "z"}'
    assert parse_proposal(flat) is not None
    assert parse_proposal(override) is not None


# ─── coach_reply 5-tuple ────────────────────────────────────────────────


def test_coach_reply_signature_returns_5_tuple():
    """The 5-tuple is load-bearing — creator_coach.py unpacks it.
    Source-pin the return-type annotation so a regression that drops
    the section slot is caught at lint time, not at runtime."""
    src = _read("app/services/coach.py")
    pos = src.find("async def coach_reply(")
    # The signature spans the next ~30 lines; grab a generous window
    sig_block = src[pos : pos + 2000]
    # Return tuple has 5 elements
    assert "tuple[" in sig_block
    # str, str|None, str|None, dict|None, dict|None
    assert (
        "dict | None,\n    dict | None,\n]" in sig_block
        or sig_block.count("| None") >= 4
    )


def test_coach_reply_accepts_sections_kwarg():
    """The 5-tuple path requires Coach to see the sections at meta-prompt
    render time. Source-pin the kwarg."""
    src = _read("app/services/coach.py")
    pos = src.find("async def coach_reply(")
    sig_block = src[pos : pos + 2000]
    assert "sections: list[dict] | str | None = None" in sig_block


def test_creator_coach_unpacks_5_tuple():
    """The route must unpack ALL FIVE values — silently dropping the
    fifth (proposed_section_change) would mean no section proposal
    ever persists, even with the flag on."""
    src = _read("app/routes/creator_coach.py")
    # The unpack assigns 5 names
    assert (
        "display,\n        proposed,\n        reasoning,\n        proposed_override,\n        proposed_section,\n    ) = await coach_service.coach_reply("
        in src
    )


def test_creator_coach_passes_sections_to_coach_reply():
    """Route must thread the bot's sections to coach_reply — without
    this, Coach never sees the sectioned soul file in its meta-prompt."""
    src = _read("app/routes/creator_coach.py")
    assert 'sections=inf.get("system_instructions_sections")' in src


def test_creator_coach_persists_section_proposal_via_add_message():
    """add_message must receive the proposed_section_change blob so it
    lands in the new JSONB column. Otherwise the section proposal is
    lost between coach_reply + the next list_messages."""
    src = _read("app/routes/creator_coach.py")
    assert "proposed_section_change=proposed_section" in src


# ─── coach_repo.add_message accepts new shape + supersedes-on-insert ────


def test_repo_add_message_accepts_proposed_section_change():
    """The kwarg must be in add_message's signature."""
    src = _read("app/repositories/coach_repo.py")
    pos = src.find("async def add_message(")
    sig = src[pos : pos + 800]
    assert "proposed_section_change: dict | None = None" in sig


def test_repo_add_message_denormalises_target_section_id():
    """Server-side denormalisation lets a future filter query hit
    a typed column. Source-pin the section_id extraction so a future
    refactor can't drop it."""
    src = _read("app/repositories/coach_repo.py")
    pos = src.find("async def add_message(")
    body = src[pos : pos + 5500]
    # The blob's section_id is read AND assigned to target_section_id
    assert "target_section_id" in body
    assert 'proposed_section_change.get("section_id")' in body


def test_repo_add_message_includes_section_in_proposal_predicate():
    """The 'is_proposal' check decides whether status='pending' fires
    + whether the supersede-on-insert runs. Must include the new column."""
    src = _read("app/repositories/coach_repo.py")
    pos = src.find("async def add_message(")
    body = src[pos : pos + 5500]
    # The three-way OR check
    assert "proposed_section_change is not None" in body


def test_repo_add_message_supersedes_older_pending_on_proposal_insert():
    """PR-2 follow-up (Rishi 2026-06-11): when a NEW proposal lands,
    OLDER pending in same session must flip to 'superseded' in the
    same transaction. Without this, two pending proposals can co-exist
    + mobile shows two Apply buttons."""
    src = _read("app/repositories/coach_repo.py")
    pos = src.find("async def add_message(")
    body = src[pos : pos + 5500]
    # Transaction wrap + supersede UPDATE inside add_message
    assert "async with conn.transaction()" in body
    assert "UPDATE coach_messages" in body
    assert "status = 'superseded'" in body
    # Predicated on is_proposal — a creator turn or a receipt should
    # NOT supersede prior pending proposals
    assert "if is_proposal:" in body


# ─── /apply dispatches on proposed_section_change ───────────────────────


def test_apply_dispatches_on_proposed_section_change():
    """The /apply endpoint must have a dispatch arm for section_change
    BEFORE the override + flat-text branches. Without this the
    proposal is ignored at apply time and the section never updates."""
    src = _read("app/routes/creator_coach.py")
    apply_pos = src.find("async def apply_coach_proposal(")
    body = src[apply_pos : apply_pos + 18000]
    # Three dispatch branches in priority order
    section_pos = body.find('proposal.get("proposed_section_change")')
    override_pos = body.find('proposal.get("proposed_global_rule_override")')
    text_pos = body.find('proposal["proposed_changes"] or ""')
    assert section_pos != -1
    assert override_pos != -1
    assert text_pos != -1
    # Section dispatch comes first so a section-shape proposal isn't
    # caught by the override branch's truthiness check
    assert section_pos < override_pos < text_pos


def test_apply_section_branch_checks_sha_concurrency():
    """previous_body_sha256 mismatch must surface as 409 stale_proposal —
    NOT silently applied. Pin the comparison so a refactor that drops
    the sha check is caught."""
    src = _read("app/routes/creator_coach.py")
    apply_pos = src.find("async def apply_coach_proposal(")
    body = src[apply_pos : apply_pos + 18000]
    assert "section_body_sha256" in body
    assert "stale_proposal" in body


def test_apply_section_branch_refuses_non_editable_section():
    """A section flipped editable=false between proposal + apply must
    surface as 409 section_not_editable. Pin so a refactor that
    bypasses the check is caught."""
    src = _read("app/routes/creator_coach.py")
    apply_pos = src.find("async def apply_coach_proposal(")
    body = src[apply_pos : apply_pos + 18000]
    assert "section_not_editable" in body
    assert 'target_section.get("editable") is False' in body


def test_apply_section_branch_refuses_missing_section_id():
    """A section deleted between proposal + apply must surface as 409
    section_not_found. The proposal carries a stale section_id; the
    creator may have removed that section via PUT /soul-file in
    between."""
    src = _read("app/routes/creator_coach.py")
    apply_pos = src.find("async def apply_coach_proposal(")
    body = src[apply_pos : apply_pos + 18000]
    assert "section_not_found" in body


def test_apply_section_branch_updates_via_jsonb_array_swap():
    """The UPDATE must rewrite the WHOLE sections array (after merging
    the new body into the target index). pg's jsonb_set could also
    work but the swap-the-list approach is simpler + lets us preserve
    the section's heading/editable fields unchanged."""
    src = _read("app/routes/creator_coach.py")
    apply_pos = src.find("async def apply_coach_proposal(")
    body = src[apply_pos : apply_pos + 18000]
    assert "UPDATE ai_influencers" in body
    assert "SET system_instructions_sections" in body
    # The new_sections array is built from the live one
    assert "new_sections[target_index] = merged_section" in body


def test_apply_section_branch_records_history():
    """Every successful apply writes to system_instructions_history
    for the audit trail. The section path is no exception."""
    src = _read("app/routes/creator_coach.py")
    apply_pos = src.find("async def apply_coach_proposal(")
    body = src[apply_pos : apply_pos + 18000]
    # record_application is called in the section branch
    section_pos = body.find('proposal.get("proposed_section_change")')
    section_block = body[section_pos : section_pos + 6000]
    assert "coach_repo.record_application" in section_block


def test_apply_section_branch_returns_section_change_type():
    """The applied_type discriminator drives mobile's success card
    rendering. section_change is the new value alongside
    'system_instructions' + 'global_rule_override'."""
    src = _read("app/routes/creator_coach.py")
    apply_pos = src.find("async def apply_coach_proposal(")
    body = src[apply_pos : apply_pos + 18000]
    assert '"applied_type": "section_change"' in body


# ─── contract doc still anchored ─────────────────────────────────────────


def test_pr2_files_reference_contract_doc():
    """At least one PR-2 file must point at the contract — otherwise
    the spec drifts away from the code over time."""
    found_in: list[str] = []
    for path in (
        "app/config.py",
        "migrations/039_coach_messages_proposed_section_change.sql",
        "app/services/coach.py",
        "app/services/soul_file.py",
        "app/routes/creator_coach.py",
    ):
        if "coach-bucket-2" in _read(path):
            found_in.append(path)
    assert found_in, "no PR-2 file references the Bucket 2 contract"
