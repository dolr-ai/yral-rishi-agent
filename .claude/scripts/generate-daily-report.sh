#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  SCRIPT: generate-daily-report.sh                                       ║
# ║                                                                          ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                            ║
# ║  Generates Rishi's daily v2-build report (morning or evening) by         ║
# ║  reading git history + session logs + open PRs, then dispatches it via  ║
# ║  email AND Google Chat webhook (if configured).                         ║
# ║                                                                          ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                       ║
# ║  Rishi sleeps; coordinator + sessions keep building overnight. This    ║
# ║  script runs twice a day (9am + 9pm IST) via launchd. It assembles a   ║
# ║  Markdown report covering what merged, what's blocked, what needs his  ║
# ║  decision, and (mornings only) a concept-of-the-day visual learning    ║
# ║  brief. Then it emails + Google-Chat-posts the report so Rishi sees    ║
# ║  status without having to dig.                                          ║
# ║                                                                          ║
# ║  🔗 HOW IT FITS                                                          ║
# ║  - Triggered: launchd job (com.yral.rishi.agent.daily-report.plist)    ║
# ║  - Reads: git log, gh CLI, SESSION-N-STATE.md, SESSION-N-LOG.md         ║
# ║  - Writes: /tmp/yral-v2-daily-report-<date>-<morning|evening>.md       ║
# ║  - Sends: email via mail/sendmail, Google Chat via curl + webhook URL  ║
# ║                                                                          ║
# ║  📥 INPUTS                                                                ║
# ║  - REPORT_KIND env: "morning" or "evening"                              ║
# ║  - YRAL_DAILY_REPORT_EMAIL_TO env: recipient (default rishi@gobazzinga.io)
# ║  - macOS Keychain entry yral-google-chat-webhook-url under -a dolr-ai  ║
# ║    (read at runtime via `security find-generic-password`; never in    ║
# ║    env vars or files in repo — keeps the webhook secret-shaped).     ║
# ║    Rishi installs once via:                                          ║
# ║      security add-generic-password -a dolr-ai \                      ║
# ║        -s yral-google-chat-webhook-url -w '<webhook-url>' -U         ║
# ║                                                                          ║
# ║  📤 OUTPUTS / SIDE EFFECTS                                                ║
# ║  - File written to /tmp/ (read-only; not committed)                     ║
# ║  - Email sent to Rishi                                                  ║
# ║  - Google Chat post (if webhook configured)                             ║
# ║  - stdout: summary of what was sent                                     ║
# ║                                                                          ║
# ║  ⭐ START HERE                                                           ║
# ║  Read main() at the bottom. The flow is linear: assemble → dispatch.   ║
# ║                                                                          ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

# Resolve script + repo paths up-front (script runs from launchd which has
# no inherited CWD; everything must be absolute).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# launchd inherits a minimal PATH; restore it so `git`, `gh`, `python3` resolve.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export HOME="${HOME:-/Users/rishichadha}"

REPORT_KIND="${REPORT_KIND:-morning}"   # "morning" or "evening"
EMAIL_RECIPIENT="${YRAL_DAILY_REPORT_EMAIL_TO:-rishi@gobazzinga.io}"

# Read Google Chat webhook URL from macOS Keychain at runtime so it never
# lands in plain text in env vars / process listings / log files. Falls
# back to env var if Keychain entry doesn't exist (for legacy/manual
# testing scenarios).
GOOGLE_CHAT_WEBHOOK="$(security find-generic-password \
    -a dolr-ai -s yral-google-chat-webhook-url -w 2>/dev/null || true)"
if [ -z "$GOOGLE_CHAT_WEBHOOK" ]; then
    GOOGLE_CHAT_WEBHOOK="${YRAL_GOOGLE_CHAT_WEBHOOK_URL:-}"
fi

REPORT_DATE="$(date +%Y-%m-%d)"
REPORT_TIME="$(date +%H:%M)"
REPORT_FILE="/tmp/yral-v2-daily-report-${REPORT_DATE}-${REPORT_KIND}.md"


# ────────────────────────────────────────────────────────────────────────
# Section assemblers — each writes to a tmp file appended into the final
# report at the end. Kept separate for testability + clarity.
# ────────────────────────────────────────────────────────────────────────


assemble_pr_table_for_window() {
    # WHAT: produces a Markdown table of PRs merged in the last
    #       reporting-window (12h for evening, 24h-since-last-evening
    #       for morning).
    # WHEN: called once per report assembly.
    # WHY:  Rishi's first question every morning/evening is "what merged?"
    #       — this section answers it without him having to run gh CLI.

    local since_hours
    case "$REPORT_KIND" in
        morning)  since_hours=15 ;;   # covers last evening (9pm) → now (9am next day)
        evening)  since_hours=12 ;;   # covers since this morning's report
        *)        since_hours=24 ;;
    esac

    local since_iso
    since_iso="$(date -u -v "-${since_hours}H" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
        || date -u -d "${since_hours} hours ago" +%Y-%m-%dT%H:%M:%SZ)"

    echo "## 🔀 Merged in the last ${since_hours}h"
    echo
    echo "| PR | Title | Author |"
    echo "|---|---|---|"
    cd "$REPO_ROOT" || return 1
    gh pr list --state merged --search "merged:>${since_iso}" --limit 30 \
        --json number,title,author \
        --jq '.[] | "| #\(.number) | \(.title | gsub("\\|"; "\\|") | .[0:80]) | \(.author.login) |"' \
        2>/dev/null || echo "| (gh CLI unavailable; check repo directly) | | |"
    echo
}


