#!/usr/bin/env bash
# ===========================================================================
# setup-isolated-session-worktrees.sh
# ===========================================================================
#
# ⭐ THIS FILE IN ONE SENTENCE
# Sets up one isolated git worktree per session (1-5) + coordinator so that
# branch switches in one session/coordinator do NOT shift the working files
# in another.
#
# 📖 EXPLAINED FOR A NON-PROGRAMMER
# Git can have multiple "checked-out copies" of the same repo sharing one
# .git database. Before this script: all sessions + coordinator shared one
# checked-out copy. When coordinator switched to a feature branch to edit,
# every session working in that same path got pulled onto that branch too
# — drift. After this script: coordinator stays at the original path, each
# session gets its own checked-out copy at a sibling path. No drift.
#
# 🔗 HOW IT FITS
# - Triggered: ONE-TIME setup, run by Rishi after the 2026-05-25 audit
# - Result: 5 new directories at <parent-of-coordinator-repo>/
#   yral-rishi-agent-worktrees/session-N/ (for N in 1..5)
# - Each session's launch prompt should be updated to cd into its own
#   worktree (see "Session launch-prompt update" follow-up note below)
# - Coordinator continues to work in the original repo path
#
# ⭐ START HERE
# From inside the coordinator repo, just run:
#   bash yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/setup-isolated-session-worktrees.sh
#
# RELATED FILES
# - 01-SESSION-SHARDING-AND-OWNERSHIP.md (each session's owned-paths)
# - .claude/agents/session-N-*.md (per-session launch prompts to update —
#   follow-up DEP; see "Follow-up" note at the bottom of this script)
#
# ===========================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Derive paths from the current git repo — NO hardcoded user-specific paths
# ---------------------------------------------------------------------------
#
# The script is invoked from inside the coordinator repo (any path inside
# it works since git rev-parse walks up to find the top). The worktrees go
# in a sibling directory named yral-rishi-agent-worktrees, matching the
# convention sessions already use.
#
# Codex round-1 BLOCKER 2026-05-25 correctly flagged the prior hardcoded
# `/Users/rishichadha/...` paths as no-hardcoded-IPs/values violations + as
# breaking the script for any other developer. Round-2 derives both paths
# at runtime.

COORDINATOR_REPO_PATH="$(git rev-parse --show-toplevel)"
WORKTREES_PARENT_DIRECTORY="$(dirname "$COORDINATOR_REPO_PATH")/yral-rishi-agent-worktrees"


# ---------------------------------------------------------------------------
# Preflight: confirm we're in the right place + git is healthy
# ---------------------------------------------------------------------------

if [ ! -d "$COORDINATOR_REPO_PATH/.git" ]; then
    echo "ERROR: $COORDINATOR_REPO_PATH does not look like a git repo (no .git directory)."
    echo "Run this script from inside the coordinator repo."
    exit 1
fi

# Ensure the worktrees parent directory exists.
mkdir -p "$WORKTREES_PARENT_DIRECTORY"


# ---------------------------------------------------------------------------
# Create one worktree per session — only if it doesn't already exist
# ---------------------------------------------------------------------------

for SESSION_NUMBER in 1 2 3 4 5; do
    WORKTREE_PATH="$WORKTREES_PARENT_DIRECTORY/session-$SESSION_NUMBER"

    if [ -d "$WORKTREE_PATH" ]; then
        echo "session-$SESSION_NUMBER: worktree already exists at $WORKTREE_PATH — skipping."
        continue
    fi

    # Create the worktree checked out at the latest main commit. The session
    # can switch to its own branch (session-N/<feature>) from there.
    echo "session-$SESSION_NUMBER: creating worktree at $WORKTREE_PATH (checked out at main)..."
    git -C "$COORDINATOR_REPO_PATH" worktree add "$WORKTREE_PATH" main
done


# ---------------------------------------------------------------------------
# Verify + report
# ---------------------------------------------------------------------------

echo ""
echo "===== All worktrees ====="
git -C "$COORDINATOR_REPO_PATH" worktree list

echo ""
echo "===== Done ====="
echo ""
echo "Each session should now launch in its own worktree:"
for SESSION_NUMBER in 1 2 3 4 5; do
    echo "  Session $SESSION_NUMBER:  cd '$WORKTREES_PARENT_DIRECTORY/session-$SESSION_NUMBER'"
done
echo ""
echo "Coordinator stays at: $COORDINATOR_REPO_PATH"
echo ""
echo "FOLLOW-UP REQUIRED: update each session's launch prompt in"
echo ".claude/agents/session-N-*.md to cd into its own worktree path"
echo "BEFORE running git operations. Without that update, sessions will"
echo "still default to the coordinator path on next launch. Tracked as a"
echo "separate small coordinator-housekeeping PR — running this script"
echo "alone is necessary but NOT sufficient for full drift isolation."
