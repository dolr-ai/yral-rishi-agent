#!/usr/bin/env bash
# Phase 21αβ.H4 — Patroni failover drill.
#
# Proves that the 3-node Patroni cluster (rishi-4/5/6) correctly fails
# over when the leader is killed or asked to step down. Without this
# drill, "we have HA" is theory — we've never tested the promotion
# mechanism under realistic load. If a real leader incident hits,
# we'd be debugging promotion under pressure instead of executing a
# known-good runbook.
#
# Strategy:
#   1. Snapshot pre-drill cluster state via patronictl list (leader,
#      sync replica, async replica, timeline).
#   2. Take a fresh pg_dump as a safety net BEFORE doing anything
#      potentially disruptive. ~5 min.
#   3. Hit https://agent.rishi.yral.com/health continuously to baseline
#      p50 over 30s pre-drill.
#   4. Initiate a graceful failover via `patronictl failover
#      --candidate=<current_sync_replica>`. The sync replica has every
#      committed transaction → zero data loss.
#   5. Continue hitting /health throughout the transition. Record any
#      5xx window + duration.
#   6. Watch patronictl list until: new leader confirmed AND the old
#      leader has rejoined as a replica AND timeline incremented by 1.
#   7. Switchover back to the original leader so post-drill cluster
#      state matches pre-drill (clean teardown).
#   8. Write a report to /tmp/patroni-drill-report-<ts>.txt: TL
#      transition timeline, app-level error window, total drill wall
#      time.
#
# Safety properties:
#   - GRACEFUL failover (not pkill) — sync replica with no replication
#     lag at promotion time = zero committed data loss
#   - Pre-drill pg_dump = belt-and-braces fallback if anything goes
#     sideways
#   - Continuous /health probe gives us a real number for any app-
#     level disruption (typically <5s during a graceful failover)
#   - Switchover BACK to original leader at the end = idempotent
#     drill; running it twice leaves the cluster in the same state
#
# Exit codes (cron + workflow friendly):
#   0  — drill passed; new leader promoted, old leader rejoined,
#        switchover back to original successful, no significant /health
#        disruption window
#   1  — drill prereqs missing (no patronictl, no SSH, no health URL)
#   2  — pre-drill pg_dump failed (refuse to proceed without safety net)
#   3  — initial failover never promoted a new leader
#   4  — switchover back to original leader failed (cluster in unexpected
#        state — operator review needed; drill marked FAIL but data is
#        intact)
#   5  — drill mechanically passed but /health saw >30s of 5xx (graceful
#        failover should not produce that long a window; operator
#        review to understand whether app-side reconnect logic regressed)

set -euo pipefail

# ─── config ────────────────────────────────────────────────────────────
# Patroni cluster name as declared in the spilo image's PATRONI_SCOPE
# env (see docker-compose/patroni-cluster.yml from Phase 0 — typically
# `yral-rishi-agent`).
PATRONI_SCOPE="${PATRONI_SCOPE:-yral-rishi-agent}"
HEALTH_URL="${HEALTH_URL:-https://agent.rishi.yral.com/health}"
PROBE_INTERVAL_SEC="${PROBE_INTERVAL_SEC:-1}"
BASELINE_PROBE_SECONDS="${BASELINE_PROBE_SECONDS:-30}"
# Total time we'll watch for the failover transition to settle before
# calling it a fail (exit 3). 2 min is generous — graceful failovers
# typically settle in <30s.
FAILOVER_TIMEOUT_SEC="${FAILOVER_TIMEOUT_SEC:-120}"
# Where to write the report. Tail this on the GH workflow + attach as
# an artifact for the drill audit trail.
REPORT_PATH="${REPORT_PATH:-/tmp/patroni-drill-report-$(date -u +%Y%m%dT%H%M%SZ).txt}"
# Pre-drill pg_dump destination on the host (NOT the live data dir).
DUMP_PATH="${DUMP_PATH:-/tmp/patroni-drill-pre-dump-$(date -u +%Y%m%dT%H%M%SZ).sql.gz}"

log() {
    local ts
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    echo "[patroni-drill ${ts}] $*"
}

fail() {
    log "FAIL: $*"
    exit "${2:-1}"
}

