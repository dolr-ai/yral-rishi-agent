# ---------------------------------------------------------------------------
# app/migrations/__init__.py — Python package marker for the Alembic
# migrations directory of the influencer-and-profile-directory service.
#
# ⭐ START HERE: this file makes Python treat `app/migrations/` as an
# importable package. Alembic itself discovers the `versions/` directory
# under here at runtime via `script_location = app/migrations` in
# `alembic.ini`; this `__init__.py` is required because Alembic's own
# import machinery still treats the directory as a Python package +
# would warn (or fail on stricter Python configurations) without it.
#
# WHY THIS FILE IS NEAR-EMPTY
# Per B7's package-marker carve-out (see soul-file-library's analogous
# `app/migrations/__init__.py` for the cross-service precedent): a
# package marker has no behaviour worth a function-level WHAT/WHEN/WHY
# block. The file-header here documents the marker's role + the
# `script_location` wiring + cross-references for any reader who lands
# here via import-trace. Codex flagged the prior short-comment form in
# PR #142 round-1 — this fuller header satisfies the standard while
# keeping the package itself empty.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# (no symbols exported — Alembic discovers `versions/` via filesystem)


# ===========================================================================
# RELATED FILES:
#   versions/                   — per-revision Alembic migration scripts
#   versions/__init__.py        — package marker for the migrations
#                                  subpackage (same B7 carve-out as this file)
#   env.py                      — Alembic environment script invoked by
#                                  `alembic upgrade` / `alembic downgrade`
#   ../../alembic.ini           — points Alembic at this directory via
#                                  `script_location`
# ===========================================================================
