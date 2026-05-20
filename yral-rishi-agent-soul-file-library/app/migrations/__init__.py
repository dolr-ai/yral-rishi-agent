# ---------------------------------------------------------------------------
# app/migrations/__init__.py — package marker for Alembic migration scripts.
#
# ⭐ START HERE: this package holds the Alembic environment script
# (`env.py`) + the per-revision migration files under `versions/`.
# Alembic discovers BOTH automatically when run via `alembic upgrade
# head` from the service folder root — this file's job is purely to
# mark `app/migrations/` as a Python package so Alembic can import
# from `env.py`.
#
# WHAT IS ALEMBIC?
# A schema-migration tool that ships per-revision Python files
# describing forward (`upgrade()`) and reverse (`downgrade()`) DDL
# changes. Operators run `alembic upgrade head` to bring a database
# to the latest schema; `alembic downgrade -1` to step back one
# revision.
#
# WHY INSIDE `app/`, NOT AT THE SERVICE-FOLDER ROOT?
# Putting migrations INSIDE the `app/` package means the same
# Dockerfile that builds the service can run migrations during
# deploy without extra COPY directives. The alembic.ini at the
# service root points `script_location = app/migrations` so Alembic
# finds this package from any CWD.
#
# WHAT DOES THIS FILE DO AT IMPORT?
# Nothing — Python uses the file's PRESENCE to mark `app/migrations/`
# as a package. No side effects.
#
# Today's contents:
#   env.py        — Alembic environment script (async + asyncpg)
#   versions/     — per-revision migration files (one per schema change)
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   env.py                   — Alembic env using AsyncEngine + asyncpg
#   versions/                — per-revision sub-package (each .py file = one
#                              forward + reverse migration)
#   ../../alembic.ini        — config pointing at this package
#   ../database.py           — runtime asyncpg pool (separate from Alembic's
#                              SQLAlchemy engine; both talk to the same DB
#                              via different drivers)
#   ../../tests/test_schema_migrations.py
#                            — round-trip up/down/up test (H11 spirit)
# ===========================================================================
