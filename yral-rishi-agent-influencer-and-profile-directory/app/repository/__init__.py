# ---------------------------------------------------------------------------
# app/repository/__init__.py — Python package marker for the data-access-
# layer directory of the influencer-and-profile-directory service.
#
# ⭐ START HERE: this file makes Python treat `app/repository/` as an
# importable package. Repository submodules live here, one file per
# logical aggregate. Today the package contains
# `influencer_metadata_repository.py` (asyncpg-backed reads against the
# `influencer_metadata` table).
#
# WHY THIS FILE IS NEAR-EMPTY
# Per B7's package-marker carve-out (same precedent as the other package
# markers in this service + soul-file-library's analogous
# `app/repository/__init__.py`): no function-level WHAT/WHEN/WHY block
# applies. The file-header documents the marker's role + cross-references
# for any reader who lands here via import-trace. Codex flagged the prior
# short-comment form in PR #142 round-1 — this fuller header satisfies
# the B7 standard while keeping the package itself empty.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# (no symbols re-exported — consumers import the specific submodule:
#  `from app.repository.influencer_metadata_repository import get_by_id`)


# ===========================================================================
# RELATED FILES:
#   influencer_metadata_repository.py
#                               — asyncpg-backed read methods
#                                  (get_by_id, list_paginated, list_trending)
#   ../models/influencer_metadata.py
#                               — Pydantic model the repository returns
#   ../database.py              — `get_pool()` accessor every repository
#                                  function consumes
# ===========================================================================
