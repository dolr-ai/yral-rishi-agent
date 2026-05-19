# ---------------------------------------------------------------------------
# tests/__init__.py — package marker for the test tree.
#
# ⭐ START HERE: this file's sole purpose is to make Python (+ pytest)
# treat `tests/` as a Python package. That single side effect is what
# unlocks two things downstream:
#   1. `from tests.contract.conftest import DEFAULT_ISSUER` style
#      cross-test imports (used by the upcoming Day-4C test file at
#      `tests/contract/test_orchestrator_proxy.py`).
#   2. pytest's rootdir-anchored fixture discovery picks up
#      conftest.py at the correct package boundary.
#
# PLAIN-ENGLISH EXPLANATION (for a non-programmer reader)
# Python's import system needs a folder to have an `__init__.py` file
# in it before it can be imported as a package. Without this empty
# marker, code that does `from tests.contract.conftest import ...`
# would fail with `ModuleNotFoundError`. The file's CONTENTS don't
# matter — only its existence does. The B7 header below exists so a
# future reader doesn't delete it thinking "this is empty, why is it
# here?" and break the import graph.
#
# INPUTS / OUTPUTS / SIDE EFFECTS
# - No runtime imports or executable statements.
# - No side effects beyond Python recognizing `tests/` as a package.
# - Read once by Python's import machinery on first `import tests`.
#
# WHY THIS HEADER EXISTS (B7 + Codex PR #97 round-3 BLOCKER 1)
# Codex round-3 flagged that this file had only a one-line comment,
# violating B7's mandatory file-header rule. Expanded to the full
# 3-tier doc treatment per the same pattern Session 4 used on PR #104
# round-1 fixup (commit 90a2a5b).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# Intentionally empty — package marker only. See header for why.

# ===========================================================================
# RELATED FILES:
#   contract/__init__.py     — same shape; marks the sub-package
#   contract/conftest.py     — pytest fixtures shared across the contract tests
#   contract/test_*.py       — actual test functions; depend on the
#                              package boundary this file establishes
# ===========================================================================
