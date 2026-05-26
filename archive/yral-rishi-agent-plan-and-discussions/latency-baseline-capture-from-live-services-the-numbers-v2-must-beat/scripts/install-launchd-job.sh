#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  install-launchd-job.sh                                                 ║
# ║                                                                          ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                            ║
# ║  Render pull-sentry-baseline.plist.template into ~/Library/LaunchAgents/║
# ║  and load it into launchd so the daily 9 a.m. IST pull runs unattended.║
# ║                                                                          ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                       ║
# ║  The committed .plist.template file contains __YRAL_REPO_ROOT__ and   ║
# ║  __USER_HOME__ placeholders so it works on any machine. This script   ║
# ║  fills the placeholders in with this laptop's actual paths, drops the ║
# ║  rendered file in the spot launchd looks for it, validates it with    ║
# ║  Apple's plutil tool, then asks launchd to start watching it. After   ║
# ║  this runs once, no further action is needed — launchd handles the    ║
# ║  daily 9 a.m. trigger forever.                                        ║
# ║                                                                          ║
# ║  🔗 HOW IT FITS                                                          ║
# ║  - Reads:  pull-sentry-baseline.plist.template (sibling file)          ║
# ║  - Writes: ~/Library/LaunchAgents/                                     ║
# ║              com.dolr-ai.yral-rishi-agent.pull-sentry-baseline.plist   ║
# ║  - Creates: ~/.local/share/yral-rishi-agent/ (for log output)          ║
# ║  - Calls:  plutil -lint, launchctl bootstrap, launchctl print          ║
# ║                                                                          ║
# ║  📥 INPUTS                                                               ║
# ║  - $HOME — taken from the user's environment, used to resolve paths.   ║
# ║                                                                          ║
# ║  📤 OUTPUTS / SIDE EFFECTS                                               ║
# ║  - Rendered plist file at the LaunchAgents path above                  ║
# ║  - Log directory created if missing                                    ║
# ║  - launchd job loaded and immediately scheduled for next 9 a.m. local  ║
# ║                                                                          ║
# ║  ⭐ START HERE                                                           ║
# ║  Read main(); the rest of the file are step-by-step support functions ║
# ║  it calls in order.                                                    ║
# ╚══════════════════════════════════════════════════════════════════════╝

# Strict mode — fail loudly on any unhandled error or unset variable so an
# install problem cannot silently leave a half-installed job.
set -euo pipefail

# ────────────────────── Constants used throughout ───────────────────────────

# launchd label — must match the <key>Label</key> in the plist template.
LAUNCHD_JOB_LABEL="com.dolr-ai.yral-rishi-agent.pull-sentry-baseline"

# Where launchd looks for per-user agent plists on macOS.
USER_LAUNCH_AGENTS_DIRECTORY="${HOME}/Library/LaunchAgents"

# Where the cron-style log files land. Matches the StandardOutPath /
# StandardErrorPath fields in the plist template.
BASELINE_CRON_LOG_DIRECTORY="${HOME}/.local/share/yral-rishi-agent"

# Resolve absolute paths relative to this script so the install works
# regardless of which directory the user invoked it from.
THIS_SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_TEMPLATE_PATH="${THIS_SCRIPT_DIRECTORY}/pull-sentry-baseline.plist.template"

# Walk up from THIS_SCRIPT_DIRECTORY (scripts/ → latency-baseline-capture-…/
# → yral-rishi-agent-plan-and-discussions/ → repo root) to find the monorepo
# root that the plist template embeds as __YRAL_REPO_ROOT__.
YRAL_REPO_ROOT="$(cd "${THIS_SCRIPT_DIRECTORY}/../../.." && pwd)"

# Final destination plist path — what launchctl actually loads.
RENDERED_PLIST_PATH="${USER_LAUNCH_AGENTS_DIRECTORY}/${LAUNCHD_JOB_LABEL}.plist"


# ─────────────────────────────── Entry point ─────────────────────────────────

main() {
    # Pre-flight — refuse to run on the wrong OS or without the template.
    confirm_running_on_macos
    confirm_template_file_exists

    # Side-effect setup — ensure the LaunchAgents and log directories exist.
    ensure_directory_exists "${USER_LAUNCH_AGENTS_DIRECTORY}"
    ensure_directory_exists "${BASELINE_CRON_LOG_DIRECTORY}"

    # Render the template into a real plist with this laptop's paths.
    render_plist_template_to_user_launch_agents_directory

    # Validate the rendered plist before asking launchd to load it.
    validate_rendered_plist_with_plutil

    # If a previous version of the job is loaded, stop it cleanly first.
    bootout_existing_job_if_loaded

    # Load the new plist into launchd.
    bootstrap_job_into_launchd

    # Confirm to the user that everything worked.
    print_post_install_summary
}


# ────────────────────── Step-by-step support functions ───────────────────────


confirm_running_on_macos() {
    # WHAT:  refuse to install on Linux/WSL — launchd is macOS-only.
    # WHEN:  first thing main() does.
    # WHY:   the rendered plist would be useless on other systems and we
    #        want a clear error message instead of a confusing launchctl
    #        "command not found" later in the install.
    if [[ "$(uname -s)" != "Darwin" ]]; then
        echo "ERROR install-launchd-job: this installer is macOS-only (uname=$(uname -s))" >&2
        exit 1
    fi
}


