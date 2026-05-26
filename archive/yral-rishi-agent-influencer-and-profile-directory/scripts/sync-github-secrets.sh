#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# sync-github-secrets.sh — interactively populates missing GitHub Secrets
# declared in `secrets.yaml` (per D8).
#
# ⭐ START HERE: run this from inside a service folder. For each secret
# declared with `ci` or `production` in `required_in`, the script checks
# whether `gh secret list` reports the name as present; if not, it prompts
# you (interactively, with hidden input) for the value and pushes via
# `gh secret set`. Existing Secrets are NEVER overwritten — re-running is
# idempotent.
#
# WHO RUNS THIS:
#   - Dev or operator after a new service spawns, to seed its Secrets.
#   - Dev / coordinator when a new secret is added to `secrets.yaml` and
#     the CI workflow starts failing validate-secrets.sh.
#   - One-time per Secret per repo. The values themselves never live in
#     the manifest (per D1/D8).
#
# WHAT THIS SCRIPT DOES NOT DO (per A2.1 + coordinator note 2026-05-14):
#   - It does NOT overwrite existing Secrets. Re-running won't replace
#     anything; if you need to rotate, delete the Secret first via
#     `gh secret delete` (operator-controlled, hard-stop list, needs YES).
#   - It does NOT delete stale Secrets. That's hard-stop territory per
#     A1 + the config/secrets carve-out.
#   - It does NOT validate the secrets.yaml schema — that's
#     `validate-secrets.sh`.
#   - It does NOT generate `.env.example` — that's `gen-env-example.sh`.
#
# DEPENDENCIES:
#   - yq (https://github.com/mikefarah/yq)
#   - gh (https://cli.github.com/), authenticated as a repo admin.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

set -euo pipefail


# ===========================================================================
# Constants
# ===========================================================================

SECRETS_YAML="secrets.yaml"

EXIT_OK=0
EXIT_USER_ABORT=1
EXIT_TOOLING_ERROR=2


# ===========================================================================
# Helpers
# ===========================================================================

print_error_and_exit() {
    local code=$1; shift
    echo "Error: $*" >&2
    exit "$code"
}


# ===========================================================================
# Pre-flight
# ===========================================================================

if ! command -v yq >/dev/null 2>&1; then
    print_error_and_exit "$EXIT_TOOLING_ERROR" \
        "yq not installed. Install via 'brew install yq' or 'apt install yq'."
fi
if ! command -v gh >/dev/null 2>&1; then
    print_error_and_exit "$EXIT_TOOLING_ERROR" \
        "gh CLI not installed. Install from https://cli.github.com/."
fi
if ! gh auth status >/dev/null 2>&1; then
    print_error_and_exit "$EXIT_TOOLING_ERROR" \
        "gh CLI not authenticated. Run 'gh auth login'."
fi
if [ ! -f "$SECRETS_YAML" ]; then
    print_error_and_exit "$EXIT_TOOLING_ERROR" \
        "$SECRETS_YAML not found in $(pwd). Run from inside a service folder."
fi


# ===========================================================================
# Discover existing Secrets in one shot, then walk the manifest
# ===========================================================================

# Cache the existing-Secrets list so we don't hit the GitHub API once per
# secret. The list is small (tens of names per repo).
mapfile -t EXISTING_SECRETS < <(gh secret list --json name --jq '.[].name')

# Returns 0 if $1 is already a GitHub Secret, 1 otherwise.
secret_already_exists() {
    local name=$1
    local existing
    for existing in "${EXISTING_SECRETS[@]}"; do
        [ "$existing" = "$name" ] && return 0
    done
    return 1
}

# Read each declared secret's name + its required_in list. Only act on
# secrets that include `ci` or `production` (others are local-only).
mapfile -t SECRET_NAMES < <(yq eval '.secrets[].name' "$SECRETS_YAML")

ANY_SET=0

for secret_name in "${SECRET_NAMES[@]}"; do
    # If the secret isn't required in any GitHub-Secret-backed env, skip.
    requires_github=$(
        yq eval \
            ".secrets[] | select(.name == \"$secret_name\") | \
             .required_in[] | select(. == \"ci\" or . == \"production\")" \
            "$SECRETS_YAML" | head -n1
    )
    if [ -z "$requires_github" ]; then
        continue
    fi

    if secret_already_exists "$secret_name"; then
        echo "$secret_name: ✓ already in GitHub Secrets (skipping — never overwrite)"
        continue
    fi

    # Pull the description so the prompt is informative.
    description=$(
        yq eval \
            ".secrets[] | select(.name == \"$secret_name\") | .description" \
            "$SECRETS_YAML"
    )

    echo ""
    echo "----------------------------------------"
    echo "Missing Secret: $secret_name"
    echo ""
    echo "Description (from secrets.yaml):"
    echo "$description" | sed 's/^/    /'
    echo ""

    # Hidden-input prompt. `read -s` doesn't echo what the user types.
    # Empty answer = abort the run for safety.
    read -r -s -p "Enter value (empty to abort): " value
    echo ""
    if [ -z "$value" ]; then
        echo ""
        echo "User aborted at $secret_name. No Secrets were pushed in this iteration."
        exit "$EXIT_USER_ABORT"
    fi

    # `gh secret set` writes the value via stdin so it never appears in
    # the shell's process list (no `gh secret set NAME=$value`).
    printf '%s' "$value" | gh secret set "$secret_name"
    echo "$secret_name: ✓ pushed to GitHub Secrets"
    ANY_SET=1
done

echo ""
if [ "$ANY_SET" -eq 0 ]; then
    echo "OK: nothing to do. Every required Secret is already present."
else
    echo "OK: pushed one or more new Secrets. Re-run validate-secrets.sh to confirm."
fi
exit "$EXIT_OK"


# ===========================================================================
# RELATED FILES:
#   ../secrets.yaml              — the manifest this script reads
#   validate-secrets.sh          — confirms post-push completeness
#   gen-env-example.sh           — regenerates .env.example
#   tests/test_sync_github_secrets.sh
#                                — argument + pre-flight tests (no real gh push)
# ===========================================================================
