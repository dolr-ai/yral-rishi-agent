# ---------------------------------------------------------------------------
# app/models/__init__.py — Python package marker for the Pydantic models
# directory of the influencer-and-profile-directory service.
#
# ⭐ START HERE: this file makes Python treat `app/models/` as an
# importable package. Pydantic models live in submodules under this
# package, one file per logical type group. Today the package contains
# `influencer_metadata.py` (the `InfluencerMetadata` model mirroring
# `InfluencerDto` from the parity contract).
#
# WHY THIS FILE IS NEAR-EMPTY
# Per B7's package-marker carve-out (same precedent as the migrations
# package markers + soul-file-library's analogous `app/models/__init__.py`):
# no function-level WHAT/WHEN/WHY block applies. The file-header documents
# the marker's role + cross-references for any reader who lands here via
# import-trace. Codex flagged the prior short-comment form in PR #142
# round-1 — this fuller header satisfies the B7 standard while keeping
# the package itself empty.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# (no symbols re-exported — consumers import the specific submodule:
#  `from app.models.influencer_metadata import InfluencerMetadata`)


# ===========================================================================
# RELATED FILES:
#   influencer_metadata.py      — `InfluencerMetadata` Pydantic model;
#                                  mirrors `InfluencerDto` from the parity
#                                  contract at
#                                  interface-contracts/00-api-contract.md
#   ../repository/              — repository layer that returns these models
# ===========================================================================