confirm_template_file_exists() {
    # WHAT:  bail out if the .plist.template file is missing.
    # WHEN:  immediately after the OS check.
    # WHY:   rendering an empty template would yield an invalid plist and
    #        plutil would fail later — fail earlier with a clearer message.
    if [[ ! -f "${PLIST_TEMPLATE_PATH}" ]]; then
        echo "ERROR install-launchd-job: template not found at ${PLIST_TEMPLATE_PATH}" >&2
        exit 1
    fi
}


ensure_directory_exists() {
    # WHAT:  mkdir -p the given directory and verify writability.
    # WHEN:  called twice in main(): for LaunchAgents and the log folder.
    # WHY:   `launchctl bootstrap` errors out cryptically if the plist
    #        directory is missing, and the daemon would silently drop log
    #        output if the log folder cannot be created.
    local target_directory="$1"
    mkdir -p "${target_directory}"
    if [[ ! -w "${target_directory}" ]]; then
        echo "ERROR install-launchd-job: ${target_directory} is not writable" >&2
        exit 1
    fi
}


render_plist_template_to_user_launch_agents_directory() {
    # WHAT:  read the template, substitute __YRAL_REPO_ROOT__ and
    #        __USER_HOME__ with this laptop's real paths, write the result.
    # WHEN:  after the LaunchAgents directory is confirmed to exist.
    # WHY:   committing absolute paths in git would tie the file to one
    #        machine; rendering at install time keeps the committed file
    #        portable while giving launchd the absolute paths it needs.

    # Use sed with a pipe delimiter (|) instead of / so paths-with-slashes
    # do not need escaping. The template path has many slashes.
    sed \
        -e "s|__YRAL_REPO_ROOT__|${YRAL_REPO_ROOT}|g" \
        -e "s|__USER_HOME__|${HOME}|g" \
        "${PLIST_TEMPLATE_PATH}" > "${RENDERED_PLIST_PATH}"

    echo "Rendered plist → ${RENDERED_PLIST_PATH}"
}


validate_rendered_plist_with_plutil() {
    # WHAT:  run `plutil -lint` against the rendered file.
    # WHEN:  after rendering, before launchd bootstrap.
    # WHY:   plutil catches XML/property-list malformedness early; an
    #        invalid plist would be silently ignored by launchd in some
    #        macOS versions, leaving the user thinking the job was loaded.
    plutil -lint "${RENDERED_PLIST_PATH}"
}


bootout_existing_job_if_loaded() {
    # WHAT:  if a same-label job is already loaded into launchd, stop it.
    # WHEN:  every install — handles the "second time the user runs this" case.
    # WHY:   `launchctl bootstrap` fails with EEXIST when the label is
    #        already loaded; bootout-then-bootstrap makes the install idempotent.
    if launchctl print "gui/$(id -u)/${LAUNCHD_JOB_LABEL}" >/dev/null 2>&1; then
        echo "Existing launchd job found — booting it out before reinstall."
        # bootout is the inverse of bootstrap. Domain `gui/$(id -u)` is
        # the per-user GUI launchd domain (== LaunchAgents).
        launchctl bootout "gui/$(id -u)/${LAUNCHD_JOB_LABEL}"
    fi
}


bootstrap_job_into_launchd() {
    # WHAT:  ask launchd to load the rendered plist into the user domain.
    # WHEN:  after any prior version is booted out.
    # WHY:   `launchctl bootstrap` is the documented way to load a per-user
    #        agent on macOS 10.10+ (replacing the old `launchctl load`).
    launchctl bootstrap "gui/$(id -u)" "${RENDERED_PLIST_PATH}"
    echo "Bootstrapped ${LAUNCHD_JOB_LABEL} into gui/$(id -u)"
}


print_post_install_summary() {
    # WHAT:  echo a short success block with next-step pointers.
    # WHEN:  last thing main() does.
    # WHY:   the user should know it worked, where the logs live, and how
    #        to verify or uninstall — without having to read the README.
    cat <<SUMMARY

✅ Daily Sentry baseline pull is installed.

  Schedule:  every day at 9:00 a.m. local time
  Logs:      ${BASELINE_CRON_LOG_DIRECTORY}/baseline-cron.{stdout,stderr}.log
  Outputs:   ${YRAL_REPO_ROOT}/yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/{daily-baseline.csv, latest-baseline.md}

Verify the job is loaded:
  launchctl print gui/\$(id -u)/${LAUNCHD_JOB_LABEL}

Run it once now (does not affect the daily schedule):
  launchctl kickstart -k gui/\$(id -u)/${LAUNCHD_JOB_LABEL}

Uninstall (preserves the plist file per A1 — only stops the schedule):
  launchctl bootout gui/\$(id -u)/${LAUNCHD_JOB_LABEL}

Pre-flight: make sure the Keychain entry exists before tomorrow's 9 a.m.:
  security add-generic-password -U -a dolr-ai -s SENTRY_AUTH_TOKEN \\
      -w '<paste-your-token>' -T /usr/bin/security

SUMMARY
}


# Run main() with whatever arguments were passed (currently none expected).
main "$@"

# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - pull-sentry-baseline.plist.template
#       The launchd plist this script renders + loads.
# - pull-sentry-baseline.py
#       The Python script the launchd job actually runs.
# - README.md
#       First-time install + verify-it-ran instructions.
# - secrets.yaml
#       Declares the Keychain-backed SENTRY_AUTH_TOKEN secret.
# ══════════════════════════════════════════════════════════════════════════
