#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# gen-env-example.sh — regenerates `.env.example` from `secrets.yaml`
# plus a small list of non-secret runtime env vars (per D8).
#
# ⭐ START HERE: run this from inside a service folder. The script reads
# `secrets.yaml` (the D8 manifest) and emits a `.env.example` with one
# entry per declared secret: a short comment block (name + description +
# source-per-env line) followed by `NAME=`. Three non-secret env vars
# (ENVIRONMENT, LOG_LEVEL, LANGFUSE_TRACING_ENABLED) get appended too so
# the file stays a complete local-dev template.
#
# WHO RUNS THIS:
#   - Dev when adding/renaming a secret in `secrets.yaml` — keeps
#     `.env.example` in lockstep so the CI drift check doesn't fire.
#   - CI workflow with `--check` (proposed) — fails the build if
#     `.env.example` drifted from `secrets.yaml`. Not wired into the
#     workflow this PR; lands when the lint-secrets-hygiene job is added.
#
# WHAT THIS SCRIPT DOES NOT DO (per A2.1):
#   - It does NOT push values anywhere — that's `sync-github-secrets.sh`.
#   - It does NOT verify env presence — that's `validate-secrets.sh`.
#   - It does NOT delete `.env.example`. The script overwrites the file
#     with new content (a file modification, not a deletion — A1-clean).
#   - It does NOT touch `.env.local` (which is gitignored).
#
# FLAGS:
#   (no flags)    write `.env.example` and exit 0.
#   --check       diff the would-be content vs the existing file; exit 0
#                 if identical, 1 if drifted. For CI gates.
#
# DEPENDENCIES:
#   - yq (https://github.com/mikefarah/yq)
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

set -euo pipefail


# ===========================================================================
# Constants
# ===========================================================================

SECRETS_YAML="secrets.yaml"
ENV_EXAMPLE=".env.example"

EXIT_OK=0
EXIT_DRIFT=1
EXIT_TOOLING_ERROR=2


# ===========================================================================
# Argument parsing
# ===========================================================================

CHECK_MODE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECK_MODE=1; shift ;;
        -h|--help)
            echo "Usage: $0 [--check]"
            echo "  --check  diff would-be content vs existing file; exit 1 on drift"
            exit 0
            ;;
        *) echo "Error: unknown arg: $1" >&2; exit "$EXIT_TOOLING_ERROR" ;;
    esac
done


# ===========================================================================
# Pre-flight
# ===========================================================================

if ! command -v yq >/dev/null 2>&1; then
    echo "Error: yq not installed. 'brew install yq' or 'apt install yq'." >&2
    exit "$EXIT_TOOLING_ERROR"
fi
if [ ! -f "$SECRETS_YAML" ]; then
    echo "Error: $SECRETS_YAML not found in $(pwd)." >&2
    exit "$EXIT_TOOLING_ERROR"
fi
# Malformed-YAML pre-flight. Without this, a syntactically-broken
# secrets.yaml falls into the per-secret yq calls below + each one
# emits stderr noise then propagates whatever exit code yq picks
# (usually 1) up through `set -e`. The test framework expects
# EXIT_TOOLING_ERROR (2) for malformed YAML so it can distinguish
# "your YAML is broken" from "drift detected" (1). Mirrors the same
# check in `validate-secrets.sh`. Added Day-5 PR #109 after the
# template-wide gap surfaced in CI; other services' copies will need
# the same fix synced back through the template (Session-2 owns).
if ! yq eval '.' "$SECRETS_YAML" >/dev/null 2>&1; then
    echo "Error: $SECRETS_YAML is malformed YAML. Run 'yq eval . $SECRETS_YAML' to see the parse error." >&2
    exit "$EXIT_TOOLING_ERROR"
fi


# ===========================================================================
# Build the would-be content into a variable, then either write OR diff
# ===========================================================================

