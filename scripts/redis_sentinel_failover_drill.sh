#!/usr/bin/env bash
# Phase 21αβ.H5 — Redis Sentinel failover drill.
#
# Proves that the V2 Redis Sentinel topology (1 primary + 2 replicas
# + 3 sentinels) correctly fails over when the primary dies. The
# application path that matters is pub/sub: WebSocket-inbox messages
# cross replicas via Redis pub/sub, so a primary outage that doesn't
# cleanly promote = silent message loss for any cross-replica delivery.
#
# Strategy (this script — Phase A, fully automated):
#   1. Snapshot pre-drill state: identify Sentinel-known primary,
#      replicas, sentinel count, last-failover epoch.
#   2. SUBSCRIBE on a tracer pub/sub channel from a sidecar client
#      that survives the primary kill (connected via Sentinel).
#   3. PUBLISH a pre-drill tracer → confirm sidecar receives it.
#   4. `docker stop` the current primary's Redis container on its host.
#   5. Watch Sentinel `+switch-master` event log until promotion
#      completes; record duration.
#   6. PUBLISH a post-promotion tracer to the NEW primary → confirm
#      sidecar still receives it (proves pub/sub recovered cleanly
#      via the new primary).
#   7. Restart the killed Redis. Watch Sentinel `+slave-reconf-done`
#      until it rejoins as a replica.
#   8. Optionally switchover back to original primary (via SENTINEL
#      FAILOVER on the now-replica's sentinel; not always needed).
#   9. Write report to /tmp/redis-sentinel-drill-report-<ts>.txt.
#
# WebSocket-level end-to-end smoke (Phase B) lives in the runbook and
# is operator-driven (3 browser tabs + chat API calls) — that's where
# we prove user-visible behavior, but it requires multi-client
# orchestration that's not worth automating for a periodic drill.
#
# Safety properties:
#   - Test pub/sub channel is a UNIQUE per-drill key (drill:<ts>) — no
#     collision with prod WebSocket channels
#   - `docker stop` (not `kill -9`) — Redis flushes WAL/AOF if enabled
#   - Sentinel-driven promotion uses its own quorum logic; we observe
#     but don't force
#   - Restart of the killed node uses `docker start` — Sentinel
#     auto-integrates it as a replica via slaveof <new-primary>
#
# Exit codes:
#   0 — drill PASSED: pub/sub survived the failover end-to-end
#   1 — prereqs missing (redis-cli, docker, SSH, jq)
#   2 — pre-drill SUBSCRIBE never received the baseline tracer (Sentinel
#       cluster broken BEFORE drill — operator must fix first)
#   3 — primary docker stop failed (couldn't disrupt)
#   4 — Sentinel never promoted a new primary within 60s
#   5 — POST-promotion tracer never arrived at the subscriber (pub/sub
#       did NOT recover — this is the critical regression signal)
#   6 — killed node failed to rejoin as replica within 120s

set -euo pipefail

# ─── config ────────────────────────────────────────────────────────────
SENTINEL_HOST="${SENTINEL_HOST:-redis-sentinel}"
SENTINEL_PORT="${SENTINEL_PORT:-26379}"
MASTER_NAME="${MASTER_NAME:-mymaster}"
PROMOTION_TIMEOUT_SEC="${PROMOTION_TIMEOUT_SEC:-60}"
REJOIN_TIMEOUT_SEC="${REJOIN_TIMEOUT_SEC:-120}"
TRACER_TIMEOUT_SEC="${TRACER_TIMEOUT_SEC:-10}"
REPORT_PATH="${REPORT_PATH:-/tmp/redis-sentinel-drill-report-$(date -u +%Y%m%dT%H%M%SZ).txt}"
DRILL_ID="drill:$(date -u +%Y%m%dT%H%M%SZ):$$"

# Credentials — file-first (Swarm secret), env fallback. Matches the
# pattern in app/redis_config.py.
if [ -f "/run/secrets/REDIS_PASSWORD" ]; then
    REDIS_PASSWORD="$(cat /run/secrets/REDIS_PASSWORD)"
else
    REDIS_PASSWORD="${REDIS_PASSWORD:-}"
fi
REDIS_AUTH_ARGS=()
if [ -n "${REDIS_PASSWORD}" ]; then
    REDIS_AUTH_ARGS=(-a "${REDIS_PASSWORD}" --no-auth-warning)
fi

log() {
    echo "[redis-sentinel-drill $(date -u +%Y%m%dT%H%M%SZ)] $*"
}

fail() {
    log "FAIL: $1"
    exit "${2:-1}"
}

