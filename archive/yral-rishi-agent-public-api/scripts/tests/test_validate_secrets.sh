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

# cleanup_temporary_fixture_directory — guarded rm -rf restricted to
# paths that match the expected `mktemp -d` scratch-directory shape.
#
# WHAT: takes one positional argument (the path to remove); refuses
#       to run rm -rf unless the path looks mktemp-generated. The
#       guard accepts the 3 mktemp shapes we encounter in practice:
#         - Linux + most BSDs: `/tmp/tmp.XXXXXX...`
#         - macOS (per-user TMPDIR): `/var/folders/<a>/<b>/T/tmp.XXXX`
#         - explicit $TMPDIR set (e.g. CI): `${TMPDIR}tmp.XXXX`
#       Any other shape (system directory, source-tree path, an
#       empty string, and anything else) returns 1 with a loud REFUSING-TO-DELETE
#       message naming the offending path so a future regression
#       surfaces immediately.
# WHEN: called by assert_exit_code (+ assert_exit_code_and_message_
#       contains) at every test-return path to clean up the per-call
#       mktemp scratch directory.
# WHY:  closes Codex PR #137 round-14 BLOCKER (A1 hard-stop on
#       deletion attempts without path-shape guard). Round-14's
#       bare `rm -rf "$temporary_fixture_directory"` at 4 return
#       points was correct in practice but had no defensive check
#       proving the path was mktemp-generated; a future refactor
#       that assigned a different (e.g. cwd-relative) path to the
#       variable would have caused rm -rf on arbitrary content with
#       no warning. The pattern guard makes the intent explicit +
#       fail-loud on shape mismatch.
cleanup_temporary_fixture_directory() {
    local dir_to_remove="$1"
    case "$dir_to_remove" in
        /tmp/tmp.*|/var/folders/*/T/tmp.*|"${TMPDIR:-/dev/null/}"tmp.*)
            rm -rf "$dir_to_remove"
            ;;
        *)
            echo "REFUSING TO DELETE: '$dir_to_remove' does not match mktemp scratch-directory pattern (A1 hard-stop)" >&2
            return 1
            ;;
    esac
}


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

    # -------- SETUP phase ------------------------------------------------
    # Round-14 (Codex round-13 CONCERN): keep setup steps OUTSIDE the
    # subshell that captures the validator's exit code. Earlier rounds
    # ran setup + invocation inside one `set -e` subshell whose
    # combined exit code was captured into `actual` — a `mktemp`/`cp`/
    # `mv`/`cd` failure exiting with 1 would have masqueraded as the
    # validator's exit 1 (EXIT_MISSING_VALUE), wrongly satisfying the
    # missing-env-local / env-local-incomplete cases.
    #
    # Per-step explicit checks now flag setup failures with a distinct
    # FAIL message naming the failing step; the validator only runs
    # once setup is known-good. Cleanup happens via explicit
    # `rm -rf` at every return path below (no subshell EXIT trap
    # needed now that setup is in the function body).

    # Materialize a per-assertion working directory. The validator
    # reads `.env.local` from the current working directory; we
    # never want that literal name to exist in the tracked tree
    # (DEP-010), so it lives only inside this throw-away
    # directory for the duration of the run.
    local temporary_fixture_directory
    if ! temporary_fixture_directory="$(mktemp -d 2>/dev/null)"; then
        FAIL=$((FAIL + 1))
        echo "FAIL  $label  (SETUP: mktemp failed before validator could run)"
        return
    fi

    # Copy the checked-in fixture (with `env.local.fixture` as the
    # env-shaped file's name) into the throw-away directory. The
    # trailing `/.` form preserves dotfiles + directory contents
    # without nesting.
    if ! cp -R "$fixture_dir/." "$temporary_fixture_directory/" 2>/dev/null; then
        FAIL=$((FAIL + 1))
        echo "FAIL  $label  (SETUP: cp from $fixture_dir failed)"
        cleanup_temporary_fixture_directory "$temporary_fixture_directory"
        return
    fi

    # Rename `env.local.fixture` → `.env.local` ONLY inside the
    # throw-away directory, only when the fixture ships one.
    # Fixtures that intentionally lack an environment-variable file
    # (missing-env-local, malformed-yaml, no-secrets-yaml) skip the
    # rename — the `[ -f ... ]` guard keeps `cp` semantics aligned
    # with the original layout where each fixture was the
    # validator's working directory directly.
    if [ -f "$temporary_fixture_directory/env.local.fixture" ]; then
        if ! mv "$temporary_fixture_directory/env.local.fixture" \
               "$temporary_fixture_directory/.env.local" 2>/dev/null; then
            FAIL=$((FAIL + 1))
            echo "FAIL  $label  (SETUP: mv env.local.fixture → .env.local failed)"
            cleanup_temporary_fixture_directory "$temporary_fixture_directory"
            return
        fi
    fi

    # -------- INVOCATION phase -------------------------------------------
    # Run the validator inside a single-purpose subshell whose ONLY
    # job is to `cd` + invoke the script. Any non-zero exit here is
    # unambiguously the validator's exit code (setup has already been
    # proven successful above); a `cd` failure here would surface as
    # exit 1 but is structurally impossible — `mktemp -d` returned a
    # writable directory + nothing else has happened to it. Output is
    # suppressed so the test harness's standard output stays scoped
    # to PASS / FAIL lines.
    local actual=0
    (cd "$temporary_fixture_directory" && bash "$SCRIPT_UNDER_TEST") >/dev/null 2>&1 || actual=$?

    # Cleanup — explicit (no subshell EXIT trap any more). Runs
    # before the assertion so the throw-away directory is gone
    # regardless of PASS/FAIL outcome. Guarded mktemp-pattern
    # rm -rf per round-15's A1 fix.
    cleanup_temporary_fixture_directory "$temporary_fixture_directory"

    # -------- ASSERTION phase --------------------------------------------
    if [ "$actual" -eq "$expected" ]; then
        PASS=$((PASS + 1))
        echo "PASS  $label  (exit=$actual)"
    else
        FAIL=$((FAIL + 1))
        echo "FAIL  $label  (expected exit=$expected, got=$actual)"
    fi
}


# assert_exit_code_and_message_contains — same as assert_exit_code,
# plus asserts the validator's combined standard output and
# standard error contains a supplied regular-expression pattern.
#
# WHAT: takes 4 positional arguments — expected_exit_code ($1),
#       fixture_subdirectory_name ($2), human_readable_label ($3),
#       message_pattern_grep_regular_expression ($4). Runs the same SETUP /
#       INVOCATION / cleanup machinery as assert_exit_code (with
#       the cleanup_temporary_fixture_directory guard), but captures
#       the validator's combined output (instead of suppressing it
#       via `>/dev/null 2>&1`) and asserts both the exit code AND
#       that the output matches the regular expression.
# WHEN: used for the env-local-incomplete case where the bare-exit-
#       code assertion is INSUFFICIENT — missing-file and missing-
#       value paths both exit 1 (EXIT_MISSING_VALUE), so a future
#       regression that removes env.local.fixture from the
#       env-local-incomplete fixture directory would still satisfy
#       a bare assert_exit_code 1. The message-pattern check
#       distinguishes the two: with the fixture present, the
#       validator emits per-secret `SAMPLE_*: ... ✗ MISSING` lines
#       proving it READ the partial `.env.local`; without the
#       fixture, the same exit code fires but those per-secret
#       lines are missing.
# WHY:  closes Codex PR #137 round-14 CONCERN — round-10's runner
#       left env-local-incomplete passing for the wrong reason
#       (missing file vs incomplete content). Round-15 adds the
#       env.local.fixture WITH one value intentionally blank +
#       this assertion proves the validator successfully read it.
assert_exit_code_and_message_contains() {
    local expected=$1
    local fixture=$2
    local label=$3
    local message_pattern=$4

    local fixture_dir="$TESTS_DIR/fixtures/$fixture"

    # SETUP — same explicit per-step guards as assert_exit_code.
    local temporary_fixture_directory
    if ! temporary_fixture_directory="$(mktemp -d 2>/dev/null)"; then
        FAIL=$((FAIL + 1))
        echo "FAIL  $label  (SETUP: mktemp failed before validator could run)"
        return
    fi
    if ! cp -R "$fixture_dir/." "$temporary_fixture_directory/" 2>/dev/null; then
        FAIL=$((FAIL + 1))
        echo "FAIL  $label  (SETUP: cp from $fixture_dir failed)"
        cleanup_temporary_fixture_directory "$temporary_fixture_directory"
        return
    fi
    if [ -f "$temporary_fixture_directory/env.local.fixture" ]; then
        if ! mv "$temporary_fixture_directory/env.local.fixture" \
               "$temporary_fixture_directory/.env.local" 2>/dev/null; then
            FAIL=$((FAIL + 1))
            echo "FAIL  $label  (SETUP: mv env.local.fixture → .env.local failed)"
            cleanup_temporary_fixture_directory "$temporary_fixture_directory"
            return
        fi
    fi

    # INVOCATION — capture combined standard output and standard error (NOT suppressed)
    # so the message-pattern check below can grep it.
    local actual=0
    local validator_output
    validator_output="$(cd "$temporary_fixture_directory" && bash "$SCRIPT_UNDER_TEST" 2>&1)" || actual=$?

    cleanup_temporary_fixture_directory "$temporary_fixture_directory"

    # ASSERTION — exit code first; if it matches, message pattern
    # second. Both must pass for PASS.
    if [ "$actual" -ne "$expected" ]; then
        FAIL=$((FAIL + 1))
        echo "FAIL  $label  (expected exit=$expected, got=$actual)"
    elif ! echo "$validator_output" | grep -qE "$message_pattern"; then
        FAIL=$((FAIL + 1))
        echo "FAIL  $label  (exit matched but output missing pattern: $message_pattern)"
    else
        PASS=$((PASS + 1))
        echo "PASS  $label  (exit=$actual + output matches /$message_pattern/)"
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

# 3. .env.local has an empty required value — exit 1 PLUS the
#    validator's per-secret `✓ present in .env.local` line for the
#    populated secret. Round-15 added an env.local.fixture with
#    SAMPLE_DATABASE_URL set + SAMPLE_REDIS_PASSWORD intentionally
#    blank. The message-pattern `present in .env.local` is the
#    load-bearing distinguisher between the missing-file case (case
#    2, where the validator can't open .env.local + emits ONLY
#    `✗ MISSING` lines for every required secret) and this
#    incomplete-content case (where the validator successfully
#    opens + reads the partial .env.local, emits `✓ present` for
#    SAMPLE_DATABASE_URL + `✗ MISSING` for SAMPLE_REDIS_PASSWORD).
#    Without this strengthening the case-3 test passed for the
#    wrong reason (missing-file output masquerading as missing-
#    value coverage, since both share exit 1). Closes Codex PR
#    #137 round-14 CONCERN.
assert_exit_code_and_message_contains 1 "env-local-incomplete" \
    "incomplete .env.local: validator READ partial file + 1 secret present + 1 missing → exit 1" \
    "present in .env.local"

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
