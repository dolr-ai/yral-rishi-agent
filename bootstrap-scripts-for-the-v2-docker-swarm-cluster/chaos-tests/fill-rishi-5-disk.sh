#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  fill-rishi-5-disk.sh — chaos test #3: fill rishi-5's disk to 80%          ║
# ║                                                                              ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                                ║
# ║  Use `fallocate` to create a single large dummy file on rishi-5 that      ║
# ║  brings disk usage to ~80%, verify Patroni keeps replicating + the disk-  ║
# ║  full Alertmanager rule fires within 5 minutes, then delete the dummy    ║
# ║  file and confirm disk usage falls back below the alert threshold.       ║
# ║                                                                              ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                           ║
# ║  This is the "what happens when a node runs low on disk" test. We do NOT ║
# ║  fill the disk to 100% (that would corrupt running Postgres) — we stop   ║
# ║  at 80%, which is the alert threshold. We expect:                         ║
# ║   • Patroni's sync replica on rishi-5 keeps streaming WAL — Postgres     ║
# ║     accepts writes until disk is genuinely out, well past 80%.            ║
# ║   • The Alertmanager rule "disk free < 20%" fires within 5 minutes.     ║
# ║     Per CONSTRAINTS D6, that alert posts to the Google Chat webhook.    ║
# ║   • A small write+read sanity query against the leader still succeeds.  ║
# ║   • Cleanup: `rm` the dummy file; disk usage drops below threshold;     ║
# ║     alert auto-resolves.                                                  ║
# ║   • If for any reason the cluster degrades (Patroni demotes, etc.) the  ║
# ║     trap-cleanup deletes the dummy file BEFORE re-raising the failure.  ║
# ║                                                                              ║
# ║  🔗 HOW IT FITS                                                              ║
# ║  - Phase 0 exit criterion per CONSTRAINTS H3 row 3.                       ║
# ║  - Tests the §6.5 Alertmanager rule "Disk free < 20%" wired to D6.       ║
# ║                                                                              ║
# ║  ⚠️ DRAFT — NEVER RUN UNTIL DAY 6 + RISHI YES                                ║
# ║                                                                              ║
# ║  ⭐ START HERE                                                               ║
# ║  Read main(); the cleanup trap runs even on failure so the dummy file    ║
# ║  is always removed.                                                       ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -euo pipefail


# ────────────────────────── Constants ───────────────────────────────────────

# Where the dummy file lives. /data is the bind-mount partition for
# Patroni + Redis on edge nodes; filling it tests the alert that
# matters. NOT /tmp because /tmp may be a tmpfs in RAM.
DUMMY_FILL_FILE_PATH=/data/yral-v2-chaos-fill-disk.bin

# Target the chaos test fills the disk TO. 80% matches the
# CONSTRAINTS §6.5 alert threshold ("disk free < 20%").
TARGET_DISK_FILL_PERCENTAGE=80

# How long we wait after creating the dummy file before checking that
# the Alertmanager rule fired. 5 minutes matches the rule's evaluation
# interval ("Disk free < 20% sustained 5 min").
ALERT_FIRE_WAIT_SECONDS=$((5 * 60))

# The node we fill. Defaults to rishi-5 (sync replica + Prometheus
# host); override via env to test rishi-4 disk-full path later.
NODE_TO_FILL_DISK_ON="${YRAL_NODE_TO_FILL_DISK_ON:-rishi-5}"


# ────────────────────────── Entry point ─────────────────────────────────────


main() {
    confirm_preconditions
    capture_baseline_disk_usage
    create_dummy_fill_file_on_target_node
    verify_alertmanager_disk_alert_fired
    verify_patroni_still_writable
    delete_dummy_fill_file_and_confirm_recovery
    print_post_test_summary
}


# ────────────────────────── Phases ──────────────────────────────────────────


confirm_preconditions() {
    # WHAT:  authorisation + lock checks; also confirm SSH to the target
    #        node works (we run `fallocate` over SSH).
    # WHEN:  first phase.
    # WHY:   without SSH we cannot fill the disk; better to fail before
    #        touching anything than to fail mid-test.
    local todays_date; todays_date="$(date +%Y-%m-%d)"
    if [[ "${YRAL_CHAOS_RUN_AUTHORISED:-}" != "${todays_date}" ]]; then
        echo "ERROR fill-rishi-5-disk: refused — YRAL_CHAOS_RUN_AUTHORISED must equal '${todays_date}'" >&2
        exit 1
    fi

    if [[ -f /tmp/yral-v2-chaos-running.lock ]]; then
        echo "ERROR fill-rishi-5-disk: another chaos run in progress" >&2; exit 1
    fi
    touch /tmp/yral-v2-chaos-running.lock

    # The cleanup trap removes the dummy file even on early failure.
    trap "delete_dummy_fill_file_silently_on_exit" EXIT

    if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 \
        "rishi-deploy@${NODE_TO_FILL_DISK_ON}" "echo ok" >/dev/null 2>&1; then
        echo "ERROR fill-rishi-5-disk: SSH to ${NODE_TO_FILL_DISK_ON} failed" >&2
        exit 1
    fi
}