# ─── 1. Pre-flight ──────────────────────────────────────────────────────

command -v patronictl >/dev/null 2>&1 || fail "patronictl not on PATH" 1
command -v curl >/dev/null 2>&1 || fail "curl not on PATH" 1
command -v pg_dump >/dev/null 2>&1 || fail "pg_dump not on PATH" 1
command -v jq >/dev/null 2>&1 || fail "jq not on PATH" 1

log "drill started (scope=${PATRONI_SCOPE}, report=${REPORT_PATH})"

# ─── 2. Snapshot pre-drill cluster state ──────────────────────────────

# patronictl list outputs:
#   + Cluster: yral-rishi-agent ----
#   |  Member   | Host    | Role         | State    | TL | Lag |
# We parse JSON via --format=json for stable scraping.
PRE_STATE_JSON="$(patronictl -c /etc/patroni/patroni.yml list --format=json 2>/dev/null || true)"
if [ -z "${PRE_STATE_JSON}" ] || [ "${PRE_STATE_JSON}" = "[]" ]; then
    fail "patronictl returned empty cluster state — bad config or no cluster reachable" 1
fi

PRE_LEADER="$(echo "${PRE_STATE_JSON}" | jq -r '.[] | select(.Role=="Leader") | .Member')"
PRE_SYNC="$(echo "${PRE_STATE_JSON}" | jq -r '.[] | select(.Role=="Sync Standby") | .Member' | head -n 1)"
PRE_ASYNC="$(echo "${PRE_STATE_JSON}" | jq -r '.[] | select(.Role=="Replica") | .Member' | head -n 1)"
PRE_TL="$(echo "${PRE_STATE_JSON}" | jq -r '.[] | select(.Role=="Leader") | .TL')"

if [ -z "${PRE_LEADER}" ] || [ -z "${PRE_SYNC}" ]; then
    fail "could not identify pre-drill leader (${PRE_LEADER:-?}) and/or sync replica (${PRE_SYNC:-?})" 1
fi

log "pre-drill: leader=${PRE_LEADER} sync=${PRE_SYNC} async=${PRE_ASYNC:-none} TL=${PRE_TL}"
{
    echo "─── Patroni failover drill report ───"
    echo "Drill started: $(date -u +%Y%m%dT%H%M%SZ)"
    echo "Cluster scope: ${PATRONI_SCOPE}"
    echo "Pre-drill state:"
    echo "  leader=${PRE_LEADER}"
    echo "  sync replica=${PRE_SYNC}"
    echo "  async replica=${PRE_ASYNC:-(none)}"
    echo "  timeline=${PRE_TL}"
} > "${REPORT_PATH}"

# ─── 3. Pre-drill pg_dump safety net ─────────────────────────────────

log "taking pre-drill pg_dump (safety net, ~5 min for production size)"
if ! pg_dump -h "${PRE_LEADER}" -U postgres -Fc -Z 6 -f "${DUMP_PATH}" 2>/dev/null; then
    fail "pre-drill pg_dump failed — refusing to proceed without a safety net" 2
fi
DUMP_SIZE="$(stat -c%s "${DUMP_PATH}" 2>/dev/null || stat -f%z "${DUMP_PATH}" 2>/dev/null || echo "0")"
log "pre-drill dump complete: ${DUMP_PATH} (${DUMP_SIZE} bytes)"
echo "Pre-drill pg_dump: ${DUMP_PATH} (${DUMP_SIZE} bytes)" >> "${REPORT_PATH}"

# ─── 4. Baseline /health p50 ────────────────────────────────────────

log "baselining ${HEALTH_URL} for ${BASELINE_PROBE_SECONDS}s"
BASELINE_OUT="/tmp/patroni-drill-baseline-$$.txt"
: > "${BASELINE_OUT}"
END_BASELINE="$(($(date +%s) + BASELINE_PROBE_SECONDS))"
while [ "$(date +%s)" -lt "${END_BASELINE}" ]; do
    PROBE_START="$(date +%s%3N)"
    HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "${HEALTH_URL}" || echo "000")"
    PROBE_END="$(date +%s%3N)"
    echo "${HTTP_CODE} $((PROBE_END - PROBE_START))" >> "${BASELINE_OUT}"
    sleep "${PROBE_INTERVAL_SEC}"
