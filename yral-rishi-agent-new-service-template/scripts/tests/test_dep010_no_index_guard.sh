#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_dep010_no_index_guard.sh — proves the DEP-010 probe in
# new-service.sh step 6 catches the regression class Codex flagged on
# PR #135 round-3: a future .gitignore rule that matches a TRACKED
# template fixture would slip past a default `git check-ignore -q`
# probe (because git treats tracked files as "tracked, not ignored"
# unless `--no-index` forces gitignore-only semantics).
#
# ⭐ START HERE: 3 assertions in a self-contained sandbox repo:
#   1. With `--no-index`: the probe catches the tracked-but-would-be-
#      ignored case (exit 0 = caught). Proves the round-4 fix works.
#   2. Without `--no-index`: the probe misses it (exit 1 = missed).
#      Demonstrates exactly the bug Codex identified, so a future
#      reader understands why `--no-index` is load-bearing.
#   3. Static-grep on new-service.sh: the actual DEP-010 probe in the
#      spawner script still uses `--no-index`. Fires the moment a
#      future refactor drops the flag — which is the most likely way
#      this regression class would re-appear.
#
# WHY A SANDBOX REPO (vs against the live one)
# The live repo's .gitignore correctly does NOT match env.local.fixture
# today (DEP-010 closed that on PR #133); the regression scenario
# doesn't exist in the live repo + we don't want to mutate the live
# .gitignore to manufacture one. A fresh `mktemp -d` + `git init`
# sandbox lets us materialize the regression cleanly, run the probe,
# and tear down without touching the live tree.
#
# WHERE THIS RUNS
#   - Spawned services: invoked from `per-service-ci.yml`'s shell-tests
#     job (same place test_validate_secrets.sh + test_gen_env_example.sh
#     run). Every spawned service gets the regression-class guard in
#     its own CI on every PR.
#   - Template-side: invoked as a pre-flight check from
#     test_spawn_smoke.sh BEFORE step 0 of the smoke (cheap; sub-second;
#     no Docker needed). Catches the regression at template-CI time.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

set -euo pipefail

PASS=0
FAIL=0


# ===========================================================================
# Resolve the new-service.sh under test (for the static-grep assertion)
# ===========================================================================

# Same path-resolution pattern as sibling tests: derive from
# `dirname "$0"` so this script works regardless of cwd.
tests_directory="$(cd "$(dirname "$0")" && pwd)"
scripts_directory="$(cd "$tests_directory/.." && pwd)"
new_service_script="$scripts_directory/new-service.sh"

if [ ! -f "$new_service_script" ]; then
    echo "FAIL  setup: new-service.sh not found at expected path $new_service_script"
    exit 2
fi


# ===========================================================================
# Build a tiny sandbox git repo
# ===========================================================================

# Per-run sandbox under the system temp directory ($TMPDIR on macOS,
# /tmp on Linux, $RUNNER_TEMP on GH Actions — `mktemp -d -t` picks
# the right one automatically). EXIT trap cleans up unconditionally
# so back-to-back local runs don't accumulate /tmp/dep010-*.
sandbox_directory="$(mktemp -d -t dep010-no-index.XXXXXX)"
trap 'rm -rf "$sandbox_directory"' EXIT

# `git init -q` quietly creates the sandbox repo. We set local
# user.email + user.name because some git versions refuse to commit
# without them (e.g. fresh CI runners with no global git config).
cd "$sandbox_directory"
git init -q .
git config user.email "dep010-test@example.invalid"
git config user.name "DEP-010 no-index guard test"

# Materialize the regression scenario: a `.gitignore` rule that
# WOULD match env.local.fixture, then force-add the fixture so it's
# TRACKED despite the gitignore rule. This is the exact shape Codex
# flagged: a future template PR that adds `*.fixture` (or any rule
# catching the fixture name) would land the rule + leave the existing
# tracked fixture in place; the default check-ignore probe would
# say "not ignored, all good" while the next caller's `git add` of
# a fresh copy WOULD be silently swallowed.
echo "*.fixture" > .gitignore
echo "SAMPLE_VAR=test" > env.local.fixture
git add -f env.local.fixture .gitignore
git commit -q -m "track env.local.fixture despite *.fixture .gitignore rule"


# ===========================================================================
# Assertion 1 — `--no-index` catches the regression (the FIX is correct)
# ===========================================================================

# Exit 0 from `check-ignore --no-index` means the path WOULD be
# ignored (evaluated independent of the index state). This is the
# behavior new-service.sh's step 6 depends on; if it broke, the
# probe wouldn't catch any future regression.
if git check-ignore --no-index -q -- env.local.fixture; then
    PASS=$((PASS + 1))
    echo "PASS  --no-index probe catches tracked-but-would-be-ignored case (exit 0)"
