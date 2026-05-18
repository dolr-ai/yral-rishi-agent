# ---------------------------------------------------------------------------
# app/safety/__init__.py — package marker for safety-canned response helpers.
#
# ⭐ START HERE: this package holds the user-facing copy that the safety
# stack (H5 prompt-injection / H4 crisis / A10 NSFW) returns when it
# short-circuits the run_turn handler. Keeping the copy in ONE place
# means Day-5+ real LLM enablement can swap the underlying detector
# while the user-facing text stays canonical.
#
# Today's contents:
#   canned_responses.py  — three functions, one per safety layer
#
# WHY A SEPARATE `safety/` PACKAGE INSTEAD OF FOLDING INTO `middleware/`?
# Per the Session-4 Day-3 directive: "Canned message text in
# app/safety/canned_responses.py (one module, three functions).
# Copy-paste-able by Day-5 real LLM enablement." The split keeps the
# COPY (what the user sees) separate from the DETECTION (how we decide
# to block). The two evolve on different cadences:
#   - DETECTION moves from rule-based (Day 3) → ML classifier (Phase 2)
#     → external content-safety-and-moderation RPC (later phase).
#   - COPY changes via product/UX iteration regardless of detector
#     version (product owns the helpline numbers + tone-of-voice text).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   canned_responses.py        — the three response-builder functions
#   ../middleware/h5_prompt_injection.py
#                              — consumer (calls prompt_injection_blocked)
#   ../middleware/h4_crisis_detection.py
#                              — consumer (calls crisis_response)
#   ../middleware/a10_nsfw_filter.py
#                              — consumer (calls nsfw_blocked)
#   ../models/turn.py          — MessageDto shape these responses must match
# ===========================================================================
