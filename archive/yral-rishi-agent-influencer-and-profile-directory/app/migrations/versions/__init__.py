# ---------------------------------------------------------------------------
# app/migrations/versions/__init__.py — Python package marker for the
# Alembic per-revision-migration directory.
#
# ⭐ START HERE: this file makes Python treat `app/migrations/versions/`
# as an importable package. Each per-revision migration file in this
# directory (e.g. `001_initial_schema.py`) is a standalone Python
# module Alembic imports + invokes `upgrade()` / `downgrade()` on. The
# package marker is required because Alembic relies on Python's import
# machinery to load these files.
#
# WHY THIS FILE IS NEAR-EMPTY
# Per B7's package-marker carve-out (same precedent as the parent
# `migrations/__init__.py` + soul-file-library's analogous package
# markers): no function-level WHAT/WHEN/WHY block applies here. The
# file-header documents the marker's role + the Alembic import-pattern
# context for any reader who lands here via import-trace. Codex flagged
# the prior short-comment form in PR #142 round-1 — this fuller header
# satisfies the B7 standard while keeping the package itself empty.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# (no symbols exported — Alembic discovers per-revision files via
#  filesystem-scan of this directory + matches `down_revision` chains)


# ===========================================================================
# RELATED FILES:
#   001_initial_schema.py       — first (and currently only) migration
#                                  in this directory; creates the
#                                  `influencer_metadata` table
#   ../env.py                   — Alembic environment script that
#                                  imports + invokes each per-revision
#                                  module's upgrade() / downgrade()
#   ../__init__.py              — parent migrations-package marker
# ===========================================================================
