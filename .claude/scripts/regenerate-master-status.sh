#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  SCRIPT: regenerate-master-status.sh                                    ║
# ║                                                                          ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                            ║
# ║  Rebuilds MASTER-STATUS.md from each SESSION-N-STATE.md + git log,     ║
# ║  giving Rishi a one-glance morning view per I13.                       ║
# ║                                                                          ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                       ║
# ║  Every 15 minutes (via launchd), this script reads each session's       ║
# ║  current state, the recent git log, the open cross-session deps, and    ║
# ║  rewrites MASTER-STATUS.md so Rishi can see "where are we" in 30s.     ║
# ║  ADHD-friendly: ONE file to scan, sections in priority order.           ║
# ║                                                                          ║
# ║  🔗 HOW IT FITS                                                          ║
# ║  - Triggered: launchd cron every 15 min (post-build) OR manual run     ║
# ║  - Reads: session-state/SESSION-*-STATE.md, cross-session-deps,        ║
# ║    git log, optionally sentry-baseline.csv (when it exists)             ║
# ║  - Writes: MASTER-STATUS.md                                              ║
# ║                                                                          ║
# ║  ⭐ START HERE                                                           ║
# ║  Read main(). Each section is built by a dedicated function.           ║
# ║                                                                          ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

# launchd runs this with a minimal env: no PATH, no HOME, no gh auth context.
# Restore the user shell's PATH so we can find git + gh + jq + yq, and set
# HOME so gh CLI can read its config at ~/.config/gh/hosts.yml.
# (D.2 fix 2026-05-05 — Codex flagged "gh CLI not configured" in MASTER-STATUS
# auto-regen output because launchd context lacks user PATH.)
export HOME="${HOME:-/Users/rishichadha}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"

# Repo root — adjust if location ever changes (it shouldn't per A1)
REPO_ROOT="/Users/rishichadha/Claude Projects/yral-rishi-agent"
COORD_DIR="$REPO_ROOT/yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination"
MASTER_STATUS="$COORD_DIR/MASTER-STATUS.md"
TIMESTAMP=$(date "+%Y-%m-%d %H:%M IST")

# Build the new MASTER-STATUS in a temp file, then atomic-move
TMP_OUTPUT=$(mktemp)

# ──────────────────────────────────────────────────────────────────────
# HEADER
# ──────────────────────────────────────────────────────────────────────
cat > "$TMP_OUTPUT" <<HEADER
# 🚦 MASTER STATUS — yral-rishi-agent v2 build
> Auto-updated every 15 min by regenerate-master-status.sh. Last update: $TIMESTAMP.

HEADER

# ──────────────────────────────────────────────────────────────────────
# AWAITING RISHI — read first
# ──────────────────────────────────────────────────────────────────────
cat >> "$TMP_OUTPUT" <<'SECTION_HEADER'
═════════════════════════════════════════════════════════════
  ❓ AWAITING RISHI  ← read this first
═════════════════════════════════════════════════════════════

SECTION_HEADER

# Pending PRs awaiting Rishi YES — query GitHub if gh is configured
if command -v gh >/dev/null 2>&1; then
  PR_LIST=$(gh pr list --repo dolr-ai/yral-rishi-agent --state open --label "awaiting-rishi" --limit 10 --json number,title,headRefName 2>/dev/null || echo "")
  if [ -n "$PR_LIST" ] && [ "$PR_LIST" != "[]" ]; then
    echo "  📋 Pending PRs awaiting your YES:" >> "$TMP_OUTPUT"
    echo "$PR_LIST" | jq -r '.[] | "     • PR #\(.number): \(.title) [\(.headRefName)]"' >> "$TMP_OUTPUT"
  else
    echo "  No PRs awaiting your YES right now." >> "$TMP_OUTPUT"
  fi
else
  echo "  (gh CLI not configured — cannot fetch PR list automatically)" >> "$TMP_OUTPUT"
fi
echo "" >> "$TMP_OUTPUT"

# ──────────────────────────────────────────────────────────────────────
# SESSION HEALTH
# ──────────────────────────────────────────────────────────────────────
cat >> "$TMP_OUTPUT" <<'SECTION_HEADER'
═════════════════════════════════════════════════════════════
  📊 SESSION HEALTH
═════════════════════════════════════════════════════════════

SECTION_HEADER

# For each known session, read its STATE file and extract a 4-line summary
for SESSION_NUM in 1 2 3 4 5; do
  STATE_FILE="$COORD_DIR/session-state/SESSION-${SESSION_NUM}-STATE.md"
  if [ -f "$STATE_FILE" ]; then
    # Extract the first non-empty header + a current-task line
    SESSION_TITLE=$(grep -m 1 "^# Session" "$STATE_FILE" | sed 's/^# //' || echo "Session $SESSION_NUM")
    LAST_UPDATE=$(grep -m 1 "Updated:" "$STATE_FILE" | sed 's/.*Updated: //' || echo "(no update)")
    CURRENT_TASK=$(awk '/^## CURRENT TASK/,/^## /' "$STATE_FILE" | head -3 | tail -2 | tr '\n' ' ' || echo "(unknown)")

    echo "  🟢 $SESSION_TITLE" >> "$TMP_OUTPUT"
    echo "     Last update: $LAST_UPDATE" >> "$TMP_OUTPUT"
    echo "     Now: $CURRENT_TASK" >> "$TMP_OUTPUT"
    echo "" >> "$TMP_OUTPUT"
  else
    echo "  ⚪ Session $SESSION_NUM: NOT YET LAUNCHED" >> "$TMP_OUTPUT"
    echo "" >> "$TMP_OUTPUT"
  fi