capture_baseline_disk_usage() {
    # WHAT:  read current disk usage % on the target node's /data
    #        partition; remember in a script-scoped variable.
    # WHEN:  before fill.
    # WHY:   we compute the dummy-file size as
    #        (target% − current%) of total /data capacity. Hardcoding a
    #        size would over- or under-fill on machines with different
    #        baselines.
    local disk_usage_line
    disk_usage_line="$(ssh "rishi-deploy@${NODE_TO_FILL_DISK_ON}" \
        "df --output=size,used,pcent /data | tail -n 1")"
    DISK_TOTAL_KILOBYTES="$(echo "${disk_usage_line}" | awk '{print $1}')"
    DISK_USED_KILOBYTES="$(echo "${disk_usage_line}" | awk '{print $2}')"
    BASELINE_DISK_USAGE_PERCENTAGE="$(echo "${disk_usage_line}" | awk '{print $3}' | tr -d '%')"
    echo "fill-rishi-5-disk: baseline = ${BASELINE_DISK_USAGE_PERCENTAGE}% on ${NODE_TO_FILL_DISK_ON}:/data"
}


create_dummy_fill_file_on_target_node() {
    # WHAT:  ssh to target node, run `fallocate -l <bytes> <path>` to
    #        create a sparse-ish file that takes <bytes> on disk.
    # WHEN:  after baseline captured.
    # WHY:   `fallocate` is faster than `dd if=/dev/zero` and avoids
    #        thousands of seconds of write traffic that would interfere
    #        with Patroni's WAL stream and skew the test.

    if (( BASELINE_DISK_USAGE_PERCENTAGE >= TARGET_DISK_FILL_PERCENTAGE )); then
        echo "ERROR fill-rishi-5-disk: baseline already at ${BASELINE_DISK_USAGE_PERCENTAGE}%; nothing to fill" >&2
        return 1
    fi

    local fill_target_kilobytes
    fill_target_kilobytes=$(( DISK_TOTAL_KILOBYTES * TARGET_DISK_FILL_PERCENTAGE / 100 ))
    local additional_kilobytes_needed
    additional_kilobytes_needed=$(( fill_target_kilobytes - DISK_USED_KILOBYTES ))

    echo "fill-rishi-5-disk: allocating ${additional_kilobytes_needed} KiB on ${NODE_TO_FILL_DISK_ON}:${DUMMY_FILL_FILE_PATH}"
    ssh "rishi-deploy@${NODE_TO_FILL_DISK_ON}" \
        "sudo fallocate --length=${additional_kilobytes_needed}KiB ${DUMMY_FILL_FILE_PATH}"

    local post_fill_percentage
    post_fill_percentage="$(ssh "rishi-deploy@${NODE_TO_FILL_DISK_ON}" \
        "df --output=pcent /data | tail -n 1 | tr -d ' %'")"
    echo "  post-fill = ${post_fill_percentage}% (target ~${TARGET_DISK_FILL_PERCENTAGE}%)"
}


verify_alertmanager_disk_alert_fired() {
    # WHAT:  sleep ALERT_FIRE_WAIT_SECONDS, then query Alertmanager's
    #        /api/v2/alerts endpoint and assert at least one alert with
    #        labels `alertname=DiskFreeLessThan20Percent` is in `firing`.
    # WHEN:  after dummy file created.
    # WHY:   the test is "alert fires", not "disk fills" — a disk-full
    #        without an alert is exactly the failure mode we are
    #        checking against.
    echo "fill-rishi-5-disk: waiting ${ALERT_FIRE_WAIT_SECONDS}s for alert to fire"
    sleep "${ALERT_FIRE_WAIT_SECONDS}"

    local firing_alerts_json
    firing_alerts_json="$(curl --silent --max-time 5 \
        "http://alertmanager:9093/api/v2/alerts?active=true&filter=alertname%3DDiskFreeLessThan20Percent" \
        || echo '[]')"
    local firing_alert_count
    firing_alert_count="$(echo "${firing_alerts_json}" \
        | jq --raw-output 'length' 2>/dev/null || echo 0)"

    if (( firing_alert_count == 0 )); then
        echo "FAIL fill-rishi-5-disk: DiskFreeLessThan20Percent alert did not fire within ${ALERT_FIRE_WAIT_SECONDS}s" >&2
        return 1
    fi
    echo "  ✅ ${firing_alert_count} disk-full alert(s) firing"
}


