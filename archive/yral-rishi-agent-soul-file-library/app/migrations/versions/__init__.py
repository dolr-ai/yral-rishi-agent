# ---------------------------------------------------------------------------
# app/migrations/versions/__init__.py — package marker for per-revision
# Alembic migration scripts.
#
# ⭐ START HERE: this directory holds ONE `.py` file per database
# schema change. Alembic threads the files together via the
# `revision` / `down_revision` identifiers at the top of each
# migration; `down_revision = None` marks the first one in the chain.
#
# WHY EACH MIGRATION IS ITS OWN FILE
# Per-revision files give us per-revision git history + per-revision
# rollback (`alembic downgrade -1` steps back exactly one file).
# Combining migrations into a single growing file would break that
# correspondence + make code review harder.
#
# WHY THE FILENAMES START WITH A NUMBER PREFIX
# `001_initial_schema_and_seed.py` etc. — the number prefix makes the
# chain order obvious at `ls`-time. Alembic itself uses the
# `revision = "001_..."` line inside the file (not the filename) for
# the real ordering, but humans benefit from the prefix.
#
# WHAT DOES THIS FILE DO AT IMPORT?
# Nothing — Python uses the file's PRESENCE to mark `versions/` as
# a package. No side effects.
#
# Today's contents:
#   001_initial_schema_and_seed.py  — Day-4 initial schema + seeds
#
# Day-4.5 adds: a migration that ports chat-ai's `ai_influencers.system_prompt`
# into Layer 3 rows (the A4 data port — "ALL data MUST port" — deferred per the Day-4
# directive's "Out of scope" list).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   001_initial_schema_and_seed.py
#                            — Day-4 first migration; creates `soul_file_layers`
#                              + indexes + L1/L2/L4 seeds (L3 deferred)
#   ../env.py                — Alembic env that runs these migrations
#   ../../../alembic.ini     — config pointing at the parent package
#   ../../../tests/test_schema_migrations.py
#                            — round-trip CI gate (H11 spirit)
# ===========================================================================
