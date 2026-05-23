#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# new-service.sh — 1-command spawner for a new yral-rishi-agent-* service.
#
# ⭐ START HERE: this script rsync-copies the template folder to a
# sibling `yral-rishi-agent-<purpose>/` folder (excluding the spawner
# itself via `--exclude`, so it never lands in the spawned service in
# the first place — no removal needed per A1 spirit), perl-substitutes
# the placeholder names in every file, and renames
# `secrets.yaml.template` to `secrets.yaml`.
#
# WHY ONE-COMMAND?
# Per F1 + F16 — adding a new service should be friction-free. The
# alternative (manual copy + manual sed across 17+ files) is error-prone
# and tedious; this script makes it boring.
#
# WHAT THIS SCRIPT DOES NOT DO (per A2.1 — strict single concern):
#   - It does NOT validate the spawned service's secrets.yaml schema —
#     that's `scripts/validate-secrets.sh` (PR 4).
#   - It does NOT push GitHub Secrets — that's `scripts/sync-github-secrets.sh`
#     (PR 4).
#   - It does NOT regenerate `.env.example` from `secrets.yaml` — that's
#     `scripts/gen-env-example.sh` (PR 4).
#   - It does NOT write the per-service CI workflow at the root
#     `.github/workflows/` (coordinator-only per I9). The spawned service
#     carries its own copy at `.github/workflows/per-service-ci.yml`;
#     coordinator stages it at root.
#   - It does NOT `rm` ANYTHING — per A1 spirit ("never delete without
#     explicit YES"), even our own transient artifacts. The script avoids
#     CREATING anything it would need to clean up: rsync's `--exclude`
#     keeps the spawner from landing in the spawned service in the first
#     place; `perl -i` does in-place edits without `.bak` backups.
#
# USAGE:
#   bash yral-rishi-agent-new-service-template/scripts/new-service.sh \
#        yral-rishi-agent-<english-purpose>
#   bash yral-rishi-agent-new-service-template/scripts/new-service.sh \
#        yral-rishi-agent-hello-world --dry-run
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# `errexit + nounset + pipefail` — three Bash safety nets:
# fail-fast on any non-zero exit, unset variables, or pipe failures.
set -euo pipefail


# ===========================================================================
# Constants
# ===========================================================================

# Folder name the spawner copies FROM. Matches the template's actual
# folder name in the monorepo.
TEMPLATE_FOLDER="yral-rishi-agent-new-service-template"

# Placeholder strings the template uses. Substituted everywhere at spawn.
PLACEHOLDER_HYPHENATED="yral-rishi-agent-new-service-template"
PLACEHOLDER_UNDERSCORED="new_service_template"
PLACEHOLDER_VARIABLE='${PROJECT_NAME}'

# B3 name pattern: must start with `yral-rishi-agent-`, then a
# lowercase letter, then any combination of lowercase letters / digits /
# hyphens, ending with a letter or digit (not a hyphen).
NAME_PATTERN='^yral-rishi-agent-[a-z][a-z0-9-]*[a-z0-9]$'

# Docker Swarm refuses stack names over 63 characters; we apply the
# same cap to the service name since SWARM_STACK = PROJECT_NAME.
SWARM_NAME_LIMIT=63


# ===========================================================================
# Helpers
# ===========================================================================

# Print usage + exit non-zero.
print_usage_and_exit() {
    echo "Usage: $0 <new-service-name> [--dry-run]"
    echo ""
    echo "Examples:"
    echo "  $0 yral-rishi-agent-hello-world"
    echo "  $0 yral-rishi-agent-payments-and-creator-earnings --dry-run"
    echo ""
    echo "Name rules (B3 + Swarm):"
    echo "  - must match pattern: $NAME_PATTERN"
    echo "  - must be <= $SWARM_NAME_LIMIT characters"
    exit 1
}


# ===========================================================================
# Argument parsing
# ===========================================================================

