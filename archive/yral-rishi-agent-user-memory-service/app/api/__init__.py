# ---------------------------------------------------------------------------
# app/api/__init__.py — marks app/api/ as a Python package.
#
# ⭐ START HERE: this file's only job is to make Python treat app/api/
# as an importable package. The real content lives in:
#   models.py              — Pydantic request + response models
#   conversation_routes.py — the 4 RPC route handlers
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# RELATED FILES:
#   models.py              — Pydantic models for requests + responses
#   conversation_routes.py — FastAPI route handlers mounted in main.py
#   ../main.py             — imports conversation_routes.router and mounts it
# ---------------------------------------------------------------------------
