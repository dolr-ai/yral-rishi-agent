"""Coach Bucket 2 PR-3 — GET + PUT /api/v1/influencers/{bot_id}/soul-file.

Source-pin tests (fastapi + httpx aren't in the local venv so endpoint-
level smoke runs in CI / prod, not here — matches the repo's offline-
test discipline established in test_llm_routing_admin.py +
test_21ab_H10_backup_health.py).

Companion to PR-1 (migration 038 source-pin) + PR-2 (compose/Coach/
dispatch source-pin).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOUL_FILE_ROUTE = REPO / "app" / "routes" / "soul_file.py"


def _read(rel: str) -> str:
    return (REPO / rel).read_text()


# ─── route registration + auth ──────────────────────────────────────────


def test_soul_file_route_file_exists():
    assert SOUL_FILE_ROUTE.exists(), "PR-3 route file missing"


def test_main_wires_soul_file_router():
    src = _read("app/main.py")
    assert "from routes.soul_file import router as soul_file_router" in src
    assert "app.include_router(soul_file_router)" in src


def test_routes_under_api_v1_influencers_prefix():
    """Both GET + PUT live under /api/v1/influencers/{bot_id}/soul-file —
    sibling to the existing /system-prompt endpoint so mobile shares
    the auth/CORS surface."""
    src = SOUL_FILE_ROUTE.read_text()
    assert 'prefix="/api/v1"' in src
    assert '@router.get("/influencers/{bot_id}/soul-file")' in src
    assert '@router.put("/influencers/{bot_id}/soul-file")' in src


def test_both_routes_owner_gated():
    """Owner-gate via parent_principal_id — same pattern as
    /system-prompt. Returns 403 on non-owner, 404 on missing bot."""
    src = SOUL_FILE_ROUTE.read_text()
    assert "_load_owned_influencer" in src
    # Both GET + PUT call the shared owner-gate helper
    assert src.count("await _load_owned_influencer(pool, user_id, bot_id)") >= 2
    # The helper itself does the 403 + 404 distinction
    helper_pos = src.find("async def _load_owned_influencer(")
    helper_block = src[helper_pos : helper_pos + 1000]
    assert "status_code=404" in helper_block
    assert "status_code=403" in helper_block
    assert "parent_principal_id" in helper_block


# ─── GET fallback shape ─────────────────────────────────────────────────


def test_get_returns_fallback_when_sections_empty():
    """Bots without sections (the today-state for the 3,941 existing
    rows) must return a single synthetic section + fallback_to_flat=true
    per #361 mobile-expert refinement."""
    src = SOUL_FILE_ROUTE.read_text()
    assert '"fallback_to_flat":' in src
    assert "_build_fallback_section" in src
    # Fixed id + heading per the contract
    assert '"core_personality"' in src
    assert '"Core personality"' in src


def test_get_returns_sections_version_sha256():
    """Mobile uses the sha as the optimistic-concurrency handle for
    PUT. Source-pin the field."""
    src = SOUL_FILE_ROUTE.read_text()
    assert '"sections_version_sha256":' in src
    assert "_canonical_sections_sha256" in src


def test_get_returns_display_name_for_card_rendering():
    """Mobile renders 'Edit Soul File for Tara' headers — the display
    name must be in the response so it doesn't need a separate GET
    /influencers/{id} round trip."""
    src = SOUL_FILE_ROUTE.read_text()
    assert '"display_name":' in src


# ─── PUT validation (422 surface) ───────────────────────────────────────


def test_put_requires_expected_sha():
    """Without expected_sections_version_sha256 PUT has no way to detect
    drift — must 422 instead of silently overwriting."""
    src = SOUL_FILE_ROUTE.read_text()
    assert "expected_sections_version_sha256" in src
    assert "missing_expected_sha" in src


def test_put_validates_section_shape():
    """Validation surface must cover the contract's hard rules + return
    a typed `error` key per case so mobile can map to UX messages."""
    src = SOUL_FILE_ROUTE.read_text()
    for code in (
        "sections_not_a_list",
        "too_many_sections",
        "section_not_an_object",
        "bad_section_id",
        "duplicate_section_id",
        "empty_section_body",
    ):
        assert code in src, f"validation error code missing: {code}"


def test_put_caps_at_8_sections():
    """Per contract §9 #4 — input-token budget on Gemini + UI ergonomics.
    Cap enforced server-side so a misbehaving client can't bypass."""
    src = SOUL_FILE_ROUTE.read_text()
    assert "_MAX_SECTIONS = 8" in src


