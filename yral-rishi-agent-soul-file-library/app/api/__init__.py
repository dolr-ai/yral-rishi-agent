# ---------------------------------------------------------------------------
# app/api/__init__.py — package marker for FastAPI route modules.
#
# ⭐ START HERE: this package holds the HTTP-route handlers Session 3's
# public-api + future internal callers (orchestrator, Prompt-Coach,
# etc.) reach the soul-file-library through. Each handler lives in its
# own file; this package's job is to be the import boundary that
# `app/main.py` mounts via `include_router`.
#
# WHY A SEPARATE `api` PACKAGE INSTEAD OF ONE `routes.py`?
# Three reasons:
#   1. Per-route review surface — adding a new endpoint is one new
#      file (`composed_prompt_routes.py` + future `soul_file_crud_routes.py`
#      etc.) rather than appending to a growing `routes.py` everyone
#      fights over in merges.
#   2. Per-route doc burden — each module gets its own B7 header with
#      the per-endpoint contract pointer + Pydantic-model imports,
#      keeping reviewers focused on ONE route at a time.
#   3. Per-route test discovery — `tests/test_api_<name>.py` mirrors
#      the route filename one-to-one (today: `test_api_composed_prompt.py`
#      mirrors `composed_prompt_routes.py`).
#
# WHAT DOES THIS FILE DO AT IMPORT?
# Nothing — Python uses the file's PRESENCE to mark `app/api/` as a
# package (so `from app.api.composed_prompt_routes import router`
# works). No side effects, no imports, no top-level code beyond this
# header. Routers get REGISTERED on the FastAPI app in `app/main.py`
# via `include_router(...)`; this file does NOT auto-collect them.
#
# Today's contents:
#   composed_prompt_routes.py  — `GET /composed-prompt` route handler
#                                (Day-4); delegates to the 4-layer
#                                composer + maps composer exceptions to
#                                HTTP 404 (no L3 row) / 500 (L1/L2/L4
#                                data integrity).
#
# Day-5+ adds: write-side routes when the Prompt-Coach service lands
# (auth'd PATCH endpoints for L3 edits per the agent definition's
# Day-13-15 plan).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   composed_prompt_routes.py — `GET /composed-prompt` — the Day-4 route
#   ../main.py                — mounts the router via `include_router`
#   ../composer/four_layer_composer.py
#                              — what the route handler delegates to
#   ../models/soul_file.py    — Pydantic models the routes serialise
#                              (`ComposedPromptResponse` + `UserSegment`)
#   ../../tests/test_api_composed_prompt.py
#                              — HTTP coverage for the routes in this
#                                package (200 happy + 404 unknown
#                                influencer + 422 invalid segment)
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                              — cross-service RPC contract the routes
#                                in this package implement
# ===========================================================================
