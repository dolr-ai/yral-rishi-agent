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
# checked-out copy at /Users/rishichadha/Claude Projects/yral-rishi-agent/.
# When coordinator switched to PR #145's branch to edit, every session
# working in that same path got pulled onto PR #145's branch too — drift!
# After this script: coordinator stays at the original path, each session
# gets its own checked-out copy. No drift.
#
# 🔗 HOW IT FITS
# - Triggered: ONE-TIME setup, run by Rishi after the 2026-05-25 audit
# - Result: 5 new directories at /Users/rishichadha/Claude Projects/
#   yral-rishi-agent-worktrees/session-N/ (for N in 1..5)
# - Each session's launch prompt is updated to cd into its own worktree
# - Coordinator continues to work in the original /yral-rishi-agent/ path
#
# ⭐ START HERE
# Just run this script ONCE:
#   bash yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/setup-isolated-session-worktrees.sh
#
# RELATED FILES
# - 01-SESSION-SHARDING-AND-OWNERSHIP.md (each session's owned-paths)
# - .claude/agents/session-N-*.md (per-session launch prompts to update)
#
# ===========================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — where everything lives
# ---------------------------------------------------------------------------

COORDINATOR_REPO_PATH="/Users/rishichadha/Claude Projects/yral-rishi-agent"
WORKTREES_PARENT_DIR="/Users/rishichadha/Claude Projects/yral-rishi-agent-worktrees"


# ---------------------------------------------------------------------------
# Preflight: confirm we're in the right place + git is healthy
# ---------------------------------------------------------------------------

if [ ! -d "$COORDINATOR_REPO_PATH/.git" ]; then
    echo "ERROR: $COORDINATOR_REPO_PATH does not look like a git repo."
    echo "Adjust COORDINATOR_REPO_PATH at the top of this script."
    exit 1
fi

cd "$COORDINATOR_REPO_PATH"

# Ensure parent dir exists
mkdir -p "$WORKTREES_PARENT_DIR"


# ---------------------------------------------------------------------------
# Create one worktree per session — only if it doesn't already exist
# ---------------------------------------------------------------------------

for SESSION_NUMBER in 1 2 3 4 5; do
    WORKTREE_PATH="$WORKTREES_PARENT_DIR/session-$SESSION_NUMBER"

    if [ -d "$WORKTREE_PATH" ]; then
        echo "session-$SESSION_NUMBER: worktree already exists at $WORKTREE_PATH — skipping."
        continue
    fi

    # Create the worktree checked out at the latest main commit. The session
    # can switch to its own branch (session-N/<feature>) from there.
    echo "session-$SESSION_NUMBER: creating worktree at $WORKTREE_PATH (checked out at main)..."
    git worktree add "$WORKTREE_PATH" main
done


# ---------------------------------------------------------------------------
# Verify + report
# ---------------------------------------------------------------------------

echo ""
echo "===== All worktrees ====="
git worktree list

echo ""
echo "===== Done ====="
echo ""
echo "Each session should now launch in its own worktree:"
for SESSION_NUMBER in 1 2 3 4 5; do
    echo "  Session $SESSION_NUMBER:  cd '$WORKTREES_PARENT_DIR/session-$SESSION_NUMBER'"
done
echo ""
echo "Coordinator stays at: $COORDINATOR_REPO_PATH"
echo ""
echo "Each session's launch prompt in .claude/agents/session-N-*.md"
echo "should be updated to cd into its own worktree path."
