# ---------------------------------------------------------------------------
# app/migrations/__init__.py — package marker for Alembic migration scripts.
#
# ⭐ START HERE: this directory holds ONE `.py` file per database schema
# change. Alembic threads the files together via `revision` / `down_revision`
# identifiers; `down_revision = None` marks the first in the chain.
#
# WHY EACH MIGRATION IS ITS OWN FILE?
# Per-revision files give us per-revision git history + per-revision
# rollback (`alembic downgrade -1` steps back exactly one file).
# Combining migrations into a single growing file would break that
# correspondence + make code review harder.
#
# CURRENT MIGRATIONS:
#   001_initial_schema.py  — conversations + messages tables + indices
#
# PLANNED FUTURE MIGRATIONS (added per PR):
#   002_...  — added as Phase 1 evolves or Phase 2 ships pgvector columns
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   versions/001_initial_schema.py — Phase 1 schema (conversations + messages)
#   env.py                         — Alembic env that runs these migrations
#   ../../alembic.ini              — config pointing at this package
#   ../../tests/test_schema_migrations.py
#                                  — round-trip CI gate (H11 spirit)
# ===========================================================================
