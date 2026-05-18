#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_gen_env_example.sh — covers gen-env-example.sh's --check mode + the
# structural properties of the generated output.
#
# ⭐ START HERE: invoke from anywhere. Read-only against fixtures (per A1).
# Generated output is captured via `--check` against an in-memory stream —
# never written to disk by these tests.
#
# COVERED CASES:
#   1. happy path — running on fixtures/valid/ (no .env.example) reports
#      DRIFT (exit 1) since .env.example doesn't exist there yet.
#   2. malformed secrets.yaml → exit 2 (EXIT_TOOLING_ERROR).
#   3. no secrets.yaml → exit 2.
#
# NOT COVERED:
#   - Output structure (per-secret block format). PR review catches this
#     at the source-code level; automating it from a read-only test
#     requires .env.example fixtures that drift over time. A2.1: skip.
#   - The actual file-write path. PR 5 will exercise it end-to-end when
#     new-service.sh spawns hello-world and gen-env-example.sh seeds the
#     spawned service's .env.example.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

set -euo pipefail

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_DIR="$(cd "$TESTS_DIR/.." && pwd)"
SCRIPT_UNDER_TEST="$TEMPLATE_DIR/gen-env-example.sh"

PASS=0
FAIL=0


# ===========================================================================
# Helpers
# ===========================================================================

assert_exit_code() {
    local expected=$1
    local fixture=$2
    local flag=$3
    local label=$4

    local fixture_dir="$TESTS_DIR/fixtures/$fixture"
    local actual=0
    ( cd "$fixture_dir" && bash "$SCRIPT_UNDER_TEST" $flag ) >/dev/null 2>&1 || actual=$?

    if [ "$actual" -eq "$expected" ]; then
        PASS=$((PASS + 1))
        echo "PASS  $label  (exit=$actual)"
    else
        FAIL=$((FAIL + 1))
        echo "FAIL  $label  (expected exit=$expected, got=$actual)"
    fi
}

# ===========================================================================
# Cases
# ===========================================================================

# 1. valid fixture, --check mode, no .env.example present → DRIFT (exit 1).
assert_exit_code 1 "valid" "--check" \
    "valid fixture w/o .env.example → --check reports DRIFT (exit 1)"

# 2. malformed YAML → exit 2.
assert_exit_code 2 "malformed-yaml" "--check" \
    "malformed secrets.yaml → exit 2 (EXIT_TOOLING_ERROR)"

# 3. no secrets.yaml → exit 2.
assert_exit_code 2 "no-secrets-yaml" "--check" \
    "no secrets.yaml → exit 2 (EXIT_TOOLING_ERROR)"


# ===========================================================================
# Summary
# ===========================================================================

echo ""
echo "------------------------------------------------------------"
echo "gen-env-example.sh tests: $PASS passed, $FAIL failed"
echo "------------------------------------------------------------"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi


# ===========================================================================
# RELATED FILES:
#   ../gen-env-example.sh          — the script under test
#   fixtures/valid/secrets.yaml    — happy-path fixture
#   fixtures/malformed-yaml/       — failure-path fixture
#   fixtures/no-secrets-yaml/      — failure-path fixture
#   README.md                      — how to run + extend the test suite
# ===========================================================================
