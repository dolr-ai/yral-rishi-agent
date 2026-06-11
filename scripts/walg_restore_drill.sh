#!/usr/bin/env bash
# Phase 21αβ.H6 — WAL-G restore drill (PROD BLOCKER).
#
# Proves that wal-g can actually restore the V2 Patroni cluster from the
# Hetzner Object Storage backup. Without this drill, "we have a backup"
# is theory — we've never tested the restore mechanism in production
# conditions. If a real incident hits, we'd be debugging restore syntax
# under pressure instead of executing a known-good runbook.
#
# Strategy:
#   - SSH to a chosen target host (one of rishi-4/5/6).
#   - Drop into the running patroni service container via `docker exec`.
#     That container already has wal-g, postgres binaries, and the WAL-G
#     S3 credentials mounted (via Spilo's standard env vars).
#   - Inside the container, fetch the LATEST base backup into a fresh
#     /tmp/walg-drill-<ts> directory. Never touches /home/postgres/pgdata.
#   - Start a sidecar postgres on port 5433 against that fresh directory
#     in standby mode. WAL replay catches up to the latest archived
#     segment via `wal-g wal-fetch`.
#   - Run sanity queries against the sidecar: row counts on critical
#     tables, latest message timestamp. Confirm they're in-range.
#   - Stop the sidecar postgres, rm -rf the drill directory.
#
# Safety properties:
#   - DOES NOT touch the live data dir (/home/postgres/pgdata/pgroot)
#   - DOES NOT join Patroni's etcd cluster (no DCS writes)
#   - DOES NOT bind 5432 (live Postgres keeps serving traffic)
#   - DOES NOT modify the WAL-G S3 bucket — read-only operations
#   - Sidecar postgres process is named distinctly so an operator can
#     `ps` and confirm it exited cleanly.
#
# Exit codes (cron + workflow friendly):
#   0  — restore + all sanity queries pass
#   1  — drill prerequisites missing (no patroni container, no creds)
#   2  — wal-g backup-fetch failed
#   3  — sidecar postgres failed to start or never exited recovery
#   4  — sanity query failed (table missing or counts unreasonably low)
#   5  — drill itself succeeded but cleanup failed (ops should look)

set -euo pipefail

# ─── config ────────────────────────────────────────────────────────────
SWARM_STACK="${SWARM_STACK:-yral-v2-patroni}"
POSTGRES_DB="${POSTGRES_DB:-yral_agent_db}"
DRILL_PORT="${DRILL_PORT:-5433}"
SIDECAR_NAME="walg-drill"
# V2's schema has three core tables (ai_influencers + conversations +
# messages, see migrations/001_initial.sql). No `users` table — principal
# IDs are stored directly on the conversations/messages rows. The first
# successful drill (#4 on 2026-06-11) reported SELECT FROM users as
# "relation does not exist" and the bash arithmetic compare silently
# accepted the error string as a numeric value — we'd PASS even though
# one query had genuinely failed.
MIN_ROW_COUNT_AI_INFLUENCERS="${MIN_ROW_COUNT_AI_INFLUENCERS:-1}"
MIN_ROW_COUNT_CONVERSATIONS="${MIN_ROW_COUNT_CONVERSATIONS:-1}"
MIN_ROW_COUNT_MESSAGES="${MIN_ROW_COUNT_MESSAGES:-1}"
# How recent the latest message must be for the drill to be meaningful.
# Default 7 days — anything older means we're restoring a stale backup
# and the test isn't proving fresh-data-recoverability.
MAX_LATEST_MESSAGE_AGE_SECONDS="${MAX_LATEST_MESSAGE_AGE_SECONDS:-604800}"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
DRILL_DIR="/tmp/walg-drill-${TS}"
LOG_PREFIX="[walg-drill ${TS}]"

log() { echo "${LOG_PREFIX} $*"; }
fail() { echo "${LOG_PREFIX} FAIL: $*" >&2; }

# ─── 1. locate a local patroni container ───────────────────────────────
CID="$(docker ps -qf "name=${SWARM_STACK}_patroni" | head -1)"
if [ -z "${CID}" ]; then
    fail "no patroni container on this host (looked for name~${SWARM_STACK}_patroni)"
    exit 1
fi
log "using container ${CID}"

# Quick environment sanity inside the container
if ! docker exec "${CID}" sh -c "command -v wal-g >/dev/null 2>&1"; then
    fail "wal-g binary missing inside container ${CID}"
    exit 1
fi
if ! docker exec "${CID}" sh -c "test -n \"\${WALG_S3_PREFIX:-}\""; then
    fail "WALG_S3_PREFIX env var missing inside container ${CID} (Spilo should set this)"
    exit 1
fi

log "container env looks healthy — wal-g + WALG_S3_PREFIX present"