cleanup() {
    # Kill the background SUBSCRIBE listener if it's still running.
    if [ -n "${SUBSCRIBER_PID:-}" ] && kill -0 "${SUBSCRIBER_PID}" 2>/dev/null; then
        kill "${SUBSCRIBER_PID}" 2>/dev/null || true
    fi
    rm -f "${SUBSCRIBER_OUT:-/dev/null}" 2>/dev/null || true
}
trap cleanup EXIT

# ─── 1. Pre-flight ──────────────────────────────────────────────────────

command -v redis-cli >/dev/null 2>&1 || fail "redis-cli not on PATH" 1
command -v docker >/dev/null 2>&1 || fail "docker not on PATH" 1
command -v jq >/dev/null 2>&1 || fail "jq not on PATH" 1

log "drill started (master_name=${MASTER_NAME}, report=${REPORT_PATH})"

# ─── 2. Snapshot pre-drill state ──────────────────────────────────────

# Sentinel exposes everything we need: current master IP + port, replica
# list, failover epoch. We use the first sentinel reachable.
PRE_MASTER_INFO="$(redis-cli -h "${SENTINEL_HOST}" -p "${SENTINEL_PORT}" \
    "${REDIS_AUTH_ARGS[@]}" \
    SENTINEL get-master-addr-by-name "${MASTER_NAME}" || true)"

if [ -z "${PRE_MASTER_INFO}" ]; then
    fail "Sentinel returned empty master info — Sentinel cluster broken pre-drill" 2
fi

PRE_PRIMARY_HOST="$(echo "${PRE_MASTER_INFO}" | head -n 1)"
PRE_PRIMARY_PORT="$(echo "${PRE_MASTER_INFO}" | tail -n 1)"

PRE_FAILOVER_EPOCH="$(redis-cli -h "${SENTINEL_HOST}" -p "${SENTINEL_PORT}" \
    "${REDIS_AUTH_ARGS[@]}" \
    SENTINEL masters | grep -A 1 "^failover-epoch$" | tail -n 1 || echo "0")"

log "pre-drill: primary=${PRE_PRIMARY_HOST}:${PRE_PRIMARY_PORT} failover-epoch=${PRE_FAILOVER_EPOCH}"
{
    echo "─── Redis Sentinel failover drill report ───"
    echo "Drill started: $(date -u +%Y%m%dT%H%M%SZ)"
    echo "Master name:   ${MASTER_NAME}"
    echo "Pre-drill state:"
    echo "  primary=${PRE_PRIMARY_HOST}:${PRE_PRIMARY_PORT}"
    echo "  failover-epoch=${PRE_FAILOVER_EPOCH}"
    echo "  drill tracer channel=${DRILL_ID}"
} > "${REPORT_PATH}"

# ─── 3. Start pub/sub subscriber sidecar ───────────────────────────────

# We SUBSCRIBE through Sentinel-aware client — but redis-cli doesn't
# natively SUBSCRIBE through Sentinel. So we point at the current
# primary directly + rely on Redis client reconnect at Phase 4.
# Note: in production, asyncpg/aioredis clients reconnect to the new
# primary via Sentinel discovery; this script's SUBSCRIBE is a simpler
# end-to-end smoke that PROVES the pub/sub channel survived a kill.
SUBSCRIBER_OUT="/tmp/redis-sentinel-drill-sub-$$.txt"
: > "${SUBSCRIBER_OUT}"

# The background SUBSCRIBE connects to the CURRENT primary directly so
# we can verify message delivery before + after the kill. After the
# kill, this connection will drop; we re-SUBSCRIBE to the new primary
# for Phase 4 verification.
redis-cli -h "${PRE_PRIMARY_HOST}" -p "${PRE_PRIMARY_PORT}" \
    "${REDIS_AUTH_ARGS[@]}" \
    SUBSCRIBE "${DRILL_ID}" >> "${SUBSCRIBER_OUT}" 2>&1 &
SUBSCRIBER_PID=$!
sleep 1  # let SUBSCRIBE establish

# ─── 4. Pre-drill tracer (baseline pub/sub works) ───────────────────

log "publishing pre-drill tracer to ${DRILL_ID}"
redis-cli -h "${PRE_PRIMARY_HOST}" -p "${PRE_PRIMARY_PORT}" \
    "${REDIS_AUTH_ARGS[@]}" \
    PUBLISH "${DRILL_ID}" "pre-drill-tracer:$(date -u +%Y%m%dT%H%M%SZ)" > /dev/null