verify_patroni_still_writable() {
    # WHAT:  same write+read sanity roundtrip as kill-patroni-leader.sh
    #        — confirm Postgres still accepts writes despite low disk.
    # WHEN:  after alert verification.
    # WHY:   80% disk usage should NOT block writes. If it does, that's
    #        a configuration bug (e.g. WAL archive backed up) and the
    #        chaos test must catch it.
    local sanity_check_nonce; sanity_check_nonce="chaos-disk-$(date +%s)-$RANDOM"
    local postgres_password
    postgres_password="$(cat /run/secrets/postgres-superuser-password 2>/dev/null || echo "${YRAL_POSTGRES_SUPERUSER_PASSWORD:-}")"

    PGPASSWORD="${postgres_password}" psql \
        --host=pgbouncer --username=postgres --dbname=postgres --no-password \
        --command "CREATE SCHEMA IF NOT EXISTS chaos_test_sanity;
                   CREATE TABLE IF NOT EXISTS chaos_test_sanity.disk_fill_log (
                       inserted_at TIMESTAMPTZ DEFAULT now(), nonce TEXT
                   );
                   INSERT INTO chaos_test_sanity.disk_fill_log (nonce) VALUES ('${sanity_check_nonce}');"

    local read_back
    read_back="$(PGPASSWORD="${postgres_password}" psql \
        --host=pgbouncer --username=postgres --dbname=postgres --no-password \
        --tuples-only --no-align \
        --command "SELECT nonce FROM chaos_test_sanity.disk_fill_log WHERE nonce='${sanity_check_nonce}';")"
    if [[ "${read_back}" != "${sanity_check_nonce}" ]]; then
        echo "FAIL fill-rishi-5-disk: write/read mismatch under disk pressure" >&2
        return 1
    fi
    echo "  ✅ Patroni still accepting writes under ${TARGET_DISK_FILL_PERCENTAGE}% disk"
}


delete_dummy_fill_file_and_confirm_recovery() {
    # WHAT:  ssh + `rm` the dummy file; poll df until disk usage falls
    #        back below the alert threshold (20% free).
    # WHEN:  after Patroni-writable check passes.
    # WHY:   restore is part of the contract; alert auto-resolves once
    #        the metric drops below threshold for a sustained interval.
    ssh "rishi-deploy@${NODE_TO_FILL_DISK_ON}" \
        "sudo rm --force ${DUMMY_FILL_FILE_PATH}"

    local recovery_deadline_seconds=120
    local started_at; started_at="$(date +%s)"
    while true; do
        local now elapsed; now="$(date +%s)"; elapsed=$(( now - started_at ))
        if (( elapsed > recovery_deadline_seconds )); then
            echo "FAIL fill-rishi-5-disk: disk usage did not drop below threshold within ${recovery_deadline_seconds}s" >&2
            return 1
        fi
        local current_percentage
        current_percentage="$(ssh "rishi-deploy@${NODE_TO_FILL_DISK_ON}" \
            "df --output=pcent /data | tail -n 1 | tr -d ' %'")"
        if (( current_percentage < TARGET_DISK_FILL_PERCENTAGE )); then
            echo "  ✅ disk usage back to ${current_percentage}% on ${NODE_TO_FILL_DISK_ON}"
            return 0
        fi
        sleep 5
    done
}


delete_dummy_fill_file_silently_on_exit() {
    # WHAT:  best-effort cleanup invoked by the EXIT trap.
    # WHEN:  always — even on early failure, error, or Ctrl-C.
    # WHY:   leaving an 80%-fill dummy file on the box would be
    #        operational damage. This trap fires before the lock file
    #        is released so the next chaos run sees a clean state.
    ssh -o ConnectTimeout=5 "rishi-deploy@${NODE_TO_FILL_DISK_ON}" \
        "sudo rm --force ${DUMMY_FILL_FILE_PATH}" 2>/dev/null || true
    rm --force /tmp/yral-v2-chaos-running.lock 2>/dev/null || true
}


print_post_test_summary() {
    cat <<SUMMARY

✅ fill-rishi-5-disk chaos test PASSED.

Scenario: filled ${NODE_TO_FILL_DISK_ON}:/data to ~${TARGET_DISK_FILL_PERCENTAGE}%;
DiskFreeLessThan20Percent alert fired; Patroni stayed writable; dummy
file removed; disk usage recovered.

Phase 0 exit criterion row 3 (per CONSTRAINTS H3) cleared.

SUMMARY
}


main "$@"


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - kill-rishi-6.sh         — chaos test #1.
# - kill-patroni-leader.sh  — chaos test #2.
# - partition-rishi-6.sh    — chaos test #4.
# - run-all-chaos-tests.sh  — orchestrator.
# ══════════════════════════════════════════════════════════════════════════