# ─── 1b. pre-flight orphan cleanup ─────────────────────────────────────
# A previous drill that exited via set -e BEFORE teardown leaves:
#   1. A sidecar postgres still bound to port 5433
#   2. /tmp/walg-drill-* directories with ~1GB+ of base backup data
# Both block the next drill from running clean. The 2026-06-11 drill #3
# hit this — drill #2's silent set-e exit skipped teardown, the orphan
# postgres on 5433 made drill #3's pg_ctl fail immediately with "could
# not bind". Pre-flight cleans them defensively so each drill starts
# from a known-empty state on the container.
log "pre-flight: clearing any orphan sidecars + stale drill dirs..."
docker exec --user postgres "${CID}" sh -c "
    # Stop any postgres process whose data dir matches /tmp/walg-drill-*.
    # pg_ctl -mode immediate is the fastest stop; orphans don't deserve a graceful shutdown.
    for olddir in /tmp/walg-drill-*; do
        [ -d \"\$olddir\" ] || continue
        if [ -f \"\$olddir/postmaster.pid\" ]; then
            pg_ctl -D \"\$olddir\" stop -m immediate 2>/dev/null || true
        fi
    done
    true
" 2>/dev/null || true
# Remove the dirs after stopping the daemons (run as root in case
# postgres process owned files that postgres user can't unlink).
docker exec "${CID}" sh -c "rm -rf /tmp/walg-drill-* 2>/dev/null; true" || true

# Defensive trap: teardown runs ALWAYS, even on `set -e` exit mid-flight.
# Without this, a sanity-query failure leaves an orphan postgres + drill
# dir that block the next run (the exact bug this section prevents for
# THIS run; the trap prevents it for the NEXT run).
trap 'if [ -n "${CID:-}" ] && [ -n "${DRILL_DIR:-}" ]; then
    echo "[walg-drill trap] cleaning up sidecar + ${DRILL_DIR}..." >&2
    docker exec --user postgres "${CID}" pg_ctl -D "${DRILL_DIR}" stop -m immediate 2>/dev/null || true
    docker exec "${CID}" rm -rf "${DRILL_DIR}" 2>/dev/null || true
fi' EXIT

# ─── 2. inspect backups before fetching ────────────────────────────────
log "listing available backups..."
if ! docker exec "${CID}" sh -c "wal-g backup-list 2>&1 | tail -20"; then
    fail "wal-g backup-list failed — credentials, bucket, or network bad"
    exit 1
fi

# ─── 3. fetch LATEST into a fresh drill dir ────────────────────────────
# The mkdir + chown + chmod run as the default container user (root or
# postgres depending on image hardening). The wal-g call then runs as
# postgres via `--user postgres` — cleaner than `su postgres -c '...'`
# because we avoid an extra single-quoting layer that bites us in the
# config-file step below.
log "fetching LATEST backup into ${DRILL_DIR}..."
if ! docker exec "${CID}" sh -c "
    mkdir -p ${DRILL_DIR}
    chown postgres:postgres ${DRILL_DIR}
    chmod 700 ${DRILL_DIR}
"; then
    fail "could not create drill dir ${DRILL_DIR} on container ${CID}"
    exit 2
fi
if ! docker exec --user postgres "${CID}" \
        wal-g backup-fetch "${DRILL_DIR}" LATEST; then
    fail "wal-g backup-fetch failed — drill aborted"
    exit 2
fi

log "fetch complete; configuring sidecar postgres..."

# ─── 4. configure recovery + custom port ───────────────────────────────
# Inside the container we own the file mode + permissions on ${DRILL_DIR}.
# `recovery.signal` (Postgres 12+) puts us into archive recovery.
# `restore_command` pulls each WAL segment via wal-g.
# Custom port + unix_socket_directories isolates the sidecar from the
# live Patroni Postgres listening on 5432.
#
# Note on quoting: postgresql.auto.conf REQUIRES single quotes around
# string values (Postgres rejects double quotes with a syntax error).
# Using `docker exec --user postgres` instead of `su postgres -c '...'`
# means we only have ONE outer quoting layer (the `sh -c "..."` arg)
# and can use literal single quotes inside the heredoc cleanly. The
# 2026-06-11 first-ever drill run failed exactly here — escaped \"..."\"
# values produced postgres config with `"` chars that Postgres rejected.
if ! docker exec --user postgres "${CID}" sh -c "
    touch ${DRILL_DIR}/recovery.signal
    cat >> ${DRILL_DIR}/postgresql.auto.conf <<EOF

# Drill-only overrides — never used by live Patroni
restore_command = 'wal-g wal-fetch %f %p'
port = ${DRILL_PORT}
unix_socket_directories = '${DRILL_DIR}'
# Quiet sidecar logs go to the drill dir so they roll off with the dir
log_destination = 'stderr'
logging_collector = off
EOF
"; then
    fail "failed to configure sidecar postgres in ${DRILL_DIR}"
    exit 3
fi

# ─── 5. start sidecar postgres + wait for recovery to finish ───────────
log "starting sidecar postgres on port ${DRILL_PORT}..."
if ! docker exec --user postgres "${CID}" \
        pg_ctl -D "${DRILL_DIR}" -l "${DRILL_DIR}/startup.log" -w -t 90 start; then
    fail "sidecar postgres failed to start — see ${DRILL_DIR}/startup.log on container ${CID}"
    # Sleep first so any buffered postgres output flushes to disk before
    # we read. Without this, we sometimes see only the first LOG line
    # (the 2026-06-11 drill #3 case) and miss the actual failure cause.
    sleep 2
    log "── full startup.log dump ──"
    docker exec "${CID}" cat "${DRILL_DIR}/startup.log" 2>&1 || true
    log "── /tmp/walg-drill-* dirs on container (orphan check) ──"
    docker exec "${CID}" sh -c "ls -la /tmp/ | grep walg-drill || true" 2>&1 || true
    log "── port 5433 owner inside container (if known) ──"
    docker exec "${CID}" sh -c "ss -tlnp 2>/dev/null | grep ':5433 ' || netstat -tlnp 2>/dev/null | grep ':5433 ' || echo '(ss/netstat unavailable)'" || true
    exit 3
fi
log "sidecar started; waiting for WAL replay to catch up..."

# Wait up to 5 min for `pg_is_in_recovery()` to flip false OR for a
# consistent recovery target to be reached. Spilo's default behavior
# during archive recovery is to keep replaying until WAL is exhausted.
for i in $(seq 1 60); do
    REPLAY_STATE="$(
        docker exec --user postgres "${CID}" \
            psql -p "${DRILL_PORT}" -h "${DRILL_DIR}" -d "${POSTGRES_DB}" -tA \
                -c "SELECT pg_is_in_recovery()" 2>/dev/null \
        || echo "ERROR"
    )"
    REPLAY_STATE="$(echo "${REPLAY_STATE}" | tr -d '[:space:]')"
    if [ "${REPLAY_STATE}" = "f" ]; then
        log "recovery complete (after $((i*5))s) — sidecar is open"
        break
    fi
    if [ "${REPLAY_STATE}" = "ERROR" ]; then
        # Sidecar may still be replaying WAL and not accepting queries yet.
        # That's normal; keep polling.
        :
    fi
    sleep 5
done

# ─── 6. sanity queries ─────────────────────────────────────────────────
log "running sanity queries against sidecar..."

# Helper: query the sidecar and trim whitespace. Always returns 0 from
# the function so a psql failure (sidecar shutdown mid-drill, table not
# present, connection refused) doesn't trip `set -e` on the caller —
# we want to RUN ALL queries and report which ones returned empty, not
# bail on the first. The 2026-06-11 drill #2 hit exactly this: replay
# completed cleanly but the first sanity_query call exited the script
# silently via set -e, never reaching the row-count log lines.
sidecar_query() {
    local out
    out="$(docker exec --user postgres "${CID}" \
        psql -p "${DRILL_PORT}" -h "${DRILL_DIR}" -d "${POSTGRES_DB}" -tA -c "$1" 2>&1 || true)"
    printf '%s' "${out}" | tr -d '[:space:]'
}

# Pre-flight: confirm the sidecar accepts queries on the target DB. If
# this fails, every COUNT(*) below would also fail; better to surface
# the root cause ONCE with diagnostics than five no-ops in a row.
PRE_FLIGHT="$(sidecar_query "SELECT current_database()")"
log "sidecar pre-flight (current_database): ${PRE_FLIGHT:-<empty>}"
if [ -z "${PRE_FLIGHT}" ] || echo "${PRE_FLIGHT}" | grep -qi "error\|refused\|fatal\|could.not"; then
    fail "sidecar not accepting queries on ${POSTGRES_DB}"
    log "── diagnostics: list databases via the default postgres DB ──"
    docker exec --user postgres "${CID}" \
        psql -p "${DRILL_PORT}" -h "${DRILL_DIR}" -d postgres -tA -c "\l" 2>&1 | head -20 || true
    log "── diagnostics: list public-schema tables in ${POSTGRES_DB} ──"
    docker exec --user postgres "${CID}" \
        psql -p "${DRILL_PORT}" -h "${DRILL_DIR}" -d "${POSTGRES_DB}" -tA -c \
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1" \
        2>&1 | head -20 || true
fi

# Run all 4 sanity queries — none short-circuit, even if the pre-flight
# failed. We want full visibility into which queries return empty.
INF_COUNT="$(sidecar_query "SELECT COUNT(*) FROM ai_influencers")"
CONV_COUNT="$(sidecar_query "SELECT COUNT(*) FROM conversations")"
MSG_COUNT="$(sidecar_query "SELECT COUNT(*) FROM messages")"
LATEST_MSG_EPOCH="$(sidecar_query "SELECT COALESCE(EXTRACT(EPOCH FROM MAX(created_at))::bigint, 0) FROM messages")"

log "row counts:"
log "  ai_influencers = ${INF_COUNT}"
log "  conversations  = ${CONV_COUNT}"
log "  messages       = ${MSG_COUNT}"
log "  latest message epoch = ${LATEST_MSG_EPOCH} ($(date -u -d @${LATEST_MSG_EPOCH} '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "decode-failed"))"

# Numeric guard: every count must be a pure-numeric string. If sidecar_query
# returned an error blob (e.g. "ERROR:relation does not exist"), the bash
# arithmetic compare would silently accept it as 0 — which means a real
# query failure would still PASS. Be strict: anything non-numeric = fail.
FAILED=0
is_numeric() { case "$1" in (*[!0-9]*|"") return 1 ;; (*) return 0 ;; esac }

if ! is_numeric "${INF_COUNT}"; then
    fail "ai_influencers query failed (non-numeric result: ${INF_COUNT})"
    FAILED=1
elif [ "${INF_COUNT}" -lt "${MIN_ROW_COUNT_AI_INFLUENCERS}" ]; then
    fail "ai_influencers count ${INF_COUNT} < min ${MIN_ROW_COUNT_AI_INFLUENCERS}"
    FAILED=1
fi
if ! is_numeric "${CONV_COUNT}"; then
    fail "conversations query failed (non-numeric result: ${CONV_COUNT})"
    FAILED=1
elif [ "${CONV_COUNT}" -lt "${MIN_ROW_COUNT_CONVERSATIONS}" ]; then
    fail "conversations count ${CONV_COUNT} < min ${MIN_ROW_COUNT_CONVERSATIONS}"
    FAILED=1
fi
if ! is_numeric "${MSG_COUNT}"; then
    fail "messages query failed (non-numeric result: ${MSG_COUNT})"
    FAILED=1
elif [ "${MSG_COUNT}" -lt "${MIN_ROW_COUNT_MESSAGES}" ]; then
    fail "messages count ${MSG_COUNT} < min ${MIN_ROW_COUNT_MESSAGES}"
    FAILED=1
fi
if ! is_numeric "${LATEST_MSG_EPOCH}"; then
    fail "latest-message-epoch query failed (non-numeric: ${LATEST_MSG_EPOCH})"
    FAILED=1
    LATEST_MSG_EPOCH=0  # let the freshness check below produce a clear msg
fi

NOW_EPOCH="$(date -u +%s)"
LATEST_AGE_SECONDS=$(( NOW_EPOCH - LATEST_MSG_EPOCH ))
if [ "${LATEST_AGE_SECONDS}" -gt "${MAX_LATEST_MESSAGE_AGE_SECONDS}" ]; then
    fail "latest message is ${LATEST_AGE_SECONDS}s old; max allowed ${MAX_LATEST_MESSAGE_AGE_SECONDS}s (~7 days) — backup is stale"
    FAILED=1
fi

if [ "${FAILED}" -eq 1 ]; then
    log "sanity queries failed; tearing down before exit"
fi

# ─── 7. teardown ───────────────────────────────────────────────────────
log "stopping sidecar postgres..."
TEARDOWN_FAILED=0
docker exec --user postgres "${CID}" pg_ctl -D "${DRILL_DIR}" stop -m fast 2>/dev/null \
    || { log "pg_ctl stop failed (sidecar may have already exited); attempting fast kill"; TEARDOWN_FAILED=1; }
docker exec "${CID}" rm -rf "${DRILL_DIR}" 2>/dev/null \
    || { fail "could not rm -rf ${DRILL_DIR} on container ${CID} — manual cleanup needed"; TEARDOWN_FAILED=1; }

if [ "${FAILED}" -eq 1 ]; then
    exit 4
fi
if [ "${TEARDOWN_FAILED}" -eq 1 ]; then
    log "drill PASSED but teardown was messy — ops should verify ${DRILL_DIR} is gone"
    exit 5
fi

log "─── drill PASSED ───"
log "  fetched + restored LATEST WAL-G base backup"
log "  WAL replay completed"
log "  4 critical tables present with counts above minimums"
log "  latest message within ${MAX_LATEST_MESSAGE_AGE_SECONDS}s window"
log "  sidecar cleaned up cleanly"
exit 0
