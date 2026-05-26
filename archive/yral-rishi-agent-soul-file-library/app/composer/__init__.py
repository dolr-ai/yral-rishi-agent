# ---------------------------------------------------------------------------
# app/composer/__init__.py — package marker for the Soul File composer.
#
# ⭐ START HERE: this package holds the 4-layer Soul File composer —
# the function that turns four database rows (L1 global + L2
# archetype + L3 per-influencer + L4 per-user-segment) into ONE
# byte-stable prompt prefix the orchestrator hands to the LLM
# provider per chat turn.
#
# WHY A SEPARATE PACKAGE AND NOT A SINGLE FILE?
# The composer is the load-bearing module of this service — the
# byte-identity engineering contract (per
# `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md`) lives here. Putting it
# in its own package draws a clear boundary the test suite + future
# Redis-cache layer (Day-5+) wrap around without polluting the
# `app/` top-level namespace.
#
# WHAT DOES THIS FILE DO AT IMPORT?
# Nothing — Python uses the file's PRESENCE to mark `app/composer/`
# as a package. No side effects.
#
# Today's contents:
#   four_layer_composer.py  — `compose(influencer_id, user_segment)`
#                              + `InfluencerSoulFileMissingError`
#                              + `SoulFileDataIntegrityError`
#                              + module-load `LAYER_SEPARATOR` load
#
# Day-5+ adds: a Redis cache wrapper that short-circuits the DB
# reads on cache hit + flips the `cache_hit` flag to True in the
# ComposedPromptResponse.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   four_layer_composer.py    — the only module in this package
#   ../repository/soul_file_repository.py
#                              — the data source the composer reads from
#   ../models/soul_file.py    — `ComposedPromptResponse` the composer returns
#   ../api/composed_prompt_routes.py
#                              — primary consumer (HTTP route delegates here)
#   ../../shared-config.yaml  — `LAYER_SEPARATOR` value loaded at import
#   ../../PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md
#                              — byte-identity engineering contract
#   ../../tests/test_composer.py
#                              — byte-identity × 5 reps + golden-file diff
# ===========================================================================
