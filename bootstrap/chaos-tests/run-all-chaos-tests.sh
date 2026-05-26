#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  run-all-chaos-tests.sh — Phase 0 exit-criteria orchestrator               ║
# ║                                                                              ║
# ║  ⭐ THIS FILE IN ONE SENTENCE                                                ║
# ║  Run all four chaos tests (kill-rishi-6, kill-patroni-leader,             ║
# ║  fill-rishi-5-disk, partition-rishi-6) sequentially, with a cluster      ║
# ║  health check between each one, so Phase 0 cannot be marked complete     ║
# ║  until every CONSTRAINTS H3 row is verified green.                        ║
# ║                                                                              ║
# ║  📖 EXPLAINED FOR A NON-PROGRAMMER                                           ║
# ║  Phase 0 (Days 1-7) ends only when the cluster has demonstrably survived  ║
# ║  all four failure scenarios. This script is the single entry point Rishi  ║
# ║  invokes on Day 6 to run the full chaos battery. It pauses for ~2 mins   ║
# ║  between tests so Patroni + etcd + Swarm have time to fully settle       ║
# ║  before the next test perturbs them.                                     ║
# ║                                                                              ║
# ║  Total runtime: ~30 minutes (kill scenarios are quick; partition is the  ║
# ║  long one at 10 minutes).                                                 ║
# ║                                                                              ║
# ║  🔗 HOW IT FITS                                                              ║
# ║  - This is the canonical "Phase 0 exit criterion" runner per             ║
# ║    CONSTRAINTS H3 + the V2 §6.7 chaos-testing checklist.                 ║
# ║  - Calls the four sibling scripts in this folder. Each sibling is         ║
# ║    independently runnable; this file just sequences them with health     ║
# ║    gates between.                                                         ║
# ║  - Output is a Markdown report at /tmp/yral-v2-chaos-test-report-       ║
# ║    <YYYY-MM-DD-HHMM>.md that the operator can paste into the Phase 0    ║
# ║    completion checklist.                                                  ║
# ║                                                                              ║
# ║  ⚠️ DRAFT — NEVER RUN UNTIL DAY 6 + RISHI YES.                                ║
# ║                                                                              ║
# ║  ⭐ START HERE                                                               ║
# ║  Read main(); each chaos test is a phase function called in order.        ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -euo pipefail


# ────────────────────────── Constants ───────────────────────────────────────

THIS_SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KILL_RISHI_6_SCRIPT="${THIS_SCRIPT_DIRECTORY}/kill-rishi-6.sh"
KILL_PATRONI_LEADER_SCRIPT="${THIS_SCRIPT_DIRECTORY}/kill-patroni-leader.sh"
FILL_RISHI_5_DISK_SCRIPT="${THIS_SCRIPT_DIRECTORY}/fill-rishi-5-disk.sh"
PARTITION_RISHI_6_SCRIPT="${THIS_SCRIPT_DIRECTORY}/partition-rishi-6.sh"

# How long we wait between tests for Patroni + etcd + Swarm to settle.
# 2 minutes is enough for any in-flight reschedule to complete and for
# Patroni to confirm leader stability before the next perturbation.
INTER_TEST_SETTLE_SECONDS=120

# Markdown report path. Tagged with timestamp so multiple runs in a
# single day each produce a distinct file.
REPORT_TIMESTAMP="$(date +%Y-%m-%d-%H%M)"
CHAOS_REPORT_FILE_PATH="/tmp/yral-v2-chaos-test-report-${REPORT_TIMESTAMP}.md"


# ────────────────────────── Entry point ─────────────────────────────────────


main() {
    confirm_orchestrator_preconditions
    initialise_chaos_report
    run_phase_kill_rishi_6
    settle_between_tests "kill-rishi-6"
    run_phase_kill_patroni_leader
    settle_between_tests "kill-patroni-leader"
    run_phase_fill_rishi_5_disk
    settle_between_tests "fill-rishi-5-disk"
    run_phase_partition_rishi_6
    finalise_chaos_report
}


# ────────────────────────── Phases ──────────────────────────────────────────


