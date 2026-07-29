"""Phase 4.6 — user profile memory (identity facts go global)."""


def test_global_categories_includes_identity():
    """Identity category MUST be in GLOBAL_CATEGORIES — that's the whole
    point of Phase 4.6. If a future refactor accidentally drops it, this
    test fails loudly."""
    from services.memory import GLOBAL_CATEGORIES

    assert "identity" in GLOBAL_CATEGORIES


def test_global_categories_does_not_include_per_relationship_buckets():
    """Per-relationship categories must NOT be promoted to global, otherwise
    'user is excited about influencer A's new drop' leaks into influencer B's
    context."""
    from services.memory import GLOBAL_CATEGORIES

    for cat in ("emotional", "preferences", "goals", "context"):
        assert cat not in GLOBAL_CATEGORIES, f"{cat} should stay per-(user, influencer)"