def test_put_section_id_regex_is_slug():
    """Lowercase snake_case slug, starts with letter, 2-64 chars.
    Pin so a future refactor can't drift the canonical id shape."""
    src = SOUL_FILE_ROUTE.read_text()
    assert "^[a-z][a-z0-9_]{1,63}$" in src


def test_put_normalises_missing_heading_and_editable():
    """Missing heading → Title-Case of id; missing editable → True.
    Forward-compat for older mobile clients that ship only id + body."""
    src = SOUL_FILE_ROUTE.read_text()
    # The Title-Case normalisation
    assert 'replace("_", " ").title()' in src
    # editable default
    assert "editable = True" in src


def test_put_drops_unknown_fields():
    """Validator returns ONLY the 4 contract fields (id, heading, body,
    editable). A future mobile field (e.g. `icon`) gets dropped at the
    server boundary so the DB stays clean."""
    src = SOUL_FILE_ROUTE.read_text()
    # The validator's normalised return only carries the 4 fields
    pos = src.find("def _validate_sections_shape(")
    end = src.find("def ", pos + 30)
    body = src[pos:end]
    # The return shape lists only these four keys
    for key in ('"id":', '"heading":', '"body":', '"editable":'):
        assert key in body, f"validator should set {key}"


# ─── PUT 409 reconciliation contract (#361 refinement) ──────────────────


def test_put_409_carries_current_state_for_reconciliation():
    """Per #361 mobile-expert refinement: 409 stale_sections must embed
    the CURRENT sections + sha so mobile drives the Reload dialog
    without a re-GET round trip."""
    src = SOUL_FILE_ROUTE.read_text()
    # Locate the actual 409 raise (skip the docstring mention at top)
    pos = src.find("status_code=409")
    assert pos != -1
    branch = src[pos : pos + 2000]
    assert '"error": "stale_sections"' in branch
    assert "current_sections" in branch
    assert "current_sections_version_sha256" in branch


def test_put_409_uses_canonical_sha_against_current_view():
    """The sha comparison must use the SAME canonicalised view that
    GET surfaces — including the synthetic fallback section when the
    bot has no real sections. Otherwise the FIRST PUT after a creator
    edits the fallback always 409s because mobile's sha is over the
    synthetic section, not over the empty array."""
    src = SOUL_FILE_ROUTE.read_text()
    put_pos = src.find("async def put_soul_file(")
    put_block = src[put_pos : put_pos + 6000]
    # The PUT computes current_for_sha as either current_sections OR
    # a list containing the fallback section
    assert "current_for_sha" in put_block
    assert "_build_fallback_section" in put_block


# ─── canonical sha implementation ───────────────────────────────────────


def test_canonical_sha_uses_sort_keys_and_compact_separators():
    """The whole point of "canonical" sha is byte-stability under input
    key reordering + whitespace. Pin the json.dumps args so a future
    refactor that switches to indent=2 or drops sort_keys=True is
    caught here — that change would silently break every mobile
    client whose stored sha would no longer match the server."""
    src = SOUL_FILE_ROUTE.read_text()
    fn_pos = src.find("def _canonical_sections_sha256(")
    fn_block = src[fn_pos : fn_pos + 800]
    assert "sort_keys=True" in fn_block
    assert 'separators=(",", ":")' in fn_block
    assert "hashlib.sha256" in fn_block


# ─── PUT write path ─────────────────────────────────────────────────────


def test_put_writes_jsonb_via_update():
    src = SOUL_FILE_ROUTE.read_text()
    assert "UPDATE ai_influencers" in src
    assert "SET system_instructions_sections" in src
    assert "updated_at = NOW()" in src


def test_put_returns_new_sha_in_response():
    """Successful PUT must return the new sha so mobile can store it
    + use on the next PUT without a re-GET."""
    src = SOUL_FILE_ROUTE.read_text()
    put_pos = src.find("async def put_soul_file(")
    put_block = src[put_pos : put_pos + 6000]
    # The new sha is computed + returned
    assert "new_sha = _canonical_sections_sha256(new_sections)" in put_block
    assert '"sections_version_sha256": new_sha' in put_block


# ─── contract anchoring ────────────────────────────────────────────────


def test_soul_file_route_references_contract():
    src = SOUL_FILE_ROUTE.read_text()
    assert "coach-bucket-2-sections-contract.md" in src