assemble_open_pr_section() {
    echo "## ⏳ Open PRs"
    echo
    cd "$REPO_ROOT" || return 1
    local count
    count="$(gh pr list --state open --limit 100 --json number 2>/dev/null | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)"
    if [ "$count" -eq "0" ]; then
        echo "**0 open PRs.** Clean state."
    else
        echo "**${count} open PRs:**"
        echo
        echo "| PR | Title | Branch | Awaiting |"
        echo "|---|---|---|---|"
        gh pr list --state open --limit 30 --json number,title,headRefName,labels,statusCheckRollup \
            --jq '.[] | "| #\(.number) | \(.title | gsub("\\|"; "\\|") | .[0:60]) | \(.headRefName | .[0:30]) | \([.statusCheckRollup[] | select(.conclusion=="FAILURE") | .name] | join(", ") | .[0:50]) |"' \
            2>/dev/null || true
    fi
    echo
}


assemble_session_status_section() {
    echo "## 📊 Session status"
    echo
    cd "$REPO_ROOT" || return 1
    for session_state_file in yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-*-STATE.md; do
        [ -f "$session_state_file" ] || continue
        local session_name
        session_name="$(basename "$session_state_file" | sed 's/-STATE.md//')"
        echo "### ${session_name}"
        echo
        # Print first ~12 lines after the first horizontal rule or header
        head -25 "$session_state_file" | tail -20 | sed 's/^/> /'
        echo
    done
}


assemble_blockers_section() {
    echo "## 🚨 Awaiting Rishi"
    echo
    # Look for PRs with the coordinator-review-needed label or comment markers
    cd "$REPO_ROOT" || return 1
    local awaiting
    awaiting="$(gh pr list --state open --label coordinator-review-needed --json number,title --jq '.[] | "- #\(.number): \(.title)"' 2>/dev/null || true)"
    if [ -z "$awaiting" ]; then
        echo "**No PRs blocked on Rishi's decision right now.**"
    else
        echo "$awaiting"
    fi
    echo
}


