# ---------------------------------------------------------------------------
# tests/__init__.py — Python package marker for the influencer-and-profile-
# directory test suite.
#
# ⭐ START HERE: this file makes Python treat `tests/` as an importable
# package. Pytest's discovery mechanism is happier when the test
# directory is a proper package (cross-test fixtures import cleanly,
# `from tests.conftest import ...` works in helper modules). Required
# even though pytest's `rootdir` mode can technically discover tests
# without it.
#
# WHY THIS FILE IS NEAR-EMPTY
# Per B7's package-marker carve-out (same precedent as the `app/`
# subpackage markers + soul-file-library's analogous `tests/__init__.py`):
# no function-level WHAT/WHEN/WHY block applies. The file-header
# documents the marker's role for any reader who lands here via
# import-trace.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# (no symbols re-exported — tests import directly from
#  `tests.conftest` or from `app.*` submodules)


# ===========================================================================
# RELATED FILES:
#   conftest.py                 — shared pytest fixtures (testcontainers
#                                  Postgres, asyncpg pool, alembic upgrade)
#   test_schema_migrations.py   — Alembic upgrade/downgrade round-trip +
#                                  table-presence assertions
#   test_influencer_metadata_repository.py
#                               — unit tests for the 3 repository read
#                                  methods (get_by_id, list_paginated,
#                                  list_trending)
# ===========================================================================
