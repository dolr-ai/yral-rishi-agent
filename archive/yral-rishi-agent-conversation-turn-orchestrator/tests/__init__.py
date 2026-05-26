# ---------------------------------------------------------------------------
# tests/__init__.py — marker file for the orchestrator's `tests` package.
#
# ⭐ START HERE: this directory holds pytest-runnable tests for the
# orchestrator service's `app/` code. Today it contains:
#   - conftest.py     — shared FastAPI TestClient fixture
#   - test_run_turn.py — Day-2 run_turn RPC handler coverage
#
# WHY A SEPARATE `tests/` PACKAGE INSIDE THE SERVICE FOLDER?
# Per the SESSION-SHARDING ownership map:
#   - Service-internal tests live at `<service>/tests/`. Session 4 owns
#     these for the 3 services it owns.
#   - Cross-service contract tests + integration tests + eval gold
#     prompts live at REPO-ROOT `tests/`. Session 5 owns those.
# The two folders serve different audiences (per-service unit/integration
# tests vs. cross-service contract tests) so they're separate by design.
#
# WHY pytest?
# Pinned at 8.3.4 in pyproject.toml's dev extras (per Session 2's Day-1
# template); pytest-asyncio 0.25.2 likewise. Standard Python testing
# stack — no surprises.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------


# ===========================================================================
# RELATED FILES:
#   conftest.py        — shared TestClient fixture
#   test_run_turn.py   — Day-2 happy + error path coverage
#   ../pyproject.toml  — pytest + pytest-asyncio dev extras
#   ../app/run_turn.py — handler under test
#   ../app/models/turn.py
#                      — Pydantic models the tests assert against
# ===========================================================================
