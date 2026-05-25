#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# test_validate_secrets.sh — covers happy path + failure paths for
# validate-secrets.sh.
#
# ⭐ START HERE: invoke from the service root or from the tests folder.
# Per DEP-010 the checked-in fixtures use `env.local.fixture` (NOT
# `.env.local`, which would collide with the repo-root .gitignore:25
# hygiene rule). At test runtime each fixture directory is copied
# into a `mktemp -d` working directory and `env.local.fixture` is
# renamed to `.env.local` inside that temporary directory, so the
# validator (which reads `.env.local` from the current working
# directory) sees the literal filename without it ever existing in
# the tracked tree. Cleanup is automatic via subshell EXIT trap.
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

# assert_exit_code — run validate-secrets.sh against a runtime copy
# of a checked-in fixture directory + assert the resulting exit
# code matches the expected value.
#
# WHAT: takes 3 positional arguments — expected_exit_code ($1),
#       fixture_subdirectory_name ($2), human_readable_label ($3) —
#       copies `fixtures/$2` into a fresh `mktemp -d` working
#       directory, renames `env.local.fixture` → `.env.local`
#       inside the copy (only when the fixture ships one),
#       cd's into that throw-away directory, runs the
#       `validate-secrets.sh` script under test, captures its exit
#       code in a subshell, then prints PASS or FAIL against the
#       expected value and increments the suite-level PASS/FAIL
#       counters.
# WHEN: invoked once per test case from the case block below — 5
#       calls in this suite (happy path + 4 failure paths). Each
#       call is independent; the per-call `mktemp -d` working
#       directory + EXIT-trap cleanup means assertions don't leak
#       state into each other regardless of order.
# WHY:  centralizing the run-and-assert mechanics in one helper
#       keeps each case-block call to a single line — the
#       expected-exit-code, the fixture name, and a label — so
#       reviewers see what's being tested without re-reading the
#       runner machinery. The mktemp-copy-rename pattern (per
#       DEP-010 — see file header above for the rationale)
#       guarantees the literal `.env.local` filename never exists
#       in the tracked tree even though the validator-under-test
#       requires it as its working-directory-relative input.
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
        # reads `.env.local` from the current working directory; we
        # never want that literal name to exist in the tracked tree
        # (DEP-010), so it lives only inside this throw-away
        # directory for the duration of the run.
        temporary_fixture_directory="$(mktemp -d)"
        # Cleanup fires on EVERY subshell exit path — success, failure,
        # signal — so leaked /tmp/tmp.XXXXXX directories can't
        # accumulate across test runs.
        trap 'rm -rf "$temporary_fixture_directory"' EXIT
        # Copy the checked-in fixture (with `env.local.fixture` as
        # the env-shaped file's name) into the throw-away directory.
        # The trailing `/.` form preserves dotfiles + directory
        # contents without nesting.
        cp -R "$fixture_dir/." "$temporary_fixture_directory/"
        # Rename `env.local.fixture` → `.env.local` ONLY inside the
        # throw-away directory, only when the fixture ships one.
        # Fixtures that intentionally lack an environment-variable
        # file (missing-env-local, malformed-yaml, no-secrets-yaml)
        # skip the rename — the `[ -f ... ]` guard keeps `cp`
        # semantics aligned with the original layout where each
        # fixture was the validator's working directory directly.
        if [ -f "$temporary_fixture_directory/env.local.fixture" ]; then
            mv "$temporary_fixture_directory/env.local.fixture" \
               "$temporary_fixture_directory/.env.local"
        fi
        # `cd` into the throw-away directory so the validator's
        # working-directory-relative reads (`.env.local`,
        # `secrets.yaml`) hit the fixture copy, not anything else on
        # disk.
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
