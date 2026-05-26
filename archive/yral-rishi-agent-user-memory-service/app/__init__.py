# ---------------------------------------------------------------------------
# app/__init__.py — marks `app/` as a Python package so `from app.x import y`
# resolves correctly when uvicorn loads `app.main:app`.
#
# ⭐ START HERE: nothing executable lives here. Python requires this file's
# presence to treat the folder as an importable package. All real logic
# starts in `app/main.py`.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# ===========================================================================
# RELATED FILES:
#   main.py            — FastAPI app entry point; uvicorn loads app.main:app
#   config.py          — Settings singleton (pydantic-settings from env vars)
#   database.py        — asyncpg connection pool lifecycle
#   migrations/        — Alembic schema migration scripts
#   api/               — HTTP route handlers (conversation + message RPCs)
# ===========================================================================
