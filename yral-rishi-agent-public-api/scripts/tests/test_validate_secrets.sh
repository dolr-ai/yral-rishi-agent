#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_validate_secrets.sh — covers happy path + failure paths for
# validate-secrets.sh.
#
# ⭐ START HERE: invoke from the service root or from the tests folder.
# Per DEP-010 the checked-in fixtures use `env.local.fixture` (NOT
# `.env.local`, which would collide with the repo-root .gitignore:25
# hygiene rule). At test runtime each fixture dir is copied into a
# `mktemp -d` working dir and `env.local.fixture` is renamed to
# `.env.local` inside that temp dir, so the validator (which reads
# `.env.local` from cwd) sees the literal filename without it ever
# existing in the tracked tree. Cleanup is automatic via subshell
# EXIT trap.
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

# Run validate-secrets.sh against a runtime copy of the fixture dir;
# capture exit code only. Expected exit code passed in $1; case label
# in $2. Per DEP-010 the checked-in fixture uses `env.local.fixture`;
# at runtime we copy the fixture dir into mktemp -d, rename
# env.local.fixture → .env.local inside the temp dir, then cd + run
# the validator there. Cleanup is via subshell EXIT trap so it fires
# even if the validator aborts mid-run.
assert_exit_code() {
    local expected=$1
    local fixture=$2
    local label=$3

    local fixture_dir="$TESTS_DIR/fixtures/$fixture"
    local actual=0
    # Run the validator inside a SUBSHELL so the EXIT trap below scopes
    # cleanup to this single assertion. A subshell exit also can't leak
    # `set -e` semantics back into the outer test driver.
    (
        # Strict mode inside the subshell so any intermediate failure
        # (mktemp, cp, mv, cd) aborts cleanly. The outer `|| actual=$?`
        # captures the resulting exit code.
        set -e
        # Materialize a per-assertion working directory. The validator
        # reads `.env.local` from cwd; we never want that literal name
        # to exist in the tracked tree (DEP-010), so it lives only
        # inside this throw-away dir for the duration of the run.
        temporary_fixture_directory="$(mktemp -d)"
        # Cleanup fires on EVERY subshell exit path — success, failure,
        # signal — so leaked /tmp/tmp.XXXXXX dirs can't accumulate
        # across test runs.
        trap 'rm -rf "$temporary_fixture_directory"' EXIT
        # Copy the checked-in fixture (with `env.local.fixture` as the
        # env-shaped file's name) into the throw-away dir. The trailing
        # `/.` form preserves dotfiles + dir contents without nesting.
        cp -R "$fixture_dir/." "$temporary_fixture_directory/"
        # Rename `env.local.fixture` → `.env.local` ONLY inside the
        # throw-away dir, only when the fixture ships one. Fixtures
        # that intentionally lack an env file (missing-env-local,
        # malformed-yaml, no-secrets-yaml) skip the rename — the
        # `[ -f ... ]` guard keeps `cp` semantics aligned with the
        # original-cwd-based test layout.
        if [ -f "$temporary_fixture_directory/env.local.fixture" ]; then
            mv "$temporary_fixture_directory/env.local.fixture" \
               "$temporary_fixture_directory/.env.local"
        fi
        # `cd` into the throw-away dir so the validator's cwd-relative
        # reads (`.env.local`, `secrets.yaml`) hit the fixture copy,
        # not anything else on disk.
        cd "$temporary_fixture_directory" && bash "$SCRIPT_UNDER_TEST"
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
