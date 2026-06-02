"""Tests for Phase 4.4 — embedding service shape + memory_repo vector helpers."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_memory_to_embed_text_shape():
    """Embed-text format is stable — same format used at write AND query time
    must produce embeddings in the same vector space."""
    from services.embeddings import memory_to_embed_text

    assert memory_to_embed_text("identity", "name", "Rahul") == "identity: name = Rahul"
    assert (
        memory_to_embed_text("preferences", "favorite_food", "biryani")
        == "preferences: favorite_food = biryani"
    )


def test_embedding_dim_constant():
    """Gemini text-embedding-004 is 768-dim. If this changes, the column
    type in migration 008 must change too — and all existing embeddings
    become invalid."""
    from services.embeddings import EMBEDDING_DIM

    assert EMBEDDING_DIM == 768


def test_vector_literal_format():
    """asyncpg sends vectors as text; pgvector parses '[a,b,c]'.
    A wrong format here breaks every memory write."""
    from repositories.memory_repo import _vector_literal

    assert _vector_literal(None) is None
    assert _vector_literal([1.0, 2.5, -0.3]) == "[1.000000,2.500000,-0.300000]"
    # Trim trailing zeros to 6dp (regression guard against scientific notation)
    assert "e" not in _vector_literal([1e-5, 2e10])
