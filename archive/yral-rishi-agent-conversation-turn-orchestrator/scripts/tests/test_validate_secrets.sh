#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_validate_secrets.sh — covers happy path + failure paths for
# validate-secrets.sh.
#
# ⭐ START HERE: invoke from the template root or from the tests folder.
# Tests are read-only against the `fixtures/` directories — no temp dirs,
# no cleanup needed (per A1 spirit: never delete what you don't have to
# create).
#
# COVERED CASES:
#   1. happy path — secrets.yaml + complete .env.local → exit 0
#   2. missing .env.local entirely → exit 1 (EXIT_MISSING_VALUE)
#   3. .env.local has an empty value for a required secret → exit 1
#   4. malformed secrets.yaml → exit 2 (EXIT_TOOLING_ERROR)
#   5. no secrets.yaml at all → exit 2
#
# NOT COVERED (defers to manual / live-CI testing):
#   - gh secret list integration (would need a mock or live auth).
#     Fixtures use required_in: [local] only to keep tests self-contained.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

set -euo pipefail

# Resolve paths relative to this test file so the suite works from any cwd.
TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_DIR="$(cd "$TESTS_DIR/.." && pwd)"
SCRIPT_UNDER_TEST="$TEMPLATE_DIR/validate-secrets.sh"

PASS=0
FAIL=0


# ===========================================================================
# Helpers
# ===========================================================================

# Run validate-secrets.sh from inside a fixture dir; capture exit code only.
# Expected exit code passed in $1; case label in $2.
#
# On FAIL: dump the captured stdout + stderr from the under-test script
# so CI logs surface the actual diagnostic. Without this, a happy-path
# regression like "snap-yq emits a warning that pollutes the secret-name
# list" silently fails the assertion + leaves nothing to debug from.
# Day-5 PR #109 hit exactly this — the first CI run failed the happy
# path with no visible cause; this diagnostic surfaces the underlying
# script output on the next failure.
assert_exit_code() {
    local expected=$1
    local fixture=$2
    local label=$3

    local fixture_dir="$TESTS_DIR/fixtures/$fixture"
    local actual=0
    # Capture stdout + stderr separately so we can dump both on failure.
    # `cd` into the fixture so validate-secrets.sh reads that fixture's files.
    local captured_combined_output
    captured_combined_output=$(
        ( cd "$fixture_dir" && bash "$SCRIPT_UNDER_TEST" ) 2>&1
    ) || actual=$?

    if [ "$actual" -eq "$expected" ]; then
        PASS=$((PASS + 1))
        echo "PASS  $label  (exit=$actual)"
    else
        FAIL=$((FAIL + 1))
        echo "FAIL  $label  (expected exit=$expected, got=$actual)"
        echo "    --- captured output from $SCRIPT_UNDER_TEST in $fixture_dir ---"
        # Indent each line so the diagnostic stands out + nests visually
        # under the FAIL line.
        echo "$captured_combined_output" | sed 's/^/      /'
        echo "    --- end captured output ---"
    fi
}


# ===========================================================================
# Cases
# ===========================================================================

# 1. happy path — exit 0
assert_exit_code 0 "valid" \
    "happy path: secrets.yaml + complete .env.local → exit 0"

# 2. missing .env.local — exit 1
assert_exit_code 1 "missing-env-local" \
    "missing .env.local → exit 1 (EXIT_MISSING_VALUE)"

# 3. .env.local has an empty required value — exit 1
assert_exit_code 1 "env-local-incomplete" \
    "incomplete .env.local → exit 1"

# 4. malformed YAML — exit 2
assert_exit_code 2 "malformed-yaml" \
    "malformed secrets.yaml → exit 2 (EXIT_TOOLING_ERROR)"

# 5. no secrets.yaml at all — exit 2
assert_exit_code 2 "no-secrets-yaml" \
    "no secrets.yaml → exit 2 (EXIT_TOOLING_ERROR)"


# ===========================================================================
# Summary
# ===========================================================================

echo ""
echo "------------------------------------------------------------"
echo "validate-secrets.sh tests: $PASS passed, $FAIL failed"
echo "------------------------------------------------------------"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi


# ===========================================================================
# RELATED FILES:
#   ../validate-secrets.sh         — the script under test
#   fixtures/valid/                — happy-path fixture
#   fixtures/missing-env-local/    — failure-path fixture
#   fixtures/env-local-incomplete/ — failure-path fixture
#   fixtures/malformed-yaml/       — failure-path fixture
#   fixtures/no-secrets-yaml/      — failure-path fixture
#   README.md                      — how to run + extend the test suite
# ===========================================================================
