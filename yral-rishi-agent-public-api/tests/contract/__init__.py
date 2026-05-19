# ---------------------------------------------------------------------------
# tests/contract/__init__.py — package marker for the contract test suite.
#
# ⭐ START HERE: this file makes `tests/contract/` an importable Python
# package. Same role as `tests/__init__.py` one level up. Without it,
# pytest can collect the test files but cross-test imports (e.g. a
# Day-4C test file that does `from tests.contract.conftest import
# DEFAULT_ISSUER`) would fail.
#
# PLAIN-ENGLISH EXPLANATION (for a non-programmer reader)
# Same as the parent `tests/__init__.py`: Python needs an `__init__.py`
# file in each folder you want to import from as a package. This file
# is empty by design — its EXISTENCE is the side effect, not its
# contents.
#
# WHAT IS "tests/contract/"?
# The folder holds tests that assert the LOCKED API contract between
# v2 public-api and mobile (per `interface-contracts/00-api-contract.md`).
# Each test verifies one shape claim: envelope structure, response-
# model field names + types, error-code values, status codes, header
# behavior. Contract tests are the safety net Codex + the Day 6-7
# parity sprint lean on to catch silent regressions against the
# chat-ai wire format mobile already parses.
#
# INPUTS / OUTPUTS / SIDE EFFECTS
# - No runtime imports or executable statements.
# - No side effects beyond Python recognizing `tests/contract/` as a
#   package.
# - Read once by Python's import machinery on first
#   `import tests.contract`.
#
# WHY THIS HEADER EXISTS (B7 + Codex PR #97 round-3 BLOCKER 1)
# Codex round-3 flagged that this file had only a one-line comment,
# violating B7's mandatory file-header rule. Expanded to the full
# 3-tier doc treatment per Session 4's PR #104 round-1 precedent.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# Intentionally empty — package marker only. See header for why.

# ===========================================================================
# RELATED FILES:
#   ../__init__.py           — parent package marker (same role)
#   conftest.py              — pytest fixtures shared across this folder's tests
#   test_chat_routes.py      — /api/v1/chat/* + /api/v2/chat/* contract assertions
#   test_health_routes.py    — /health/{live,ready,deep} contract assertions
#   test_influencer_routes.py — /api/v1/influencers/* + admin contract assertions
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — the locked contract the tests assert against
# ===========================================================================
