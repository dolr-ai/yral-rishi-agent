#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# validate-secrets.sh — verifies every secret declared in `secrets.yaml`
# has a value in every environment where it's required (per D8).
#
# ⭐ START HERE: run this from inside a service folder. The script reads
# `secrets.yaml` next to it, then for each declared secret:
#   - if `local` is in `required_in`, checks the env file (default
#     `.env.local`) has the key with a non-empty value;
#   - if `ci` or `production` is in `required_in`, checks `gh secret list`
#     reports the key as present.
# Exits 0 on full compliance, 1 if any required value is missing, 2 on
# tooling errors (missing yq, gh CLI not authenticated, etc.).
#
# USAGE:
#   validate-secrets.sh                              # reads ./.env.local
#   validate-secrets.sh --env-file <path>            # reads <path> instead
#
# WHY THE `--env-file` FLAG (per Codex round-8 A1 BLOCKER closure on
# PR #148): the test harness needs to point this script at a fixture
# file named `env.local.fixture` (the fixture-rename pattern from
# round-5 keeps the canonical `.env.local` name out of the gitignored
# test tree per D8). Earlier rounds had the test create a transient
# `.env.local` from the fixture + delete it after the run; Codex
# flagged BOTH the in-source-tree deletion (round-7) AND the
# mktemp-temp-dir deletion that still CONTAINED a `.env.local`
# (round-8) as A1 secrets-file-class deletion attempts. The flag
# lets the test pass the fixture path directly, so a file named
# `.env.local` never appears in the test fixture tree or in any
# mktemp temp dir, and the test invocation does not run `rm` against
# any path. Production callers omit the flag and the default
# behavior is unchanged.
#
# WHO RUNS THIS:
#   - CI workflow on every PR touching the service folder (per D8 + I10).
#   - Devs locally before opening a PR ("did I forget to set anything?").
#   - The post-spawn checklist (PR 5 — first run after new-service.sh).
#   - The test harness at `scripts/tests/test_validate_secrets.sh`
#     (passes `--env-file env.local.fixture`).
#
# WHAT THIS SCRIPT DOES NOT DO (per A2.1):
#   - It does NOT push missing values — that's `sync-github-secrets.sh`.
#   - It does NOT generate `.env.example` — that's `gen-env-example.sh`.
#   - It does NOT delete stale Secrets. Per coordinator's note on
#     2026-05-14: removing stale GitHub Secrets hits the hard-stop list
#     (config/secrets) and needs typed YES. This script only REPORTS;
#     deletion is operator-controlled.
#   - It does NOT create or delete any local file. The script is
#     strictly read-only against the filesystem (per the A1 hard-stop
#     discipline + the round-9 test-harness contract above).
#
# DEPENDENCIES:
#   - yq (https://github.com/mikefarah/yq) — YAML query tool.
#     `brew install yq` or `apt install yq`.
#   - gh (https://cli.github.com/) — GitHub CLI, authenticated.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

set -euo pipefail


# ===========================================================================
# Constants
# ===========================================================================

SECRETS_YAML="secrets.yaml"

# Default env-file path. Production callers leave this default; the
# test harness overrides via `--env-file` so the canonical
# `.env.local` filename never appears in the test fixture tree (per
# Codex round-8 A1 BLOCKER closure on PR #148; see file header).
ENV_LOCAL=".env.local"

# Exit codes — keep numeric so the CI workflow can branch on them.
EXIT_OK=0
EXIT_MISSING_VALUE=1
EXIT_TOOLING_ERROR=2


# ===========================================================================
# Helpers
# ===========================================================================

# Print an error to stderr + return a specific exit code.
print_error_and_exit() {
    local code=$1; shift
    echo "Error: $*" >&2
    exit "$code"
}


# ===========================================================================
# Argument parsing
# ===========================================================================

# Walk argv looking for `--env-file <path>`. Anything else is
# rejected with a usage hint so a typo doesn't silently misroute the
# default env-file path. Kept inline (no getopts) because there's
# exactly one flag today + adding getopts would obscure the simple
# read for non-Bash readers.
while [ $# -gt 0 ]; do
    case "$1" in
        --env-file)
            if [ $# -lt 2 ]; then
                print_error_and_exit "$EXIT_TOOLING_ERROR" \
                    "--env-file requires a path argument."
            fi
            ENV_LOCAL="$2"
            shift 2
            ;;
        --env-file=*)
            ENV_LOCAL="${1#--env-file=}"
            shift
            ;;
        *)
            print_error_and_exit "$EXIT_TOOLING_ERROR" \
                "Unknown argument '$1'. Usage: validate-secrets.sh [--env-file <path>]"
            ;;
    esac