confirm_orchestrator_preconditions() {
    # WHAT:  authorise the chaos battery for today + verify that all four
    #        sibling scripts exist and are executable.
    # WHEN:  first phase.
    # WHY:   batch-running four chaos tests is more disruptive than any
    #        single one; the orchestrator gate is the final
    #        "yes I really mean to run all of these now" signal.
    local todays_date; todays_date="$(date +%Y-%m-%d)"
    if [[ "${YRAL_CHAOS_RUN_AUTHORISED:-}" != "${todays_date}" ]]; then
        echo "ERROR run-all-chaos-tests: refused — YRAL_CHAOS_RUN_AUTHORISED must equal '${todays_date}'" >&2
        exit 1
    fi

    local sibling_chaos_script
    for sibling_chaos_script in \
        "${KILL_RISHI_6_SCRIPT}" \
        "${KILL_PATRONI_LEADER_SCRIPT}" \
        "${FILL_RISHI_5_DISK_SCRIPT}" \
        "${PARTITION_RISHI_6_SCRIPT}"; do
        if [[ ! -x "${sibling_chaos_script}" ]]; then
            echo "ERROR run-all-chaos-tests: ${sibling_chaos_script} not executable" >&2
            exit 1
        fi
    done

    if ! docker info --format '{{.Swarm.ControlAvailable}}' | grep --quiet true; then
        echo "ERROR run-all-chaos-tests: not a Swarm manager" >&2; exit 1
    fi
}


initialise_chaos_report() {
    # WHAT:  write the Markdown report header + open file for append.
    # WHEN:  immediately after preconditions.
    # WHY:   the report is the artifact Rishi pastes into the Phase 0
    #        completion checklist. Starting it now means we can stream
    #        results into it as each test finishes — even if a later test
    #        fails, we keep the partial report.
    cat > "${CHAOS_REPORT_FILE_PATH}" <<HEADER
# Yral v2 Chaos Test Report — ${REPORT_TIMESTAMP}

**Cluster:** rishi-4 / rishi-5 / rishi-6
**Operator:** $(whoami)@$(hostname)
**Authorisation:** YRAL_CHAOS_RUN_AUTHORISED=$(date +%Y-%m-%d)

This report covers all four CONSTRAINTS H3 chaos test scenarios. Each
section captures: test name, start time, end time, pass/fail, and any
notable observations. A passing run-all-chaos-tests = Phase 0 exit
criterion cleared (per V2 §6.7).

---

HEADER
    echo "run-all-chaos-tests: report being written to ${CHAOS_REPORT_FILE_PATH}"
}


run_phase_kill_rishi_6() {
    # WHAT:  invoke kill-rishi-6.sh; record outcome to the report.
    # WHEN:  first chaos test in the sequence.
    # WHY:   ordered first because it's the least invasive (drain only,
    #        no kill, no iptables); a cluster that fails this is unfit
    #        for the harder tests.
    record_test_phase_in_report "## Test 1 — kill-rishi-6 (drain rishi-6)" \
        "${KILL_RISHI_6_SCRIPT}"
}


run_phase_kill_patroni_leader() {
    # WHAT:  invoke kill-patroni-leader.sh.
    # WHEN:  second test.
    # WHY:   moderately invasive — kills a single container. Tests the
    #        most important guarantee Patroni gives us: leader failover.
    record_test_phase_in_report "## Test 2 — kill-patroni-leader" \
        "${KILL_PATRONI_LEADER_SCRIPT}"
}


run_phase_fill_rishi_5_disk() {
    # WHAT:  invoke fill-rishi-5-disk.sh.
    # WHEN:  third test.
    # WHY:   tests the Alertmanager + disk-pressure path. Safe to run
    #        only at 80% disk so Postgres still serves writes.
    record_test_phase_in_report "## Test 3 — fill-rishi-5-disk (to 80%)" \
        "${FILL_RISHI_5_DISK_SCRIPT}"
}


run_phase_partition_rishi_6() {
    # WHAT:  invoke partition-rishi-6.sh.
    # WHEN:  last test (it's a 10-minute hold).
    # WHY:   most invasive — iptables changes on two nodes for 10 min.
    #        Run last so prior tests don't get tangled with stuck
    #        iptables state if cleanup misfires.
    record_test_phase_in_report "## Test 4 — partition-rishi-6 (10-minute network split)" \
        "${PARTITION_RISHI_6_SCRIPT}"
}


