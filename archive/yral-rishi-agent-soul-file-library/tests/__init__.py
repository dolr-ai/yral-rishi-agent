# ---------------------------------------------------------------------------
# tests/__init__.py — package marker for the soul-file-library's test suite.
#
# ⭐ START HERE: this directory holds pytest-runnable tests for every
# code surface in `app/`. Tests are organised by concern (one
# `test_*.py` file per source module). pytest auto-discovers them
# via the `tests/` convention + the asyncio-auto mode declared in
# `pyproject.toml`'s `[tool.pytest.ini_options]`.
#
# WHY A SEPARATE `tests/` PACKAGE INSIDE THE SERVICE FOLDER?
# Per the SESSION-SHARDING ownership map:
#   - Service-internal tests live at `<service>/tests/`. Session 4
#     owns these for the 3 services it owns.
#   - Cross-service contract tests + integration tests + eval gold
#     prompts live at REPO-ROOT `tests/`. Session 5 owns those.
# The two folders serve different audiences (per-service unit /
# integration vs. cross-service contract) so they're separate by
# design.
#
# WHY tests/ HAS ITS OWN `__init__.py`?
# Two reasons:
#   1. It lets the conftest's helper functions be `import`-able by
#      each test module (e.g. for shared fixture-builders if those
#      grow beyond conftest scope later).
#   2. It signals to humans + tooling that this directory is a
#      first-class package, not a loose-files directory.
#
# WHAT DOES THIS FILE DO AT IMPORT?
# Nothing — Python uses the file's PRESENCE to mark `tests/` as
# a package. No side effects, no imports, no top-level code.
#
# Today's contents:
#   conftest.py                 — shared fixtures (testcontainers
#                                  Postgres, asyncpg pool, FastAPI client)
#   test_schema_migrations.py   — alembic up/down round-trip
#   test_repository.py          — repository CRUD + partial-unique
#   test_composer.py            — composer happy/error + byte-identity ×5
#   test_api_composed_prompt.py — HTTP route 200 / 404 / 422 paths
#   fixtures/                   — golden-file inputs for composer tests
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   conftest.py                   — shared fixtures every test file uses
#   test_schema_migrations.py     — H11 round-trip
#   test_repository.py            — Day-4 repository surface
#   test_composer.py              — Day-4 composer surface incl. byte-identity
#   test_api_composed_prompt.py   — Day-4 HTTP surface
#   fixtures/composer_golden_layer_output.txt
#                                  — committed expected composer output
#   ../pyproject.toml             — pytest + asyncio + testcontainers + httpx
#                                    dev-dep declarations
#   ../app/                       — the package under test
# ===========================================================================
