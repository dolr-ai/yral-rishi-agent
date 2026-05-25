# ---------------------------------------------------------------------------
# app/api/ — HTTP route modules for the influencer-and-profile-directory
# service. Each module exposes ONE `APIRouter` that `app/main.py`
# imports + mounts via `include_router`.
#
# Why this package exists: keeping routes in a per-module folder
# (instead of all in `main.py`) means a new endpoint adds a file
# + one `include_router` line, with no risk of stepping on existing
# routes. Mirrors the shape of every other v2 service's `app/api/`
# folder.
# ---------------------------------------------------------------------------