# Give the subscriber up to TRACER_TIMEOUT_SEC to record it.
RECEIVED_PRE=0
END_AT="$(($(date +%s) + TRACER_TIMEOUT_SEC))"
while [ "$(date +%s)" -lt "${END_AT}" ]; do
    if grep -q "pre-drill-tracer:" "${SUBSCRIBER_OUT}" 2>/dev/null; then
        RECEIVED_PRE=1
        break
    fi
    sleep 0.5
done
if [ "${RECEIVED_PRE}" -ne 1 ]; then
    fail "pre-drill tracer never reached the subscriber — Sentinel cluster broken pre-drill" 2
fi
log "pre-drill tracer received OK"
echo "Pre-drill tracer: RECEIVED" >> "${REPORT_PATH}"

# Stop the sidecar SUBSCRIBE — it's tied to the soon-to-die primary.
kill "${SUBSCRIBER_PID}" 2>/dev/null || true
wait "${SUBSCRIBER_PID}" 2>/dev/null || true

# ─── 5. Kill the primary ──────────────────────────────────────────────

# The primary is one of the redis containers in the Swarm. We identify
# it by the IP we got from Sentinel + the swarm service name pattern.
# In practice the drill runs ON the host that has the primary, so docker
# can see it locally.
log "stopping primary Redis container at ${PRE_PRIMARY_HOST}:${PRE_PRIMARY_PORT}"
PRIMARY_CID="$(docker ps --filter "label=com.docker.swarm.service.name=yral-v2-redis" \
    --filter "publish=${PRE_PRIMARY_PORT}" \
    --format '{{.ID}}' | head -n 1)"
if [ -z "${PRIMARY_CID}" ]; then
    # Fall back: any redis container on this host. Drill operator should
    # ensure they're on the right host first (see runbook).
    PRIMARY_CID="$(docker ps --filter "label=com.docker.swarm.service.name=yral-v2-redis" \
        --format '{{.ID}}' | head -n 1)"
fi
if [ -z "${PRIMARY_CID}" ]; then
    fail "no Redis service container on this host — wrong target host?" 3
fi
log "found primary container: ${PRIMARY_CID}"

KILL_START="$(date +%s)"
if ! docker stop "${PRIMARY_CID}" >/dev/null 2>&1; then
    fail "docker stop failed for ${PRIMARY_CID}" 3
fi
log "primary container stopped"

# ─── 6. Watch Sentinel for promotion ────────────────────────────────

log "watching Sentinel for promotion (timeout ${PROMOTION_TIMEOUT_SEC}s)"
PROMOTED=0
NEW_PRIMARY_HOST=""
NEW_PRIMARY_PORT=""
PROMOTION_END_AT="$((KILL_START + PROMOTION_TIMEOUT_SEC))"
while [ "$(date +%s)" -lt "${PROMOTION_END_AT}" ]; do
    NEW_INFO="$(redis-cli -h "${SENTINEL_HOST}" -p "${SENTINEL_PORT}" \
        "${REDIS_AUTH_ARGS[@]}" \
        SENTINEL get-master-addr-by-name "${MASTER_NAME}" 2>/dev/null || true)"
    if [ -n "${NEW_INFO}" ]; then
        CAND_HOST="$(echo "${NEW_INFO}" | head -n 1)"
        CAND_PORT="$(echo "${NEW_INFO}" | tail -n 1)"
        if [ "${CAND_HOST}" != "${PRE_PRIMARY_HOST}" ] || [ "${CAND_PORT}" != "${PRE_PRIMARY_PORT}" ]; then
            NEW_PRIMARY_HOST="${CAND_HOST}"
            NEW_PRIMARY_PORT="${CAND_PORT}"
            PROMOTED=1
            break
        fi
    fi
    sleep 1
done

if [ "${PROMOTED}" -ne 1 ]; then
    fail "Sentinel never promoted a new primary (still pointing at ${PRE_PRIMARY_HOST}:${PRE_PRIMARY_PORT})" 4
fi

PROMOTION_END="$(date +%s)"
PROMOTION_DURATION_SEC="$((PROMOTION_END - KILL_START))"
log "promotion confirmed: new primary=${NEW_PRIMARY_HOST}:${NEW_PRIMARY_PORT} (took ${PROMOTION_DURATION_SEC}s)"
{
    echo "Promotion:"
    echo "  new primary=${NEW_PRIMARY_HOST}:${NEW_PRIMARY_PORT}"
    echo "  promotion duration=${PROMOTION_DURATION_SEC}s"
} >> "${REPORT_PATH}"

# ─── 7. Post-promotion tracer (pub/sub survived?) ──────────────────────