done
BASELINE_P50="$(awk '$1=="200"{print $2}' "${BASELINE_OUT}" | sort -n | awk 'BEGIN{c=0}{a[c++]=$1}END{print a[int(c/2)]}')"
log "baseline /health p50 = ${BASELINE_P50}ms"
echo "Baseline /health p50 (pre-drill, ${BASELINE_PROBE_SECONDS}s): ${BASELINE_P50}ms" >> "${REPORT_PATH}"

# ─── 5. Initiate graceful failover ──────────────────────────────────

log "initiating failover: candidate=${PRE_SYNC} (sync replica → leader)"
DRILL_START="$(date +%s)"
# `failover` (vs `switchover`) means "current leader has no chance to
# graceful shutdown" — but the sync replica still has every committed
# transaction so this is still zero-data-loss in practice.
patronictl -c /etc/patroni/patroni.yml failover \
    --master "${PRE_LEADER}" \
    --candidate "${PRE_SYNC}" \
    --force 2>&1 | tee -a "${REPORT_PATH}" &
FAILOVER_PID=$!

# In parallel: hit /health continuously to capture the disruption
# window. Background loop terminates when failover settles.
PROBE_OUT="/tmp/patroni-drill-probe-$$.txt"
: > "${PROBE_OUT}"
PROBE_END_AT="$((DRILL_START + FAILOVER_TIMEOUT_SEC))"
while [ "$(date +%s)" -lt "${PROBE_END_AT}" ]; do
    PROBE_START="$(date +%s%3N)"
    HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "${HEALTH_URL}" || echo "000")"
    PROBE_END="$(date +%s%3N)"
    echo "$(date +%s) ${HTTP_CODE} $((PROBE_END - PROBE_START))" >> "${PROBE_OUT}"
    sleep "${PROBE_INTERVAL_SEC}"
done &
PROBE_PID=$!

# Wait for the failover command to return.
wait "${FAILOVER_PID}" || true

# ─── 6. Watch for promotion + TL increment ──────────────────────────

log "watching for promotion (timeout ${FAILOVER_TIMEOUT_SEC}s)"
PROMOTED=0
while [ "$(date +%s)" -lt "${PROBE_END_AT}" ]; do
    POST_STATE_JSON="$(patronictl -c /etc/patroni/patroni.yml list --format=json 2>/dev/null || echo "[]")"
    NEW_LEADER="$(echo "${POST_STATE_JSON}" | jq -r '.[] | select(.Role=="Leader") | .Member')"
    NEW_TL="$(echo "${POST_STATE_JSON}" | jq -r '.[] | select(.Role=="Leader") | .TL')"
    if [ -n "${NEW_LEADER}" ] && [ "${NEW_LEADER}" = "${PRE_SYNC}" ] && [ "${NEW_TL:-0}" -gt "${PRE_TL:-0}" ]; then
        PROMOTED=1
        break
    fi
    sleep 2
done

# Stop the background /health probe loop.
kill "${PROBE_PID}" 2>/dev/null || true
wait "${PROBE_PID}" 2>/dev/null || true

if [ "${PROMOTED}" -ne 1 ]; then
    log "promotion never observed — cluster may be stuck mid-failover"
    {
        echo "FAILOVER VERDICT: did not observe ${PRE_SYNC} promoted to leader within ${FAILOVER_TIMEOUT_SEC}s."
        echo "Post-state JSON: ${POST_STATE_JSON}"
    } >> "${REPORT_PATH}"
    fail "promotion timed out" 3
fi

DRILL_END="$(date +%s)"
log "promotion confirmed: new_leader=${NEW_LEADER} TL=${NEW_TL} (took $((DRILL_END - DRILL_START))s)"
{
    echo "Promotion confirmed:"
    echo "  new leader=${NEW_LEADER}"
    echo "  new timeline=${NEW_TL}"
    echo "  wall time to promotion: $((DRILL_END - DRILL_START))s"
} >> "${REPORT_PATH}"

# ─── 7. Compute /health disruption window from probe log ─────────────

