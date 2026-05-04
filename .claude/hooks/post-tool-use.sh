#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  HOOK: post-tool-use.sh                                                 ║
# ║                                                                          ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                            ║
# ║  After every Claude Code tool use that performs a git commit, append   ║
# ║  a structured entry to the session's SESSION-N-LOG.md diary.           ║
# ║                                                                          ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                       ║
# ║  Sessions write code in their own scope. Every time a session commits  ║
# ║  to git, this hook runs automatically. It detects which session is     ║
# ║  active (from env var YRAL_SESSION_ID, set in the session's startup    ║
# ║  prompt), reads the latest commit, and appends a one-block entry to    ║
# ║  the session's diary file. This is what makes laptop-crash recovery    ║
# ║  fast: read the diary, you know where work was.                        ║
# ║                                                                          ║
# ║  🔗 HOW IT FITS                                                          ║
# ║  - Triggered by: Claude Code's PostToolUse hook event                   ║
# ║  - Runs ONLY when the tool call was a Bash invocation that included    ║
# ║    'git commit' — silent for everything else                           ║
# ║  - Reads env: YRAL_SESSION_ID (set by session startup prompt)          ║
# ║  - Writes: yral-rishi-agent-plan-and-discussions/multi-session-...    ║
# ║    /session-logs/SESSION-N-LOG.md                                      ║
# ║                                                                          ║
# ║  ⭐ START HERE                                                           ║
# ║  Read main(); the rest is helpers.                                     ║
# ║                                                                          ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

# Read Claude Code's hook payload from stdin (JSON)
# Hook payload contains: tool_name, tool_input, tool_response
HOOK_PAYLOAD=$(cat)

# We only care about Bash tool uses — git commits go through Bash
TOOL_NAME=$(echo "$HOOK_PAYLOAD" | jq -r '.tool_name // ""')
if [ "$TOOL_NAME" != "Bash" ]; then
  # Not a Bash call → silent exit, no-op
  exit 0
fi

# Extract the command that was run from the tool_input
COMMAND=$(echo "$HOOK_PAYLOAD" | jq -r '.tool_input.command // ""')

# Only proceed if the command included 'git commit'
if ! echo "$COMMAND" | grep -q "git commit"; then
  exit 0
fi

# Determine which session is active — falls back to "coordinator" if unset
SESSION_ID="${YRAL_SESSION_ID:-coordinator}"

# Resolve the log file path
REPO_ROOT="/Users/rishichadha/Claude Projects/yral-rishi-agent"
LOG_FILE="$REPO_ROOT/yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-${SESSION_ID}-LOG.md"

# If the log file doesn't exist (session-id is unfamiliar or coordinator), create from template
if [ ! -f "$LOG_FILE" ]; then
  TEMPLATE="$REPO_ROOT/yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/templates/SESSION-N-LOG-TEMPLATE.md"
  if [ -f "$TEMPLATE" ]; then
    cp "$TEMPLATE" "$LOG_FILE"
  else
    # Bare-minimum init if template missing
    cat > "$LOG_FILE" <<EMPTY_INIT
# Session $SESSION_ID LOG
> Append-only diary. Most recent entries at TOP.

EMPTY_INIT
  fi
fi

# Get the latest commit info (SHA, message, files changed)
COMMIT_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown")
COMMIT_MSG=$(git -C "$REPO_ROOT" log -1 --pretty=%s 2>/dev/null || echo "(no commit message)")
FILES_CHANGED=$(git -C "$REPO_ROOT" diff-tree --no-commit-id --name-only -r HEAD 2>/dev/null || echo "(unknown)")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Build the new entry in a temp file (avoids heredoc-inside-$() bash bug
# that broke on apostrophes/quotes in commit messages — DEP-002 fix
# 2026-05-04, Session 1 caught this).
NEW_ENTRY_FILE=$(mktemp)
{
  printf '## %s — %s\n' "$TIMESTAMP" "$COMMIT_SHA"
  printf '### Action\n%s\n\n' "$COMMIT_MSG"
  printf '### Files touched\n'
  echo "$FILES_CHANGED" | sed 's/^/- /'
  printf '\n### Notes\n'
  printf 'Auto-appended by post-tool-use.sh hook. Add manual milestone entries\n'
  printf 'above this line when crossing a meaningful boundary.\n\n---\n\n'
} > "$NEW_ENTRY_FILE"

# Insert the new entry at the TOP of the file (after the header lines)
# OR append at the end if no entries exist yet.
if grep -q '^## ' "$LOG_FILE"; then
  # File has prior entries — splice new entry before the first '## ' line.
  # We do this with a simple file split rather than awk -v entry=... because
  # awk's -v assignment chokes on multi-line values.
  HEADER_END_LINE=$(grep -n '^## ' "$LOG_FILE" | head -1 | cut -d: -f1)
  HEADER_END_LINE=$((HEADER_END_LINE - 1))
  TMP_OUTPUT=$(mktemp)
  head -n "$HEADER_END_LINE" "$LOG_FILE" > "$TMP_OUTPUT"
  cat "$NEW_ENTRY_FILE" >> "$TMP_OUTPUT"
  tail -n +"$((HEADER_END_LINE + 1))" "$LOG_FILE" >> "$TMP_OUTPUT"
  mv "$TMP_OUTPUT" "$LOG_FILE"
else
  # No prior entries — append at end
  printf '\n' >> "$LOG_FILE"
  cat "$NEW_ENTRY_FILE" >> "$LOG_FILE"
fi

# Cleanup temp file
rm -f "$NEW_ENTRY_FILE"

# Silent success — don't spam Claude Code with output
exit 0

# ══════════════════════════════════════════════════════════════════════
# RELATED FILES
# ──────────────
# - .claude/scripts/regenerate-master-status.sh — reads logs to rebuild MASTER-STATUS
# - yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/06-STATE-PERSISTENCE-AND-RESUME.md
# ══════════════════════════════════════════════════════════════════════