DRY_RUN=0
TARGET_NAME=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) print_usage_and_exit ;;
        --*) echo "Error: unknown flag $1"; print_usage_and_exit ;;
        *)
            if [ -n "$TARGET_NAME" ]; then
                echo "Error: only one service name allowed"
                print_usage_and_exit
            fi
            TARGET_NAME="$1"
            shift
            ;;
    esac
done

[ -z "$TARGET_NAME" ] && print_usage_and_exit


# ===========================================================================
# Validate target name (B3 + Swarm 63-char limit)
# ===========================================================================

if [[ ! "$TARGET_NAME" =~ $NAME_PATTERN ]]; then
    echo "Error: '$TARGET_NAME' does not match $NAME_PATTERN"
    echo "Hint: names look like yral-rishi-agent-<english-purpose>"
    exit 1
fi

if [ ${#TARGET_NAME} -gt $SWARM_NAME_LIMIT ]; then
    echo "Error: '$TARGET_NAME' is ${#TARGET_NAME} chars; max is $SWARM_NAME_LIMIT (Swarm stack limit)"
    exit 1
fi


# ===========================================================================
# Derive substitution strings
# ===========================================================================

# Strip the `yral-rishi-agent-` prefix to get the suffix; replace
# hyphens with underscores for Postgres-friendly identifiers.
TARGET_SUFFIX_HYPHENATED="${TARGET_NAME#yral-rishi-agent-}"
TARGET_SUFFIX_UNDERSCORED="${TARGET_SUFFIX_HYPHENATED//-/_}"


# ===========================================================================
# Resolve paths (template + target)
# ===========================================================================

# Use the repo root so the script works from any cwd inside the repo.
REPO_ROOT="$(git rev-parse --show-toplevel)"
TEMPLATE_PATH="$REPO_ROOT/$TEMPLATE_FOLDER"
TARGET_PATH="$REPO_ROOT/$TARGET_NAME"

if [ ! -d "$TEMPLATE_PATH" ]; then
    echo "Error: template folder not found at $TEMPLATE_PATH"
    exit 1
fi

if [ -e "$TARGET_PATH" ]; then
    echo "Error: target path already exists: $TARGET_PATH"
    echo "Refusing to overwrite (per A1 — no deletions without explicit YES)"
    exit 1
fi


# ===========================================================================
# Dry-run preview
# ===========================================================================

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN — would perform:"
    echo "  1. rsync $TEMPLATE_PATH/ → $TARGET_PATH/ (excluding scripts/new-service.sh)"
    echo "  2. Substitute '$PLACEHOLDER_HYPHENATED' → '$TARGET_NAME' in every file"
    echo "  3. Substitute '$PLACEHOLDER_UNDERSCORED' → '$TARGET_SUFFIX_UNDERSCORED' in every file"
    echo "  4. Substitute '$PLACEHOLDER_VARIABLE' → '$TARGET_NAME' in every file"
    echo "  5. Rename $TARGET_PATH/secrets.yaml.template → secrets.yaml"
    echo "  6. DEP-010 post-spawn check: every env.local.fixture is add-able"
    echo "     (git add --dry-run shows 'add ...' line, not silently ignored)"
    echo ""
    echo "No changes written. Re-run without --dry-run to actually create."
    echo "(A1 spirit — this script never 'rm's anything; rsync --exclude keeps"
    echo " the spawner out of the spawned service so no cleanup is needed.)"
    exit 0
fi


# ===========================================================================
# Actual spawn
# ===========================================================================

echo "Spawning $TARGET_NAME from $TEMPLATE_FOLDER..."

# Step 1: copy the template folder via rsync, EXCLUDING the spawner.
# Per A1 spirit ("never delete"), the spawner is kept OUT of the
# spawned service in the first place — we never have to clean it up.
# The D8 bridge scripts (validate-secrets.sh / sync-github-secrets.sh /
# gen-env-example.sh, PR 4) are NOT excluded; they're per-service tools.
#
# Trailing slashes matter: `$TEMPLATE_PATH/` copies CONTENTS of the
# folder into `$TARGET_PATH/` (which rsync creates if absent).
# `-a` = archive mode (recursive + permissions + timestamps + symlinks).
rsync -a --exclude='scripts/new-service.sh' "$TEMPLATE_PATH/" "$TARGET_PATH/"

# Steps 2-4: substitute the three placeholder strings across every
# regular file in the target.
#
# perl -i (NOT sed) for two reasons:
#   1. `perl -i` does in-place edits WITHOUT leaving `.bak` files
#      anywhere (sed's `-i` flag differs between GNU and BSD; the
#      portable `-i.bak` form leaves backups we'd then need to `rm`,
#      which violates A1 spirit even at "small blast radius").
#   2. Values are passed via @ARGV + shifted off in BEGIN — NOT
#      shell-expanded into the perl source. Reason: if we expanded
#      PLACEHOLDER_VARIABLE (literal `${PROJECT_NAME}`) directly into
#      the perl script, perl would parse it as a perl variable
#      reference at regex-compile time, resolve `$PROJECT_NAME` to
#      empty (perl has no such var), leaving `\Q\E` as an EMPTY
#      pattern that matches between every character → exponential
#      content bloat. Passing as @ARGV data + interpolating from a
#      perl scalar in `\Q$var\E` is one-pass + safe (verified at
#      integration test time, PR 5).
find "$TARGET_PATH" -type f -print0 | while IFS= read -r -d '' file; do
    perl -i -pe '
        BEGIN {
            ($ph_hyphenated, $ph_underscored, $ph_variable,
             $tgt_name, $tgt_suffix) = splice @ARGV, 0, 5;
        }
        s/\Q$ph_hyphenated\E/$tgt_name/g;
        s/\Q$ph_underscored\E/$tgt_suffix/g;
        s/\Q$ph_variable\E/$tgt_name/g;
    ' "$PLACEHOLDER_HYPHENATED" "$PLACEHOLDER_UNDERSCORED" "$PLACEHOLDER_VARIABLE" \
      "$TARGET_NAME" "$TARGET_SUFFIX_UNDERSCORED" \
      "$file"
done

# Step 5: rename the secrets manifest from `.template` form to the
# spawned-service form. `mv` is fine — it RENAMES a tracked file, it
# does NOT delete anything (A1 spirit preserved).
mv "$TARGET_PATH/secrets.yaml.template" "$TARGET_PATH/secrets.yaml"


# ===========================================================================
# Step 6: DEP-010 post-spawn verification
# ===========================================================================
#
# Every `env.local.fixture` in the spawned tree MUST be visible to
# `git add` — i.e. NOT silently swallowed by .gitignore. The
# DEP-010 bug was that fixtures shipped as literal `.env.local`,
# which collides with .gitignore:25 and got dropped at spawn time
# (3 of 4 spawned services hit red CI before the rename to
# `env.local.fixture`). This check guards against future drift:
# someone renaming back to a gitignored filename, or a new
# .gitignore rule that catches `env.local.fixture`.
#
# Codex PR #121 round-7 chose `git add --dry-run` (over
# `git check-ignore`) because it surfaces the exact tracking
# outcome the spawn cares about: would `git add` add the file
# (success → "add 'path'" line), or silently ignore it (empty
# output or "ignored by .gitignore" error)? Anything other than
# an "add '...'" line is a failure.
# `find ... -print0` + `while IFS= read -r -d ''` is the standard
# null-delimited iteration pattern. It handles paths with spaces or
# newlines correctly — `$TARGET_PATH` lives under "/Users/.../Claude
# Projects/..." on dev macs so the space-in-path case is real.
while IFS= read -r -d '' fixture_file; do
    # Convert the absolute path to a repo-relative path so the
    # `git add --dry-run` invocation below resolves the same way a
    # caller running `git add <path>` from $REPO_ROOT would. Without
    # the strip, git would still work (git tolerates absolute paths
    # under the worktree) but the error output below reads cleaner
    # with repo-relative paths.
    relative_fixture_path="${fixture_file#$REPO_ROOT/}"
    # Probe what `git add` would actually do for this path. `--dry-run`
    # never mutates the index; `2>&1` captures the "ignored by …"
    # message git writes to stderr when a path is gitignored; `|| true`
    # prevents `set -e` from aborting the spawn on a non-zero git exit
    # (gitignored paths produce non-zero — we want to keep iterating
    # so the error message below can surface all violations, not just
    # the first).
    dry_run_output="$(git -C "$REPO_ROOT" add --dry-run -- "$relative_fixture_path" 2>&1)" || true
    # The exact tracking outcome the spawn cares about: would `git
    # add` actually add the file? A success produces an `add '…'` line
    # on stdout. Gitignored paths produce an "ignored by .gitignore"
    # message (or empty output on some git versions); anything that
    # isn't an `add '…'` line means the fixture would be silently
    # swallowed at the next real `git add`. Codex PR #121 round-7
    # chose this over `git check-ignore` because it surfaces the
    # observable spawn outcome, not just whether a rule matches.
    if ! echo "$dry_run_output" | grep -q "^add '"; then
        # Failure path: tell the operator EXACTLY which fixture
        # tripped the check + what git said + the two most likely
        # root causes so they can land the fix without re-reading
        # the DEP. `exit 1` aborts the spawn loudly — better a
        # noisy failure now than a silently-broken spawned service.
        echo "Error: post-spawn DEP-010 check failed for $relative_fixture_path"
        echo "  git add --dry-run output: $dry_run_output"
        echo "  Fixture would be silently ignored by .gitignore."
        echo "  Likely cause: rename back to .env.local OR new .gitignore"
        echo "  rule catching env.local.fixture. See DEP-010."
        exit 1
    fi
done < <(find "$TARGET_PATH" -name 'env.local.fixture' -type f -print0)


# ===========================================================================
# Success message
# ===========================================================================

echo ""
echo "Spawned $TARGET_NAME at $TARGET_PATH"
echo ""
echo "Next steps:"
echo "  1. Review the spawned files (especially project.config + secrets.yaml)"
echo "  2. cd $TARGET_PATH && docker compose build  # verify the image still builds"
echo "  3. Ask coordinator to stage $TARGET_PATH/.github/workflows/per-service-ci.yml"
echo "     at the repo root .github/workflows/$TARGET_NAME-ci.yml (per I9 — coordinator-only)"


# ===========================================================================
# RELATED FILES:
#   ../project.config              — primary substitution target (PROJECT_NAME etc.)
#   ../secrets.yaml.template       — renamed to secrets.yaml at spawn
#   ../docker-compose.yml          — substituted (container names, etc.)
#   ../docker-compose.swarm.yml    — substituted (stack name, image, secrets)
#   ../pyproject.toml              — substituted (package name)
#   ../README.md + docs            — substituted (service name everywhere)
#   ../.github/workflows/per-service-ci.yml
#                                  — substituted (path-scope + workflow name)
#   validate-secrets.sh            — D8 bridge script (PR 4) — kept by rsync
#   sync-github-secrets.sh         — D8 bridge script (PR 4) — kept by rsync
#   gen-env-example.sh             — D8 bridge script (PR 4) — kept by rsync
#
# A1 SPIRIT: this script intentionally contains NO `rm` and NO
# `find -delete` calls. The rsync --exclude pattern keeps the spawner
# out of the spawned service in the first place; perl -i avoids the
# `.bak` files sed would leave behind. Never delete what you don't
# have to create.
# ===========================================================================
