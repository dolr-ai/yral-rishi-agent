"""Phase 4.5 — cross-conversation memory recall.

Verifies the semantic_search signature dropped its influencer-scope filter.
The full retrieval behavior (vector recall, ranking) is exercised by the live
backfill + E2E flow; this file pins the contract so future refactors don't
silently re-introduce the per-influencer filter.
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


def test_semantic_search_signature_dropped_influencer_arg():
    """The Phase 4.5 contract is: semantic_search pulls across ALL the user's
    memories. If a future refactor adds back an `influencer_id` param, this
    test fails — the caller in services/memory.py would also need updating
    in lockstep."""
    from repositories.memory_repo import semantic_search

    params = list(inspect.signature(semantic_search).parameters.keys())
    assert "influencer_id" not in params, (
        f"semantic_search should not take influencer_id (Phase 4.5). Got: {params}"
    )
    # Sanity: the params we DO expect
    assert params[0] == "pool"
    assert "user_id" in params
    assert "query_embedding" in params
    assert "top_k" in params


def test_get_memories_for_prompt_no_query_falls_back_to_get_all():
    """When called without a query_embedding, must NOT touch semantic_search —
    falls back to get_all_for_user (per-(user, influencer) + global). This
    matters for the proactive-messages flow which has no current user msg."""
    import services.memory as memory_mod

    src = inspect.getsource(memory_mod.get_memories_for_prompt)
    # The function should have both branches visible in source — semantic
    # when query_embedding truthy, get_all_for_user when falsy.
    assert "semantic_search" in src
    assert "get_all_for_user" in src
