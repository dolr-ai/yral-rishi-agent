"""Phase 4 polish (Task 1) — memory recitation fix.

Pins the constants + the strengthened L4 anti-recitation prompt.
"""


def test_semantic_top_k_capped():
    """Top-K dropped from 8 to 3 — if a future refactor moves it back up,
    the bot starts leading with personal facts again (Motorola regression)."""
    from services.memory import SEMANTIC_TOP_K, SEMANTIC_SEARCH_BUFFER

    assert SEMANTIC_TOP_K == 3
    # Buffer must exceed top_k so the variety filter has slack
    assert SEMANTIC_SEARCH_BUFFER > SEMANTIC_TOP_K


def test_repeat_limit_constants_sane():
    """If MEMORY_REPEAT_LIMIT > MEMORY_HISTORY_DEPTH, the filter never triggers."""
    from services.session_memory import MEMORY_REPEAT_LIMIT, MEMORY_HISTORY_DEPTH

    assert 1 < MEMORY_REPEAT_LIMIT <= MEMORY_HISTORY_DEPTH


def test_soul_file_l4_prompt_strengthened():
    """The L4 block must contain the explicit anti-recitation language.
    A future refactor that softens it back to 'use naturally' would fail this."""
    from services.soul_file import compose

    out = compose(
        system_instructions="Be friendly.",
        category="companion",
        memories={"identity_name": "Rahul", "preferences_food": "biryani"},
    )
    # Must contain the strong negative-instruction phrases
    assert "NEVER lead with personal facts" in out
    assert "I remember you said" in out  # negative example
    assert "you mentioned X before" in out  # negative example
    assert "recitation" in out