else
    FAIL=$((FAIL + 1))
    echo "FAIL  --no-index probe did NOT catch the case (exit 1) — git semantics changed?"
fi


# ===========================================================================
# Assertion 2 — without `--no-index`, the probe misses (documents the BUG)
# ===========================================================================

# Exit 1 from default `check-ignore` means "not ignored" — but the
# path IS matched by the gitignore rule; it's just hidden because
# the path is tracked. This assertion documents WHY --no-index is
# load-bearing: if a future refactor drops the flag, this branch
# would flip to exit 0 (caught) — but then assertion 1 in production
# would silently start passing for the regression class, breaking
# the whole point of the probe.
if git check-ignore -q -- env.local.fixture; then
    FAIL=$((FAIL + 1))
    echo "FAIL  default probe (no --no-index) caught the case — git default semantics changed; --no-index may no longer be required?"
else
    PASS=$((PASS + 1))
    echo "PASS  default probe (no --no-index) misses tracked case (exit 1, as expected) — this is why --no-index is load-bearing"
fi


# ===========================================================================
# Assertion 3 — static grep: new-service.sh's probe still uses `--no-index`
# ===========================================================================

# This is the regression-class guard that actually fires when someone
# edits new-service.sh and drops --no-index in a future refactor.
# Assertions 1 + 2 prove git's semantics; this assertion proves
# new-service.sh USES those semantics correctly.
#
# WHY THE FILTER PIPELINE BELOW (not just a naive grep):
# Codex round-4 on PR #135 caught that a naive `grep 'check-ignore
# --no-index'` would false-pass if that exact phrase appeared only
# in COMMENTS or in ECHO/PRINTF STRINGS. new-service.sh's own DEP-010
# comment block + the operator-facing error message both contain
# the literal phrase `check-ignore --no-index` — so the naive grep
# would silently miss a regression where someone removes the flag
# from the actual `if git -C "$REPO_ROOT" check-ignore --no-index`
# line while leaving the comments + echo in place.
#
# Fix: strip lines that BEGIN with `#`, `echo`, or `printf` (allowing
# leading whitespace) BEFORE the fixed-string grep. What remains is
# real executable shell — if the phrase appears there, the probe is
# correctly invoking `--no-index`; if it doesn't, the probe regressed.
#
# WHY NOT JUST PATTERN-MATCH THE EXACT IF-LINE
# A literal `grep -F 'if git -C "$REPO_ROOT" check-ignore --no-index'`
# would be even tighter, but breaks under legitimate refactors
# (variable rename like REPO_ROOT → repo_root, restructuring the
# git invocation, etc.). The filter-then-fixed-grep approach is
# robust to refactors while still catching the regression class.
#
# The filter EXCLUDES:
#   - lines that start with optional whitespace + `#` (any comment)
#   - lines that start with optional whitespace + `echo ` or `echo"`
#   - lines that start with optional whitespace + `printf `
# Word-boundary on `echo`/`printf` (the trailing space-or-quote check)
# prevents false-strips of identifiers like `echotemp_var=foo`.
filtered_lines="$(grep -vE '^[[:space:]]*(#|echo[[:space:]"'"'"']|printf[[:space:]])' "$new_service_script" || true)"
if echo "$filtered_lines" | grep -qF 'check-ignore --no-index'; then
    PASS=$((PASS + 1))
    echo "PASS  new-service.sh DEP-010 probe still uses 'check-ignore --no-index' on an executable line"
else
    FAIL=$((FAIL + 1))
    echo "FAIL  new-service.sh DEP-010 probe is MISSING --no-index on any executable line — regression-class guard would re-open"
    echo "      (Comment-only or echo-string mentions of 'check-ignore --no-index' are intentionally ignored here.)"
    echo "      Path: $new_service_script"
fi


# ===========================================================================
# Summary
# ===========================================================================

echo ""
echo "------------------------------------------------------------"
echo "DEP-010 --no-index probe regression-class guard: $PASS passed, $FAIL failed"
echo "------------------------------------------------------------"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi


# ===========================================================================
# RELATED FILES:
#   ../new-service.sh                 — the spawner whose DEP-010 probe
#                                       in step 6 this test guards
#   ../tests/test_validate_secrets.sh — sibling D8-bridge unit test
#   ../tests/test_spawn_smoke.sh      — invokes this test as a
#                                       pre-flight check
#   ../../.github/workflows/per-service-ci.yml
#                                     — template's per-service CI;
#                                       invokes this test in its
#                                       shell-tests job (downstream
#                                       coverage on every spawned
#                                       service)
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-
#     parallel-build-coordination/cross-session-dependencies.md (DEP-010)
#                                     — the bug + invariant this test
#                                       guards against future regression of
# ===========================================================================
