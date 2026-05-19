# ---------------------------------------------------------------------------
# app/models/__init__.py — package marker for Pydantic request/response models.
#
# ⭐ START HERE: this package holds the typed request + response
# Pydantic models the FastAPI routes parse + serialise. Each domain
# concept lives in its own file (`soul_file.py` for the layer
# models; future Day-5+ adds `composer_cache.py` etc.).
#
# WHY A SEPARATE `models` PACKAGE INSTEAD OF ONE `models.py`?
# Per F8 + B7 — each model file stays small enough to read top-to-
# bottom in one sitting. A single `models.py` would grow into a
# kitchen-sink as the surface expands. One file per concern keeps
# the doc burden + import graph honest.
#
# WHY PYDANTIC v2 + NOT SQLAlchemy ORM MODELS?
# Per the Day-4 directive verbatim: "no SQLAlchemy ORM — direct
# asyncpg + Pydantic models keeps the dep tree thin per A2.1." The
# repository layer maps asyncpg `Record` objects into these
# Pydantic models at the boundary; FastAPI serialises them to JSON
# at the HTTP boundary.
#
# WHAT DOES THIS FILE DO AT IMPORT?
# Nothing — Python uses the file's PRESENCE to mark `app/models/`
# as a package. No side effects.
#
# Today's contents:
#   soul_file.py  — `SoulFileLayer` (DB row) + `ComposedPromptResponse`
#                   (composer-/route-facing) + `UserSegment` literal type
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   soul_file.py             — DTOs for the soul-file-library surface
#   ../repository/soul_file_repository.py
#                            — builds `SoulFileLayer` from asyncpg `Record`
#   ../composer/four_layer_composer.py
#                            — assembles `ComposedPromptResponse`
#   ../api/composed_prompt_routes.py
#                            — serialises `ComposedPromptResponse` to JSON
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — chat-ai parity contract these models mirror
# ===========================================================================
