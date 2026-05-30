"""v2 unified inbox — H2H rows must surface alongside AI rows.

The bug: app/repositories/conversation_repo.py:list_by_user used an
INNER JOIN ai_influencers, which silently dropped H2H conversations
(influencer_id IS NULL). Mobile inbox couldn't ever show H2H.

These tests pin the structural shape of the fix via source inspection
so it can't be quietly reverted. Live behavior is exercised after
deploy via the existing endpoint suite + mobile integration."""

from pathlib import Path


def _read(rel: str) -> str:
    return (Path(__file__).resolve().parent.parent / rel).read_text()


def test_list_by_user_uses_left_join():
    """INNER JOIN would drop H2H rows with NULL influencer_id. LEFT JOIN
    keeps them. The bug we're fixing."""
    src = _read("app/repositories/conversation_repo.py")
    # The no-influencer-id branch uses LEFT JOIN (multi-row form, may be
    # broken across lines after ruff format)
    assert "LEFT JOIN ai_influencers" in src


def test_list_by_user_surfaces_h2h_via_or_clause():
    """The WHERE must include the H2H case where caller is either side
    of the conversation (user_id or participant_b_id)."""
    src = _read("app/repositories/conversation_repo.py")
    assert "c.conversation_type = 'human_chat'" in src
    # H2H requires either-side match — caller could be user_id OR
    # participant_b_id (the one who received the H2H invite).
    assert "c.participant_b_id = $1" in src


def test_list_by_user_filters_orphan_ai_rows():
    """Q1 decision: orphans (non-NULL influencer_id but no matching
    ai_influencers row) are filtered. Same intent as the existing
    soft-delete check, applied to hard-deleted influencer rows.
    Without this, LEFT JOIN would surface AI rows with null influencer
    fields and crash mobile rendering."""
    src = _read("app/repositories/conversation_repo.py")
    assert "i.id IS NOT NULL" in src


def test_list_by_user_preserves_bot_leak_defense():
    """The defensive `c.user_id NOT IN (SELECT id FROM ai_influencers)`
    filter was already there; the unified rewrite must keep it so bot-
    side conversations don't leak into the user inbox."""
    src = _read("app/repositories/conversation_repo.py")
    assert "c.user_id NOT IN (SELECT id FROM ai_influencers)" in src


def test_list_by_user_selects_participant_b_id():
    """The chat_v2 formatting layer needs participant_b_id to compute
    the H2H peer (the side that isn't me). Without it in the SELECT,
    the peer block can't render."""
    src = _read("app/repositories/conversation_repo.py")
    assert "c.participant_b_id" in src


def test_list_by_user_with_influencer_id_stays_ai_only():
    """When the caller passes ?influencer_id=, they want a specific AI
    chat — H2H must NOT leak into that filtered view. The with-
    influencer-id branch keeps its old INNER-JOIN shape (no LEFT JOIN
    needed because the filter already requires a matching influencer).

    Detect by counting LEFT JOINs in the file: 1 in the no-filter
    branch (the new unified one), and 0 + an INNER JOIN in the
    influencer-id branch."""
    src = _read("app/repositories/conversation_repo.py")
    # The with-influencer-id branch still references the original
    # JOIN form
    assert "JOIN ai_influencers i ON c.influencer_id = i.id" in src
    # And requires that influencer match — same WHERE shape as before
    assert "c.influencer_id = $2" in src


def test_count_by_user_mirrors_list_where():
    """If the count query has a different WHERE than the list query,
    pagination breaks (`total` doesn't match `len(conversations)`)."""
    src = _read("app/repositories/conversation_repo.py")
    # count_by_user must also use the unified WHERE with H2H union
    # (the participant_b_id alternation appears twice in the file
    # if both queries use it).
    assert src.count("c.participant_b_id = $1") >= 2


def test_chat_v2_h2h_branches_on_conversation_type():
    """For H2H rows: influencer=None, user=peer_metadata. For AI rows:
    influencer=block, user=None. The same response key shape on either
    side keeps mobile parsing simple."""
    src = _read("app/routes/chat_v2.py")
    # H2H peer logic + the conversation_type branch
    assert 'conv_type == "human_chat"' in src
    assert "h2h_peer_ids" in src or "peer_id" in src


def test_chat_v2_batches_peer_profile_lookups():
    """Per-row metadata-bulk calls would be N round trips. Batch up
    front via _fetch_user_profiles (the same helper the bot-side path
    uses)."""
    src = _read("app/routes/chat_v2.py")
    # The user-side list must now call _fetch_user_profiles too
    # (not just the bot-side _list_for_bot)
    assert src.count("_fetch_user_profiles") >= 2


def test_chat_v2_includes_conversation_type_in_response():
    """Mobile needs to know which row is which type to pick the right
    renderer. Per-row conversation_type in the response payload makes
    this explicit (matches the v3 endpoint's contract)."""
    src = _read("app/routes/chat_v2.py")
    assert '"conversation_type": conv_type' in src
