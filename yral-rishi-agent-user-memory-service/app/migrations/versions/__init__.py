# ---------------------------------------------------------------------------
# app/migrations/versions/__init__.py — package marker for per-revision
# Alembic migration scripts.
#
# ⭐ START HERE: each `.py` file in this directory is ONE schema change.
# Alembic threads them together via `revision` / `down_revision` headers
# inside each file; `down_revision = None` marks the first migration.
#
# CURRENT CONTENTS:
#   001_initial_schema.py  — Phase 1 schema: conversations + messages tables
#                            with indices for the RPC read patterns.
#
# WHAT THIS FILE DOES AT IMPORT?
# Nothing — Python uses the file's PRESENCE to mark `versions/` as a
# package. No side effects.
#
# HOW TO ADD A NEW MIGRATION:
# 1. Create `002_<descriptive_slug>.py` in this folder.
# 2. Set `down_revision = "001_initial_schema"` (points to the prior rev).
# 3. Set `revision = "002_<descriptive_slug>"`.
# 4. Write `def upgrade():` with the forward DDL.
# 5. Write `def downgrade():` with the reverse DDL.
# 6. Run `alembic upgrade head` in a test (or local compose) to verify.
# 7. PR includes `tests/test_schema_migrations.py` update if the table
#    list changes.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   001_initial_schema.py  — Phase 1 migration (conversations + messages)
#   ../env.py              — Alembic env that runs these migrations
#   ../../../alembic.ini   — config pointing at this package
#   ../../../tests/test_schema_migrations.py
#                          — round-trip test asserts both tables exist
#                            post-upgrade and are gone post-downgrade
# ===========================================================================