generate_content() {
    cat <<'HEADER'
# .env.example
# AUTO-GENERATED from secrets.yaml by scripts/gen-env-example.sh.
# DO NOT EDIT BY HAND — your changes will be overwritten on the next run.
#
# To use locally:
#   cp .env.example .env.local
#   Then fill in REAL VALUES in .env.local (which is gitignored).

# ---------------------------------------------------------------------------
# Non-secret runtime configuration (NOT in secrets.yaml — just env wiring)
# ---------------------------------------------------------------------------

# ENVIRONMENT — one of: local | staging | production.
ENVIRONMENT=local

# LOG_LEVEL — Python logging threshold (DEBUG | INFO | WARNING | ERROR).
LOG_LEVEL=INFO

# LANGFUSE_TRACING_ENABLED — set "true" to ship LLM traces to rishi-6.
# Default "false" so local dev doesn't need real Langfuse keys.
LANGFUSE_TRACING_ENABLED=false


# ---------------------------------------------------------------------------
# Secrets (1-to-1 with secrets.yaml declarations)
# ---------------------------------------------------------------------------
HEADER

    # One block per declared secret. We re-read each field via yq because
    # the description can be multi-line; piping it through `sed 's/^/# /'`
    # turns it into a comment block that's safe to commit.
    local count
    count=$(yq eval '.secrets | length' "$SECRETS_YAML")
    local index=0
    while [ "$index" -lt "$count" ]; do
        local name description local_source ci_source production_source
        name=$(yq eval ".secrets[$index].name" "$SECRETS_YAML")
        description=$(yq eval ".secrets[$index].description" "$SECRETS_YAML")
        local_source=$(yq eval ".secrets[$index].source.local // \"(not required locally)\"" "$SECRETS_YAML")
        ci_source=$(yq eval ".secrets[$index].source.ci // \"(not required in CI)\"" "$SECRETS_YAML")
        production_source=$(yq eval ".secrets[$index].source.production // \"(not required in production)\"" "$SECRETS_YAML")

        echo ""
        echo "# $name"
        # Comment-prefix the description so it lands as plain text in the
        # output file. `sed` is read-only here — never edits files in place.
        echo "$description" | sed 's/^/#   /'
        echo "# Source: local=$local_source"
        echo "#         ci=$ci_source"
        echo "#         production=$production_source"
        echo "$name="

        index=$((index + 1))
    done
}

WOULD_BE_CONTENT=$(generate_content)

if [ "$CHECK_MODE" -eq 1 ]; then
    # --check: diff would-be against current; exit 1 on drift. No writes.
    if [ ! -f "$ENV_EXAMPLE" ]; then
        echo "DRIFT: $ENV_EXAMPLE doesn't exist; would be generated."
        exit "$EXIT_DRIFT"
    fi
    if diff -q <(printf '%s\n' "$WOULD_BE_CONTENT") "$ENV_EXAMPLE" >/dev/null; then
        echo "OK: $ENV_EXAMPLE matches secrets.yaml. No drift."
        exit "$EXIT_OK"
    else
        echo "DRIFT: $ENV_EXAMPLE differs from would-be generated content."
        echo "Run 'bash scripts/gen-env-example.sh' to regenerate."
        diff <(printf '%s\n' "$WOULD_BE_CONTENT") "$ENV_EXAMPLE" || true
        exit "$EXIT_DRIFT"
    fi
fi

# Default (no --check): write the would-be content to .env.example.
printf '%s\n' "$WOULD_BE_CONTENT" > "$ENV_EXAMPLE"
echo "OK: regenerated $ENV_EXAMPLE from $SECRETS_YAML."
exit "$EXIT_OK"


# ===========================================================================
# RELATED FILES:
#   ../secrets.yaml              — the manifest this script reads
#   ../.env.example              — the file this script writes
#   validate-secrets.sh          — confirms every required value is set
#   sync-github-secrets.sh       — pushes missing values to GitHub
#   tests/test_gen_env_example.sh
#                                — drift + happy-path tests
# ===========================================================================
