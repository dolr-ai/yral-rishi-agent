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
# PER-SERVICE LOCAL-DEV-DEFAULT LOOKUP (Codex PR #137 round-7 BLOCKER 2 fix):
# Most secrets emit `NAME=` (blank — devs fill in via `.env.local`),
# which is correct for true-secret credentials (SENTRY_DSN, LANGFUSE_*,
# REDIS_PASSWORD). Some secrets have a safe public local-dev value
# (notably `REDIS_URL=redis://localhost:6379/0` pointing at the
# docker-compose Redis) which a blank `.env.example` line would
# WRONGLY override the Settings default for when devs run
# `cp .env.example .env.local`.
#
# Round-8 fix: the `local_default_value_for_name()` helper below
# returns a hardcoded per-service default for the small set of
# secret names that need one (currently just `REDIS_URL` here in
# public-api). Other secret names fall through to blank emission.
#
# Why a hardcoded lookup (not a `secrets.yaml` schema extension):
# extending the D8 schema with a per-entry `local_default_value`
# field would force EVERY service's `secrets.yaml` to be updated +
# every service's copy of THIS script to recognize the new field —
# cross-service schema drift in the worst case. The per-service
# hardcoded case-statement is service-local: encodes service-
# specific knowledge in the service-specific script. A coordinator-
# queued follow-up syncs the same case-statement convention into
# the template's `gen-env-example.sh` so future services inherit it.
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


# ===========================================================================
# Build the would-be content into a variable, then either write OR diff
# ===========================================================================

local_default_value_for_name() {
    # WHAT: return the hardcoded local-dev default value for the given
    #       secret name, or empty string if the secret has no safe
    #       local-dev default (the common case).
    # WHEN: called once per secret while generating each `.env.example`
    #       entry — the return value becomes the right-hand side of
    #       `NAME=<value>` in the output.
    # WHY:  closes Codex PR #137 round-7 BLOCKER 2 — the generator is
    #       now the single source of truth for the local-dev default,
    #       so a clean re-run reproduces `.env.example` exactly (no
    #       post-script manual edit required, no `lint-secrets-
    #       hygiene` CI drift).
    case "$1" in
        REDIS_URL)
            # docker-compose Redis (unauthenticated) — the safe local
            # default. Production REDIS_URL points at the Sentinel
            # quorum and is PASSWORDLESS (AUTH is via REDIS_PASSWORD
            # per H3 + the 2026-05-22 rotation). See `secrets.yaml`
            # REDIS_URL description for the passwordless-URL contract.
            echo "redis://localhost:6379/0"
            ;;
        *)
            # No safe local-dev default for this secret — emit blank
            # so devs MUST fill it in via `.env.local`.
            echo ""
            ;;
    esac
}


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
        # Per-service local-dev-default lookup (Codex PR #137 round-7
        # BLOCKER 2 fix). Returns the hardcoded value for secrets like
        # REDIS_URL whose local-dev default is a safe public URL;
        # returns empty for true-secret credentials so devs fill them
        # in via .env.local.
        local local_default_value
        local_default_value=$(local_default_value_for_name "$name")
        echo "$name=$local_default_value"

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
