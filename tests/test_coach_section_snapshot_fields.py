"""PR #361 contract refinement — section_heading + section_editable snapshots.

PR #366 shipped the `proposed_section_change` shape against the original
#357 contract; #361's refinement (snapshot fields for mobile's badge +
Apply-button gating) wasn't yet captured. This test file pins the three
fixes the audit identified:

  1. META_PROMPT Rule 8 explicitly requires section_heading +
     section_editable (was "are SNAPSHOTS" — informational; now MUST be a
     snapshot).
  2. Defensive injection: when Coach forgets either snapshot,
     `_ensure_section_snapshots` looks the section up on the live
     `ai_influencers.system_instructions_sections` array and fills the
     missing field before persistence.
  3. `_format_message` surfaces `proposed_section_change` on the GET
     /messages response (was dropping it on the floor previously).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── 1. META_PROMPT explicit "MUST" wording ─────────────────────────────


def test_meta_prompt_requires_all_five_section_fields():
    """The Rule 8 block must say all five fields are REQUIRED so an LLM
    skim-reading the prompt doesn't treat the snapshots as optional."""
    src = _read("app/services/coach.py")
    pos = src.find("8. SECTIONED SOUL FILE")
    rule_block = src[pos : pos + 3000]
    assert "ALL FIVE fields" in rule_block or "all five" in rule_block.lower()
    # Each load-bearing snapshot field carries an explicit MUST
    assert "section_heading MUST be" in rule_block
    assert "section_editable MUST be" in rule_block


def test_meta_prompt_documents_mobile_use_for_each_snapshot():
    """Explain WHY each snapshot matters so an LLM that's tempted to
    drop fields sees the consequence. The wording references mobile's
    badge + Apply-button gating per the contract."""
    src = _read("app/services/coach.py")
    pos = src.find("8. SECTIONED SOUL FILE")
    rule_block = src[pos : pos + 3000]
    # Badge text appears once
    assert "badge" in rule_block.lower()
    # Apply-button gating mentioned
    assert "Apply button" in rule_block


# ─── 2. Defensive injection from live state ─────────────────────────────


def test_ensure_section_snapshots_helper_exists():
    """The route file owns the helper since it has direct access to the
    `inf` (ai_influencers row) the live sections live on. Source-pin
    the helper so a future refactor doesn't silently move + break it."""
    src = _read("app/routes/creator_coach.py")
    assert "def _ensure_section_snapshots(" in src


def test_send_coach_message_calls_ensure_snapshots_before_persist():
    """The injection MUST run BETWEEN coach_reply returning and
    add_message persisting — otherwise the JSONB column ends up missing
    fields and mobile sees an incomplete blob via GET /messages."""
    src = _read("app/routes/creator_coach.py")
    pos = src.find("async def send_coach_message(")
    # Window 14000 — the send_coach_message handler spans the action-verb
    # short-circuit, recent-conv grounding fetch, force_proposal flag, the
    # 5-tuple coach_reply unpack, the snapshot injection (this PR), and the
    # add_message call. ~10k chars from start to add_message.
    body = src[pos : pos + 14000]
    inject_pos = body.find("_ensure_section_snapshots(")
    persist_pos = body.find("proposed_section_change=proposed_section")
    assert inject_pos != -1, "snapshot injection missing in send_coach_message"
    assert persist_pos != -1
    assert inject_pos < persist_pos, (
        "injection must run BEFORE add_message — otherwise the JSONB "
        "column persists an incomplete blob"
    )


def test_ensure_snapshots_only_fills_missing_fields():
    """Coach-emitted heading/editable stays intact when present. The
    contract treats snapshots as "what Coach read," which can legit
    differ from the current live row if the creator renamed mid-session.
    Source-pin the `needs_*` guards so a future refactor can't
    accidentally overwrite Coach's values."""
    src = _read("app/routes/creator_coach.py")
    fn_pos = src.find("def _ensure_section_snapshots(")
    fn_block = src[fn_pos : fn_pos + 3000]
    # Both guard names present
    assert "needs_heading" in fn_block
    assert "needs_editable" in fn_block
    # Both guards are checked before assignment — pin the structure
    assert "if needs_heading" in fn_block
    assert "if needs_editable" in fn_block


def test_ensure_snapshots_is_safe_on_missing_or_malformed_inputs():
    """Edge cases the helper must survive: section_id missing/bad, live
    sections column NULL, JSON-string column, no matching section_id.
    Source-pin the early-return + JSONB-string coercion paths."""
    src = _read("app/routes/creator_coach.py")
    fn_pos = src.find("def _ensure_section_snapshots(")
    fn_block = src[fn_pos : fn_pos + 3000]
    # Bad section_id → return blob unchanged
    assert "not isinstance(section_id, str)" in fn_block
    # JSON-string column handled
    assert "isinstance(live_sections, str)" in fn_block
    # Non-list live_sections → bail
    assert "not isinstance(live_sections, list)" in fn_block


def test_ensure_snapshots_documents_apply_authority():
    """Helper docstring must call out that live row stays authoritative
    at apply time — snapshots are purely a render-time convenience.
    Without this comment a future maintainer might assume the snapshots
    drive apply behavior + introduce a sha mismatch bug."""
    src = _read("app/routes/creator_coach.py")
    fn_pos = src.find("def _ensure_section_snapshots(")
    fn_block = src[fn_pos : fn_pos + 3000]
    # Authority language
    assert "authoritative" in fn_block.lower()
    # Render-time convenience framing
    assert "render-time" in fn_block.lower() or "render time" in fn_block.lower()


# ─── 3. _format_message surfaces proposed_section_change ────────────────


def test_format_message_surfaces_proposed_section_change():
    """Pre-fix the JSONB column stored the blob fine but the GET
    /messages response dropped it on the floor — mobile never saw the
    snapshot it needed to render the badge. Source-pin both halves:
    the key in the return dict + the JSONB-string coercion (asyncpg's
    codec gives JSONB-strings on some setups)."""
    src = _read("app/routes/creator_coach.py")
    fmt_pos = src.find("def _format_message(")
    fmt_block = src[fmt_pos : fmt_pos + 3000]
    # The key appears in the return dict
    assert '"proposed_section_change": section_change' in fmt_block
    # JSONB-string coercion present (mirrors the override handling)
    assert 'm.get("proposed_section_change")' in fmt_block
    assert "section_change = _json.loads(section_change)" in fmt_block


def test_format_message_surface_is_documented():
    """The "why" — mobile renders the badge from the snapshot — needs
    to live next to the code so a future maintainer doesn't drop the
    key thinking it's redundant with proposed_changes/override."""
    src = _read("app/routes/creator_coach.py")
    fmt_pos = src.find("def _format_message(")
    fmt_block = src[fmt_pos : fmt_pos + 3000]
    assert "badge" in fmt_block.lower()


# ─── End-to-end: snapshot survives the whole pipeline ──────────────────


def test_snapshot_round_trip_pin():
    """Belt-and-braces: pin the three contract refinement touchpoints
    so a future refactor that touches any single one runs into this
    test instead of breaking the mobile contract silently."""
    coach_src = _read("app/services/coach.py")
    route_src = _read("app/routes/creator_coach.py")
    # 1. META_PROMPT mentions both snapshot fields with MUST language
    assert "section_heading MUST be" in coach_src
    assert "section_editable MUST be" in coach_src
    # 2. Helper exists in route file
    assert "_ensure_section_snapshots" in route_src
    # 3. _format_message exposes the column via the response shape
    assert '"proposed_section_change":' in route_src
