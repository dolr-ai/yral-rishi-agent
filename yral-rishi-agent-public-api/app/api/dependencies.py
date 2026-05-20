# ---------------------------------------------------------------------------
# dependencies.py — the SINGLE FastAPI dependency surface for the API.
#
# ⭐ START HERE: every authenticated handler depends on
# `require_authenticated_user`. This file is the import target handler
# code reaches for; it re-exports `AuthenticatedUser` so handlers can
# type their `user:` parameter without dipping into the auth-internal
# package.
#
# WHY THIS FILE EXISTS (vs handlers importing from app/api/auth/dependency.py
# directly)?
# Three wins:
#   1. Single import path — handler boilerplate is:
#        from app.api.dependencies import require_authenticated_user, AuthenticatedUser
#      Reads as English. The auth-internal name
#      `authenticate_user_dual_validate` reads as a description of the
#      machinery, not as the action the handler is taking.
#   2. Insulation — if Day-N later adds preconditions (rate limiting,
#      tenant resolution, billing precheck), they layer in HERE. Handlers
#      keep depending on `require_authenticated_user`; this file orchestrates.
#   3. Discoverability — `app/api/dependencies.py` is the canonical
#      "what dependencies do my handlers need?" lookup, matching the
#      FastAPI ecosystem convention (FastAPI's tutorials all put deps
#      under `dependencies.py`).
#
# WHY DAY-4B IS A THIN RE-EXPORT (not new logic)?
# The actual dual-validate work + AuthenticatedUser construction live
# in `app/api/auth/dependency.py` next to the validators they orchestrate.
# Day-4B's scope is "wire the existing rig into real handlers" — adding
# duplicate logic here would violate A2.1 + the directive's scope
# guardrail ("Do NOT change JWT validator internals — only wire them in").
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from app.api.auth.dependency import (
    AuthenticatedUser,
    authenticate_user_dual_validate,
)

# Public alias the handlers use. Day-4B wires every chat + influencer
# handler with `Depends(require_authenticated_user)`. Health handlers
# DO NOT depend on this — health probes must answer without auth so
# Caddy's `health_uri /health/ready` + Uptime Kuma + Swarm rolling-
# update health checks don't break per F9 + C10 + I2.
require_authenticated_user = authenticate_user_dual_validate


# `__all__` so `from app.api.dependencies import *` does the right
# thing in any future star-import callsite (we don't have one today,
# but it's cheap insurance).
__all__ = ["require_authenticated_user", "AuthenticatedUser"]


# ===========================================================================
# RELATED FILES:
#   auth/dependency.py       — defines AuthenticatedUser + the underlying
#                              authenticate_user_dual_validate function
#   auth/validators.py       — LegacyJwtValidator + StrictJwtValidator
#   auth/jwks_client.py      — Redis-backed JWKS cache per E9
#   auth/observability.py    — Sentry + Langfuse divergence emission
#   chat_routes.py           — every Day-2 chat handler now depends on
#                              require_authenticated_user
#   influencer_routes.py     — every Day-2 influencer handler likewise
#   health_routes.py         — does NOT depend on this (per F9 + C10 + I2)
# ===========================================================================