done

# ──────────────────────────────────────────────────────────────────────
# AUTO-MERGED IN LAST 24H
# ──────────────────────────────────────────────────────────────────────
cat >> "$TMP_OUTPUT" <<'SECTION_HEADER'
═════════════════════════════════════════════════════════════
  🤖 AUTO-MERGED IN LAST 24H (per I14, no Rishi YES needed)
═════════════════════════════════════════════════════════════

SECTION_HEADER

# Query GitHub for merged PRs in last 24h with auto-merge label
if command -v gh >/dev/null 2>&1; then
  AUTO_MERGED=$(gh pr list --repo dolr-ai/yral-rishi-agent --state merged --label "auto-merged" --limit 20 --json number,title,mergedAt 2>/dev/null || echo "")
  if [ -n "$AUTO_MERGED" ] && [ "$AUTO_MERGED" != "[]" ]; then
    # Filter to last 24h via jq
    YESTERDAY=$(date -u -v-1d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "1 day ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")
    echo "$AUTO_MERGED" | jq -r --arg yesterday "$YESTERDAY" \
      '.[] | select(.mergedAt > $yesterday) | "  • PR #\(.number): \(.title)"' >> "$TMP_OUTPUT" 2>/dev/null || \
      echo "  (none)" >> "$TMP_OUTPUT"
  else
    echo "  None." >> "$TMP_OUTPUT"
  fi
else
  echo "  (gh CLI not configured)" >> "$TMP_OUTPUT"
fi
echo "" >> "$TMP_OUTPUT"

# ──────────────────────────────────────────────────────────────────────
# OPEN CROSS-SESSION DEPENDENCIES
# ──────────────────────────────────────────────────────────────────────
cat >> "$TMP_OUTPUT" <<'SECTION_HEADER'
═════════════════════════════════════════════════════════════
  🔗 OPEN CROSS-SESSION DEPENDENCIES
═════════════════════════════════════════════════════════════

SECTION_HEADER

DEPS_FILE="$COORD_DIR/cross-session-dependencies.md"
if [ -f "$DEPS_FILE" ]; then
  # Extract the OPEN section
  awk '/^## OPEN/,/^## RESOLVED/ { print }' "$DEPS_FILE" | head -30 | sed 's/^## OPEN/  Open dependencies:/' | sed 's/^## RESOLVED//' >> "$TMP_OUTPUT"
fi
echo "" >> "$TMP_OUTPUT"

# ──────────────────────────────────────────────────────────────────────
# LATENCY BASELINE
# ──────────────────────────────────────────────────────────────────────
cat >> "$TMP_OUTPUT" <<'SECTION_HEADER'
═════════════════════════════════════════════════════════════
  📈 LATENCY BASELINE (yesterday from sentry.rishi.yral.com)
═════════════════════════════════════════════════════════════

SECTION_HEADER

BASELINE_CSV="$REPO_ROOT/yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/daily-baseline.csv"
if [ -f "$BASELINE_CSV" ]; then
  # Show the most recent row
  LATEST_ROW=$(tail -1 "$BASELINE_CSV")
  echo "  Latest: $LATEST_ROW" >> "$TMP_OUTPUT"
  echo "  V2 hard target (per E1): each user-interactive endpoint p95 ≤ 0.5 × yral-chat-ai p95" >> "$TMP_OUTPUT"
else
  echo "  Will populate when Sentry baseline cron lands (Session 1 Day 0.5)." >> "$TMP_OUTPUT"
fi
echo "" >> "$TMP_OUTPUT"

# ──────────────────────────────────────────────────────────────────────
# TODAY'S CRITICAL PATH
# ──────────────────────────────────────────────────────────────────────
cat >> "$TMP_OUTPUT" <<'SECTION_HEADER'
═════════════════════════════════════════════════════════════
  ⏰ TODAY'S CRITICAL PATH
═════════════════════════════════════════════════════════════

  Auto-generated section. Coordinator overwrites manually with the
  single highest-priority action. Defaults to "review pending PRs"
  if not set.

SECTION_HEADER

# Atomic move into place — only one writer at a time
mv "$TMP_OUTPUT" "$MASTER_STATUS"

# Silent success
exit 0

# ══════════════════════════════════════════════════════════════════════
# RELATED FILES
# ──────────────
# - .claude/hooks/post-tool-use.sh — the hook that updates per-session logs
# - launchd plist (separate file) — schedules this script every 15 min
# - yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/06-STATE-PERSISTENCE-AND-RESUME.md
# ══════════════════════════════════════════════════════════════════════