done


# ===========================================================================
# Pre-flight checks
# ===========================================================================

if ! command -v yq >/dev/null 2>&1; then
    print_error_and_exit "$EXIT_TOOLING_ERROR" \
        "yq not installed. Install via 'brew install yq' or 'apt install yq'."
fi

if [ ! -f "$SECRETS_YAML" ]; then
    print_error_and_exit "$EXIT_TOOLING_ERROR" \
        "$SECRETS_YAML not found in $(pwd). Run from inside a service folder."
fi

# Validate that secrets.yaml is parseable YAML before we walk it.
if ! yq eval '.' "$SECRETS_YAML" >/dev/null 2>&1; then
    print_error_and_exit "$EXIT_TOOLING_ERROR" \
        "$SECRETS_YAML is malformed YAML. Run 'yq eval . $SECRETS_YAML' to see the parse error."
fi


# ===========================================================================
# Discovery — read every declared secret + its required_in list
# ===========================================================================

# `yq` emits one secret name per line. Collected into an array via
# mapfile so the loop below sees one entry per element (no IFS gotchas).
mapfile -t SECRET_NAMES < <(yq eval '.secrets[].name' "$SECRETS_YAML")

if [ ${#SECRET_NAMES[@]} -eq 0 ]; then
    echo "No secrets declared in $SECRETS_YAML (nothing to validate)."
    exit "$EXIT_OK"
fi


# ===========================================================================
# Walk each secret + check its required_in envs
# ===========================================================================

ANY_MISSING=0
GH_AUTH_CHECKED=0

# Track whether we've successfully reached GitHub at least once, so the
# loop can fail fast if `gh` is missing or unauthenticated.
ensure_gh_ready() {
    if [ "$GH_AUTH_CHECKED" -eq 1 ]; then return 0; fi
    if ! command -v gh >/dev/null 2>&1; then
        print_error_and_exit "$EXIT_TOOLING_ERROR" \
            "gh CLI not installed. Install from https://cli.github.com/."
    fi
    if ! gh auth status >/dev/null 2>&1; then
        print_error_and_exit "$EXIT_TOOLING_ERROR" \
            "gh CLI not authenticated. Run 'gh auth login'."
    fi
    GH_AUTH_CHECKED=1
}

for secret_name in "${SECRET_NAMES[@]}"; do
    # Per-secret accumulator: one line per env reporting present/missing.
    echo "$secret_name:"

    # Read the required_in array for this secret.
    mapfile -t REQUIRED_ENVS < <(
        yq eval ".secrets[] | select(.name == \"$secret_name\") | .required_in[]" "$SECRETS_YAML"
    )

    for env_name in "${REQUIRED_ENVS[@]}"; do
        case "$env_name" in
            local)
                # Look for KEY= line with a non-empty value in .env.local.
                if [ -f "$ENV_LOCAL" ] && grep -q "^$secret_name=..*" "$ENV_LOCAL"; then
                    echo "  local      ✓ present in $ENV_LOCAL"
                else
                    echo "  local      ✗ MISSING in $ENV_LOCAL"
                    ANY_MISSING=1
                fi
                ;;
            ci|production)
                ensure_gh_ready
                # Same GitHub Secret backs both ci and production for
                # per-service secrets (per D1/D8). Check once per secret.
                if gh secret list --json name --jq '.[].name' 2>/dev/null \
                        | grep -qx "$secret_name"; then
                    echo "  $env_name    ✓ present in GitHub Secrets"
                else
                    echo "  $env_name    ✗ MISSING in GitHub Secrets"
                    ANY_MISSING=1
                fi
                ;;
            *)
                echo "  $env_name    ? unknown env (declared in $SECRETS_YAML)"
                ANY_MISSING=1
                ;;
        esac
    done
done

echo ""
if [ "$ANY_MISSING" -eq 1 ]; then
    echo "FAIL: one or more required values missing. See above for details."
    exit "$EXIT_MISSING_VALUE"
fi

echo "OK: every declared secret has a value in every required environment."
exit "$EXIT_OK"


# ===========================================================================
# RELATED FILES:
#   ../secrets.yaml              — the manifest this script reads
#   ../.env.example              — local-env-var template (gen-env-example.sh)
#   sync-github-secrets.sh       — interactively populates missing Secrets
#   gen-env-example.sh           — regenerates .env.example from secrets.yaml
#   tests/test_validate_secrets.sh
#                                — smoke + failure-path tests
#   ../yral-rishi-agent-plan-and-discussions/
#     secrets-management-pattern-for-every-v2-service/
#                                — D8 schema + bridge-scripts spec
# ===========================================================================
