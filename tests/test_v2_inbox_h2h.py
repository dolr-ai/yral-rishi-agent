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


# ─── _can_access_conversation: H2H recipient bug fix (2026-06-01) ─────────
#
# Bug: PR #228 expanded H2H access on the inbox LIST endpoint
# (GET /api/v2/chat/conversations) but didn't propagate the same expansion
# to the access helper that gates the DETAIL endpoints in app/routes/chat.py
# (GET /messages, POST /read, POST /messages/stream, POST /images).
# Recipient saw the row + preview but tapping it returned 403.
# Fix: also honor conv["participant_b_id"] in the helper.


def test_can_access_conversation_honors_participant_b():
    """The detail-endpoint gate must let the H2H recipient through, not
    just the creator + AI principal + influencer parent. Without this
    branch, the recipient saw the row in their inbox but tapping it
    returned 403 from /messages, /read, /messages/stream, and /images
    (all four share this helper)."""
    src = _read("app/routes/chat.py")
    assert 'conv.get("participant_b_id") == user_id' in src


def test_can_access_conversation_helper_still_used_by_messages_endpoint():
    """Sanity pin: the fix only helps if the GET /messages endpoint
    actually calls this helper. If a future refactor inlines or replaces
    the check, the recipient bug returns silently."""
    src = _read("app/routes/chat.py")
    assert "_can_access_conversation" in src
    # The helper must be awaited at the /messages route (line ~278)
    assert "await _can_access_conversation(" in src


# ─── unread_count subquery: H2H recipient bug fix (2026-06-02) ────────────
#
# Second PR #228 trailing-edge bug. The list_by_user unread_count subquery
# filtered m2.role = 'assistant'. H2H messages all have role='user' from
# both peers → count always 0 for H2H rows → recipient's pink badge never
# appeared despite real unread messages. The fix branches on
# conversation_type — AI keeps role='assistant', H2H uses sender_id != $1
# (the viewer principal already bound to $1 for the outer WHERE clause).


def test_list_by_user_unread_count_branches_on_conversation_type():
    """The subquery must handle both AI (role='assistant') and H2H
    (sender_id != viewer) — picking the right unread criterion per
    conversation_type. Without this branch, the H2H recipient's badge
    stays at 0 regardless of actual unread state."""
    src = _read("app/repositories/conversation_repo.py")
    # Both branches present in the same subquery
    assert "c.conversation_type = 'ai_chat'" in src
    assert "c.conversation_type = 'human_chat'" in src
    # H2H branch keys off sender_id != viewer (the viewer principal is $1)
    assert "m2.sender_id != $1" in src
    # AI branch keeps the original role-based filter
    assert "m2.role = 'assistant'" in src


def test_list_by_user_unread_count_does_not_count_own_sends_for_h2h():
    """A self-sent H2H message must never count toward the sender's own
    unread badge. The sender_id != $1 clause is what enforces this — pin
    that the inequality (not equality) ships."""
    src = _read("app/repositories/conversation_repo.py")
    # Inequality form must ship (not "= $1")
    assert "m2.sender_id != $1" in src


# ─── count_unread + mark_as_read: H2H Part B grep-sweep finding (2026-06-02) ──
#
# Third PR #228 trailing-edge bug surfaced by the Part B grep sweep.
# message_repo.count_unread + mark_as_read filtered role='assistant'.
# H2H peers both send role='user', so for H2H:
#  - count_unread always returned 0 (POST /read response, H2H send WS broadcast)
#  - mark_as_read no-op'd (POST /conversations/{id}/read for H2H recipient)
#
# Fix: unified semantic "unread = messages not sent by the viewer."
# Both functions take a viewer_principal parameter and filter
# sender_id != $viewer. Works for both AI (bot's sender_id != user_id)
# and H2H (peer's sender_id != viewer). No conversation_type branch
# needed — the sender_id comparison handles both cases naturally.


def test_count_unread_uses_sender_id_filter_not_role():
    """The old role='assistant' filter must NOT be present anymore in
    count_unread — that's exactly what made H2H always return 0."""
    src = _read("app/repositories/message_repo.py")
    # Find the count_unread function body and check the filter shape
    # (we can't easily extract just one function body in a source-pin
    # test, so we assert at the file level — sender_id != $2 must be
    # present at least twice, once in count_unread and once in
    # mark_as_read).
    assert src.count("sender_id != $2") >= 2


def test_count_unread_signature_takes_viewer_principal():
    """The new viewer_principal parameter is what scopes the filter
    correctly per caller. Without it, all callers would pass nothing
    and the function would error out — that's the desired regression
    catch."""
    src = _read("app/repositories/message_repo.py")
    assert "async def count_unread(pool, conversation_id: str, viewer_principal: str)" in src
    assert "async def mark_as_read(pool, conversation_id: str, viewer_principal: str)" in src


def test_callers_pass_correct_viewer_to_unread_helpers():
    """All 5 known callers must pass the right viewer principal. If a
    future refactor drops the second arg, this test catches it."""
    chat_src = _read("app/routes/chat.py")
    human_src = _read("app/routes/human_chat.py")
    takeover_src = _read("app/routes/creator_takeover.py")
    proactive_src = _read("app/services/proactive.py")

    # POST /read: viewer is the caller (user_id)
    assert "message_repo.mark_as_read(pool, conversation_id, user_id)" in chat_src
    assert "message_repo.count_unread(pool, conversation_id, user_id)" in chat_src
    # H2H send: viewer is the recipient (the other peer)
    assert "message_repo.count_unread(pool, conversation_id, recipient_id)" in human_src
    # Creator takeover: viewer is the user (conv["user_id"])
    assert 'message_repo.count_unread(pool, conversation_id, conv["user_id"])' in takeover_src
    # Proactive AI nudge: viewer is the user (recipient of AI message)
    assert "message_repo.count_unread(pool, conversation_id, user_id)" in proactive_src
