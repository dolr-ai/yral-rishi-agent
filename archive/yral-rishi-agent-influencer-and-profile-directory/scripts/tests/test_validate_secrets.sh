#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_validate_secrets.sh — covers happy path + failure paths for
# validate-secrets.sh.
#
# ⭐ START HERE: invoke from the template root or from the tests folder.
# Per Codex round-8 A1 BLOCKER closure on PR #148 (round-9): the test
# harness NEVER creates, renames, or deletes any file in the fixture
# tree. It does not use `mktemp`, `cp`, or `rm` against any path.
# validate-secrets.sh now accepts `--env-file <path>` (round-9
# refactor), so each test simply `cd`s into its source fixture
# directory and invokes the script with `--env-file env.local.fixture`.
# The canonical `.env.local` filename never appears in the fixture
# tree (the rename pattern from PR #148 round-5 + the now-round-9
# argument flow keep it out entirely).
#
# A1 path: zero filesystem mutations from this test harness against
# any path that could match the secrets-file class. Codex round-8
# flagged `rm -rf "$temp_dir"` as A1-violating because the temp dir
# CONTAINED a transient `.env.local`; the round-9 refactor removes
# the temp dir + the transient `.env.local` entirely so the
# discipline is satisfied without any guard helper.
#
# COVERED CASES:
#   1. happy path — secrets.yaml + complete env.local.fixture → exit 0
#   2. missing env file entirely → exit 1 (EXIT_MISSING_VALUE)
#   3. env file has an empty value for a required secret → exit 1
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
TESTS_DIRECTORY="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE_DIRECTORY="$(cd "$TESTS_DIRECTORY/.." && pwd)"
SCRIPT_UNDER_TEST="$TEMPLATE_DIRECTORY/validate-secrets.sh"

PASS=0
FAIL=0


# ===========================================================================
# Helpers
# ===========================================================================

# Run validate-secrets.sh against a source fixture directory; capture
# exit code only. Expected exit code in $1; fixture subdirectory name
# in $2; case label in $3.
#
# A1-safe design (per Codex round-8 BLOCKER closure on PR #148):
#   - No file creation, no file deletion. The harness does not run
#     `cp`, `rm`, or `mktemp` against any path.
#   - The validate-secrets.sh `--env-file env.local.fixture` argument
#     points the script at the fixture's `env.local.fixture` file
#     directly. The canonical `.env.local` filename never appears in
#     the source tree or anywhere else under any path that could
#     match the secrets-file class.
#   - `cd` into the source fixture directory is reversible state
#     (the subshell discards the cd on exit) and is not a filesystem
#     mutation.
assert_exit_code() {
    local expected=$1
    local fixture=$2
    local label=$3

    local source_fixture_directory="$TESTS_DIRECTORY/fixtures/$fixture"
    local actual=0

    # cd into the source fixture directory so validate-secrets.sh
    # finds secrets.yaml there. Subshell so the cd doesn't leak.
    # Pass `--env-file env.local.fixture` so the script reads values
    # from the fixture file (instead of looking for `.env.local`,
    # which intentionally does not exist in the fixture tree).
    (
        cd "$source_fixture_directory" \
            && bash "$SCRIPT_UNDER_TEST" --env-file env.local.fixture
    ) >/dev/null 2>&1 || actual=$?

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

# 1. happy path — exit 0
assert_exit_code 0 "valid" \
    "happy path: secrets.yaml + complete env.local.fixture → exit 0"

# 2. missing env file — exit 1
assert_exit_code 1 "missing-env-local" \
    "missing env file → exit 1 (EXIT_MISSING_VALUE)"

# 3. env file has an empty required value — exit 1
assert_exit_code 1 "env-local-incomplete" \
    "incomplete env file → exit 1"

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