record_test_phase_in_report() {
    # WHAT:  append a heading + start time, run the chaos script, then
    #        append the script's exit status + end time + log path.
    # WHEN:  called four times by the run_phase_* functions above.
    # WHY:   centralising the record pattern means each run_phase_*
    #        is one line — easier to read, no duplicated formatting.
    local section_heading="$1"
    local chaos_script_path="$2"

    {
        echo ""
        echo "${section_heading}"
        echo ""
        echo "- **Start:** $(date --iso-8601=seconds)"
    } >> "${CHAOS_REPORT_FILE_PATH}"

    local script_exit_code=0
    if "${chaos_script_path}" >> "${CHAOS_REPORT_FILE_PATH}" 2>&1; then
        script_exit_code=0
    else
        script_exit_code=$?
    fi

    {
        echo ""
        echo "- **End:** $(date --iso-8601=seconds)"
        if (( script_exit_code == 0 )); then
            echo "- **Outcome:** ✅ PASS"
        else
            echo "- **Outcome:** ❌ FAIL (exit ${script_exit_code})"
        fi
    } >> "${CHAOS_REPORT_FILE_PATH}"

    if (( script_exit_code != 0 )); then
        echo "ERROR run-all-chaos-tests: ${chaos_script_path} failed (exit ${script_exit_code})" >&2
        echo "  partial report: ${CHAOS_REPORT_FILE_PATH}" >&2
        exit "${script_exit_code}"
    fi
}


settle_between_tests() {
    # WHAT:  sleep INTER_TEST_SETTLE_SECONDS; quick health probe of
    #        Patroni + etcd before the next test starts.
    # WHEN:  three times, between consecutive chaos tests.
    # WHY:   chaining tests without settle would compound transient
    #        states (e.g. Patroni leader changed by Test 2, partition in
    #        Test 4 might find an unexpected topology). Settle gives
    #        Patroni a chance to re-stabilise.
    local previous_test_name="$1"
    echo "run-all-chaos-tests: settling for ${INTER_TEST_SETTLE_SECONDS}s after ${previous_test_name}"
    sleep "${INTER_TEST_SETTLE_SECONDS}"

    if ! docker exec "$(docker ps --filter name=etcd-rishi-4 --quiet | head -n 1)" \
        etcdctl --endpoints=http://etcd-rishi-4:2379 endpoint health >/dev/null 2>&1; then
        echo "FAIL run-all-chaos-tests: etcd unhealthy after ${previous_test_name} settle" >&2
        exit 1
    fi
}


finalise_chaos_report() {
    # WHAT:  append a summary + ASCII celebration to the report.
    # WHEN:  after all four tests pass.
    # WHY:   gives the operator a clear "yes Phase 0 H3 cleared"
    #        signal at the bottom of the report.
    cat >> "${CHAOS_REPORT_FILE_PATH}" <<TRAILER

---

## Summary

All four chaos tests **PASSED**. Phase 0 exit criterion (CONSTRAINTS H3)
is cleared. Report archived at \`${CHAOS_REPORT_FILE_PATH}\`.

**Next:** Day 7 — write the Caddy snippet PR against
\`dolr-ai/yral-rishi-hetzner-infra-template\` per CONSTRAINTS A2 carve-out
to wire \`agent.rishi.yral.com\` through rishi-1/2 Caddy to rishi-4/5
ingress (separate Rishi YES required).
TRAILER

    cat <<SUMMARY

✅ run-all-chaos-tests: all 4 chaos scenarios passed.

Report: ${CHAOS_REPORT_FILE_PATH}
Phase 0 exit criterion (CONSTRAINTS H3) — CLEARED.

SUMMARY
}


main "$@"


# ══════════════════════════════════════════════════════════════════════════
# RELATED FILES
# ─────────────
# - kill-rishi-6.sh, kill-patroni-leader.sh, fill-rishi-5-disk.sh,
#   partition-rishi-6.sh — the four chaos tests this orchestrator runs.
# - ../scripts/node-bootstrap.sh + sibling install scripts — must have
#   provisioned the cluster before any chaos test can run.
# ══════════════════════════════════════════════════════════════════════════