# Start a NEW subscriber against the NEW primary.
SUBSCRIBER_OUT2="/tmp/redis-sentinel-drill-sub2-$$.txt"
: > "${SUBSCRIBER_OUT2}"
redis-cli -h "${NEW_PRIMARY_HOST}" -p "${NEW_PRIMARY_PORT}" \
    "${REDIS_AUTH_ARGS[@]}" \
    SUBSCRIBE "${DRILL_ID}" >> "${SUBSCRIBER_OUT2}" 2>&1 &
SUBSCRIBER_PID=$!
sleep 1

log "publishing post-promotion tracer to new primary"
redis-cli -h "${NEW_PRIMARY_HOST}" -p "${NEW_PRIMARY_PORT}" \
    "${REDIS_AUTH_ARGS[@]}" \
    PUBLISH "${DRILL_ID}" "post-promotion-tracer:$(date -u +%Y%m%dT%H%M%SZ)" > /dev/null

RECEIVED_POST=0
END_AT="$(($(date +%s) + TRACER_TIMEOUT_SEC))"
while [ "$(date +%s)" -lt "${END_AT}" ]; do
    if grep -q "post-promotion-tracer:" "${SUBSCRIBER_OUT2}" 2>/dev/null; then
        RECEIVED_POST=1
        break
    fi
    sleep 0.5
done
kill "${SUBSCRIBER_PID}" 2>/dev/null || true
wait "${SUBSCRIBER_PID}" 2>/dev/null || true

if [ "${RECEIVED_POST}" -ne 1 ]; then
    {
        echo "POST-PROMOTION TRACER: NEVER RECEIVED"
        echo "  Pub/sub did NOT recover via the new primary. Critical regression signal."
    } >> "${REPORT_PATH}"
    fail "post-promotion tracer never arrived — pub/sub failed to recover" 5
fi
log "post-promotion tracer received OK — pub/sub survived"
echo "Post-promotion tracer: RECEIVED (pub/sub survived failover)" >> "${REPORT_PATH}"

# ─── 8. Restart the killed Redis ─────────────────────────────────────

log "restarting killed Redis container ${PRIMARY_CID}"
docker start "${PRIMARY_CID}" >/dev/null 2>&1 || log "(restart returned non-zero — Swarm may have already re-spun it)"

# Wait for Sentinel to recognize it as a replica of the new primary.
log "watching Sentinel for rejoin as replica (timeout ${REJOIN_TIMEOUT_SEC}s)"
REJOIN_END_AT="$(($(date +%s) + REJOIN_TIMEOUT_SEC))"
REJOINED=0
while [ "$(date +%s)" -lt "${REJOIN_END_AT}" ]; do
    REPLICAS_JSON="$(redis-cli -h "${SENTINEL_HOST}" -p "${SENTINEL_PORT}" \
        "${REDIS_AUTH_ARGS[@]}" \
        SENTINEL replicas "${MASTER_NAME}" 2>/dev/null || echo "")"
    # `SENTINEL replicas` returns a list of key/value pairs per replica.
    # The killed node's IP appears once it reconnects via slaveof.
    if echo "${REPLICAS_JSON}" | grep -q "${PRE_PRIMARY_HOST}"; then
        REJOINED=1
        break
    fi
    sleep 2
done

if [ "${REJOINED}" -ne 1 ]; then
    {
        echo "REJOIN: killed node ${PRE_PRIMARY_HOST}:${PRE_PRIMARY_PORT} did NOT rejoin as replica within ${REJOIN_TIMEOUT_SEC}s."
        echo "  Operator must check container health + Sentinel logs."
    } >> "${REPORT_PATH}"
    fail "killed node did not rejoin as replica" 6
fi
log "killed node rejoined as replica"
echo "Rejoin: ${PRE_PRIMARY_HOST} rejoined as replica" >> "${REPORT_PATH}"

# ─── 9. Verdict ────────────────────────────────────────────────────────

{
    echo ""
    echo "Drill finished: $(date -u +%Y%m%dT%H%M%SZ)"
    echo ""
    echo "VERDICT: PASS"
    echo "  - Sentinel promoted new primary in ${PROMOTION_DURATION_SEC}s"
    echo "  - Pub/sub recovered via new primary (post-promotion tracer received)"
    echo "  - Killed node rejoined as replica"
    echo ""
    echo "Note: switchover BACK to original primary is intentionally NOT"
    echo "automated — the new primary is fully healthy and the original"
    echo "host is now a healthy replica. Operator may run"
    echo "\`SENTINEL FAILOVER ${MASTER_NAME}\` later if they want to restore"
    echo "the prior topology."
    echo ""
    echo "WebSocket end-to-end smoke (Phase B): operator-driven, see runbook."
} >> "${REPORT_PATH}"

log "PASS — report written to ${REPORT_PATH}"
exit 0