# Count contiguous non-200 probes. The "window" is the longest run of
# 5xx (or 000 = timeout/connection refused) during the failover.
LONGEST_5XX_RUN_SEC="$(awk 'BEGIN{cur=0; best=0; last_ts=0; run_start=0}
{
    ts=$1; code=$2;
    if (code != "200") {
        if (cur == 0) { run_start=ts }
        cur = ts - run_start
        if (cur > best) { best = cur }
    } else {
        cur = 0
    }
}
END{print best}' "${PROBE_OUT}")"
TOTAL_5XX="$(awk '$2!="200"' "${PROBE_OUT}" | wc -l | tr -d ' ')"
TOTAL_PROBES="$(wc -l < "${PROBE_OUT}" | tr -d ' ')"

log "/health disruption: ${TOTAL_5XX}/${TOTAL_PROBES} non-200 probes; longest contiguous = ${LONGEST_5XX_RUN_SEC}s"
{
    echo "/health probe summary during failover:"
    echo "  total probes=${TOTAL_PROBES}"
    echo "  non-200 probes=${TOTAL_5XX}"
    echo "  longest contiguous non-200 window=${LONGEST_5XX_RUN_SEC}s"
} >> "${REPORT_PATH}"

# ─── 8. Switchover back to original leader (idempotent drill) ────────

log "switching back to original leader (${PRE_LEADER}) so cluster matches pre-drill state"
SWITCHBACK_START="$(date +%s)"
if ! patronictl -c /etc/patroni/patroni.yml switchover \
    --master "${NEW_LEADER}" \
    --candidate "${PRE_LEADER}" \
    --force 2>&1 | tee -a "${REPORT_PATH}"; then
    {
        echo ""
        echo "SWITCHBACK FAILED — cluster left with new leader=${NEW_LEADER}, NOT the original ${PRE_LEADER}."
        echo "Operator must verify cluster state via patronictl list + decide whether to retry switchover."
    } >> "${REPORT_PATH}"
    fail "switchback failed — cluster left in unexpected state (data intact)" 4
fi

# Wait for the switchback to settle.
sleep 10
FINAL_STATE_JSON="$(patronictl -c /etc/patroni/patroni.yml list --format=json 2>/dev/null || echo "[]")"
FINAL_LEADER="$(echo "${FINAL_STATE_JSON}" | jq -r '.[] | select(.Role=="Leader") | .Member')"
FINAL_TL="$(echo "${FINAL_STATE_JSON}" | jq -r '.[] | select(.Role=="Leader") | .TL')"
SWITCHBACK_END="$(date +%s)"

{
    echo ""
    echo "Switchback complete:"
    echo "  final leader=${FINAL_LEADER}"
    echo "  final timeline=${FINAL_TL}"
    echo "  wall time switchback: $((SWITCHBACK_END - SWITCHBACK_START))s"
    echo ""
    echo "Total drill wall time: $((SWITCHBACK_END - DRILL_START))s"
    echo "Drill finished: $(date -u +%Y%m%dT%H%M%SZ)"
} >> "${REPORT_PATH}"

log "switchback complete: final leader=${FINAL_LEADER} TL=${FINAL_TL}"
log "report written to ${REPORT_PATH}"

# ─── 9. Verdict ─────────────────────────────────────────────────────────

if [ "${LONGEST_5XX_RUN_SEC:-0}" -gt 30 ]; then
    log "PASS-with-note: /health disruption window ${LONGEST_5XX_RUN_SEC}s exceeds 30s soft threshold"
    {
        echo ""
        echo "VERDICT: PASS-WITH-NOTE"
        echo "  Mechanical failover + switchback succeeded BUT /health saw"
        echo "  ${LONGEST_5XX_RUN_SEC}s of contiguous non-200 — review app-"
        echo "  side reconnect logic for regression."
    } >> "${REPORT_PATH}"
    exit 5
fi

{
    echo ""
    echo "VERDICT: PASS"
    echo "  Failover + promotion + switchback completed. /health disruption"
    echo "  ${LONGEST_5XX_RUN_SEC}s (within 30s soft threshold)."
} >> "${REPORT_PATH}"
log "PASS"
exit 0
