# ---------------------------------------------------------------------------
# app/models/__init__.py — marker file for the `models` Python package.
#
# ⭐ START HERE: this directory holds the Pydantic models that define the
# orchestrator's request + response shapes. Today it contains `turn.py`
# (the RunTurnRequest + MessageDto pair for the Day-2 run_turn RPC).
# Subsequent PRs add per-feature model files: e.g., conversation lookups,
# Soul-File composition, safety-stack decisions.
#
# WHY A SEPARATE `models` PACKAGE INSTEAD OF ONE `models.py`?
# Per F8 + B7 — each model file stays small enough to read top-to-bottom
# in one sitting. A single `models.py` would grow into a kitchen-sink as
# the orchestrator's surface expands (Days 3-7). One file per concern
# keeps the doc burden + import graph honest.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   turn.py      — RunTurnRequest + MessageDto (Day-2 RPC)
#   ../run_turn.py — the route handler that consumes these models
#   ../config.py — `enable_run_turn_stub` feature flag
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                — chat-ai MessageDto parity source-of-truth
# ===========================================================================