assemble_concept_of_the_day_section() {
    # Morning report only — picks ONE architectural concept relevant to the
    # current Phase + writes a 3-5 min visual learning brief.
    # Rotation handled by the day-of-month modulo a small list.
    [ "$REPORT_KIND" != "morning" ] && return 0

    echo "## 🎓 Concept of the day (3-5 min read)"
    echo
    # The actual concept content is too long to embed in this Bash; the
    # coordinator generates a separate brief file at report time. This
    # section just points to it. The brief lives at:
    #   .claude/learning-briefs/<concept-slug>.md
    # The launchd job's morning invocation regenerates a fresh brief if
    # one isn't already queued. Concept selection is deterministic by
    # day-of-month modulo the brief count, so Rishi gets reproducible
    # rotation.
    local brief_dir="${REPO_ROOT}/.claude/learning-briefs"
    if [ -d "$brief_dir" ]; then
        # Pick by day-of-month
        local day_num
        day_num="$(date +%d | sed 's/^0//')"
        local briefs=( "$brief_dir"/*.md )
        local n=${#briefs[@]}
        if [ "$n" -gt "0" ]; then
            local idx=$(( (day_num - 1) % n ))
            local picked="${briefs[$idx]}"
            echo "_Today's brief: $(basename "$picked" .md)_"
            echo
            cat "$picked"
        else
            echo "_(No learning briefs queued; coordinator will write one this week.)_"
        fi
    else
        echo "_(Learning briefs directory not yet populated; coordinator will start writing these in week 2.)_"
    fi
    echo
}


assemble_todays_plan_section() {
    echo "## 🗓️ Plan"
    echo
    if [ "$REPORT_KIND" = "morning" ]; then
        echo "**Today's sequenced plan** (coordinator's working hypothesis; may adjust if blockers surface):"
    else
        echo "**Tomorrow's plan**:"
    fi
    echo
    # The coordinator populates this section by writing to /tmp/yral-v2-next-plan.md
    # at end-of-current-cycle. If that file exists, include it. Otherwise note default.
    local plan_file="/tmp/yral-v2-next-plan.md"
    if [ -f "$plan_file" ]; then
        cat "$plan_file"
    else
        echo "_(No explicit plan staged; continuing per current Phase sequencing.)_"
        echo
        echo "_To adjust: coordinator writes to ${plan_file} before next report fires._"
    fi
    echo
}


assemble_decisions_log_section() {
    echo "## 🧠 Decisions made autonomously (with reasoning)"
    echo
    # The coordinator appends to /tmp/yral-v2-decisions-log.md throughout the
    # day; this section reads + flushes it for the next cycle.
    local decisions_file="/tmp/yral-v2-decisions-log.md"
    if [ -f "$decisions_file" ] && [ -s "$decisions_file" ]; then
        cat "$decisions_file"
        # Truncate after reading so next cycle starts fresh
        : > "$decisions_file"
    else
        echo "_No major autonomous decisions logged in this cycle._"
    fi
    echo
}


# ────────────────────────────────────────────────────────────────────────
# Dispatch — write the file, then send via email + Google Chat
# ────────────────────────────────────────────────────────────────────────


send_via_email() {
    # macOS `mail` requires postfix/sendmail to be RUNNING + CONFIGURED
    # to relay externally (e.g., via Gmail SMTP). Out of the box, neither
    # is set up on a fresh macOS install — the `mail` command exits 0
    # silently but the message goes into a queue that never drains.
    #
    # We try to detect the "mail system is down" state up-front + skip
    # so we don't silently lose mail. If Rishi wants real email later,
    # he can either:
    #   (a) Configure postfix to relay through Gmail SMTP (sudo +
    #       app-password setup, ~30 min one-time)
    #   (b) Switch to a transactional API (Resend / Mailgun / SendGrid)
    #       via curl call from this function
    #   (c) Stay on Google Chat as the primary channel + skip email
    #       (current recommendation 2026-05-18)
    if ! command -v mail >/dev/null 2>&1; then
        echo "[email] SKIPPED (mail command not available)"
        return 0
    fi
    # Quick health probe: postqueue -p exits non-zero if mail system down
    if ! postqueue -p >/dev/null 2>&1; then
        echo "[email] SKIPPED (postfix mail system not running — see send_via_email() comment for fix options)"
        return 0
    fi

    local subject="🚦 yral-v2 daily report (${REPORT_KIND}) — ${REPORT_DATE}"
    mail -s "$subject" "$EMAIL_RECIPIENT" < "$REPORT_FILE" \
        && echo "[email] sent to $EMAIL_RECIPIENT" \
        || echo "[email] FAILED to send (mail command returned non-zero — check /var/log/mail.log)"
}


send_via_google_chat() {
    if [ -z "$GOOGLE_CHAT_WEBHOOK" ]; then
        echo "[google-chat] SKIPPED (webhook not in Keychain at yral-google-chat-webhook-url AND env var not set)"
        return 0
    fi

    # Google Chat webhook accepts JSON with `text` field for simple posts.
    # For the daily report we'd ideally use Cards-V2 for rich layout, but
    # the simple `text` form lets Rishi see the summary directly in chat.
    # Truncate to ~3000 chars (Google Chat limit on simple text is ~4096).
    local report_text
    report_text="$(head -c 3500 "$REPORT_FILE")"
    if [ ${#report_text} -ge 3500 ]; then
        report_text+="

... (truncated — full report in email)"
    fi

    # Build JSON safely via python3 to escape quotes/newlines
    local payload
    payload="$(python3 -c "
import json, sys
text = sys.stdin.read()
print(json.dumps({'text': text}))
" <<< "$report_text")"

    if curl -fsS -X POST -H "Content-Type: application/json" \
        -d "$payload" "$GOOGLE_CHAT_WEBHOOK" > /dev/null; then
        echo "[google-chat] sent to webhook"
    else
        echo "[google-chat] FAILED to post (curl returned non-zero)"
    fi
}


# ────────────────────────────────────────────────────────────────────────
# Main assembly + dispatch flow
# ────────────────────────────────────────────────────────────────────────


main() {
    {
        echo "# 🚦 yral-v2 daily report — ${REPORT_DATE} ${REPORT_KIND} (${REPORT_TIME} IST)"
        echo
        echo "> Auto-generated by .claude/scripts/generate-daily-report.sh."
        echo "> Phase: $(grep -m1 '^Phase:' "${REPO_ROOT}/yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/MASTER-STATUS.md" 2>/dev/null | head -c 100 || echo 'see MASTER-STATUS.md')"
        echo
        echo "---"
        echo

        # Section order: blockers FIRST (Rishi sees what's urgent),
        # then activity (what happened), then plan (what's next),
        # then learning brief (mornings only).
        assemble_blockers_section
        assemble_pr_table_for_window
        assemble_open_pr_section
        assemble_session_status_section
        assemble_decisions_log_section
        assemble_todays_plan_section
        assemble_concept_of_the_day_section

        echo "---"
        echo
        echo "_Sent via email + Google Chat. Full report at \`${REPORT_FILE}\` on Rishi's Mac._"
    } > "$REPORT_FILE"

    echo "Report assembled at ${REPORT_FILE} ($(wc -c < "$REPORT_FILE") bytes)"
    send_via_email
    send_via_google_chat
}


main "$@"
