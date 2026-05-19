# ---------------------------------------------------------------------------
# app/repository/__init__.py — package marker for the DB-access layer.
#
# ⭐ START HERE: this package holds the ONLY place in the codebase
# that issues raw SQL against `soul_file_layers`. The composer +
# the HTTP route reach the database through these functions; nothing
# else imports asyncpg directly.
#
# WHY A SEPARATE REPOSITORY LAYER?
# Three reasons:
#   1. Centralised SQL — a future schema bump touches ONE file.
#   2. Test isolation — tests can monkeypatch the repository
#      functions to mock data without spinning up Postgres for
#      every unit test (today the testcontainers Postgres handles
#      this; the pattern lets us swap to mocks where appropriate).
#   3. Audit surface — every query against the Day-4 schema lives
#      in one place; security review reads ONE file.
#
# WHY NO SQLAlchemy ORM?
# Per the Day-4 directive verbatim: "no SQLAlchemy ORM — direct
# asyncpg + Pydantic models keeps the dep tree thin per A2.1." The
# repository uses asyncpg's native `pool.fetchrow(...)` /
# `pool.fetch(...)` API + maps Records into Pydantic models at the
# return boundary.
#
# WHAT DOES THIS FILE DO AT IMPORT?
# Nothing — Python uses the file's PRESENCE to mark `app/repository/`
# as a package. No side effects.
#
# Today's contents:
#   soul_file_repository.py  — read + write methods over `soul_file_layers`
#                              (writes for tests + future Prompt-Coach;
#                              NOT wired to an HTTP route Day 4)
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   soul_file_repository.py  — the only module in this package today
#   ../database.py           — asyncpg pool the repository functions consume
#   ../models/soul_file.py   — `SoulFileLayer` Pydantic model returned here
#   ../composer/four_layer_composer.py
#                            — primary consumer (4 get_current() calls / turn)
#   ../api/composed_prompt_routes.py
#                            — also calls get_current() to detect L3 misses
#   ../migrations/versions/001_initial_schema_and_seed.py
#                            — the schema this repository's SQL targets
#   ../../tests/test_repository.py
#                            — CRUD + partial-unique-index tests
# ===========================================================================
