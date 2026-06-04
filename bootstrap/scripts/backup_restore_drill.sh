#!/usr/bin/env bash
# Weekly backup-restore drill — I10 deliverable (per cutover prereq list).
#
# Picks the most recent dump under /home/rishi-deploy/yral-backups/nightly/
# (produced by backup_nightly_pg_dump.sh), spins up a throwaway Postgres
# sidecar container on the SAME host, restores into it, runs sanity
# queries, and tears the sidecar down. Exits non-zero on any failure
# step so cron can email the operator.
#
# WHY A SIDECAR not the production cluster:
#   - Restore into production would overwrite live data — never.
#   - Restore into a sidecar guarantees the dump is bit-readable +
#     pg_restore can replay it + the schema isn't subtly broken.
#   - Sanity queries against the sidecar prove the data is intact
#     (table row counts non-zero, sample message-text decodable).
#
# WHAT'S NOT YET TESTED (until WAL-G lands):
#   - PITR (no WAL stream to replay against the base backup)
#   - Cross-region restore (no offsite — I11 follow-up)
#
# CRON: install on rishi-deploy host as:
#   30 4 * * 0 /home/rishi-deploy/yral-backups/bin/backup_restore_drill.sh \
#     >> /home/rishi-deploy/yral-backups/drill.log 2>&1
# Sunday 04:30 UTC = 10:00 IST — Rishi sees the result with morning
# coffee. Picked AFTER the nightly dump at 03:00 so the freshest
# dump is the one being restored.
#
# EXIT CODES:
#   0 — restore + all sanity queries pass
#   1 — no dump file to restore
#   2 — sidecar spin-up failed
#   3 — pg_restore failed
#   4 — sanity query failed (data missing or unreadable)
#   5 — sidecar teardown failed (drill itself succeeded; ops should look)
#
# Designed to be re-runnable manually — just invoke without args.

set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/home/rishi-deploy/yral-backups/nightly}"
SIDECAR_NAME="${SIDECAR_NAME:-yral-restore-drill-sidecar}"
SIDECAR_IMAGE="${SIDECAR_IMAGE:-pgvector/pgvector:pg15}"
SIDECAR_PASSWORD="${SIDECAR_PASSWORD:-drill_$(openssl rand -hex 8)}"
SIDECAR_DB="${SIDECAR_DB:-restore_drill}"

TIMESTAMP="$(date -u +%FT%TZ)"
log() { echo "[$(date -u +%FT%TZ)] backup_restore_drill: $*"; }

# ─── 1. find the dump ──────────────────────────────────────────────
LATEST_DUMP="$(ls -1t "${BACKUP_ROOT}"/yral_agent_db_*.dump 2>/dev/null | head -1 || true)"
if [[ -z "${LATEST_DUMP}" ]]; then
    log "FAIL: no dump found in ${BACKUP_ROOT} (has backup_nightly_pg_dump.sh run?)"
    exit 1
fi
DUMP_AGE_SECONDS=$(( $(date +%s) - $(stat -c%Y "${LATEST_DUMP}") ))
DUMP_AGE_HOURS=$(( DUMP_AGE_SECONDS / 3600 ))
DUMP_SIZE_MB=$(( $(stat -c%s "${LATEST_DUMP}") / 1024 / 1024 ))
log "restoring ${LATEST_DUMP} (${DUMP_SIZE_MB} MB, ${DUMP_AGE_HOURS}h old)"

if (( DUMP_AGE_HOURS > 30 )); then
    log "WARN: dump is >30h old — nightly cron may have skipped a run"
fi

# ─── 2. tear down any stale sidecar from a prior failed run ────────
docker rm -f "${SIDECAR_NAME}" 2>/dev/null || true

# ─── 3. spin up sidecar (postgres:15-alpine, no port published) ────
if ! docker run -d --name "${SIDECAR_NAME}" \
        -e POSTGRES_PASSWORD="${SIDECAR_PASSWORD}" \
        -e POSTGRES_DB="${SIDECAR_DB}" \
        "${SIDECAR_IMAGE}" >/dev/null; then
    log "FAIL: sidecar spin-up (docker run) errored"
    exit 2
fi

# Wait for postgres-inside-sidecar to accept connections, then wait
# a bit longer to ensure we're past the docker-entrypoint initdb
# bring-up/shutdown/restart cycle. Without the stable-window check
# the loop would break on the brief ready-state during initdb, then
# the very next check fails because the entrypoint restarted PG.
ready_streak=0
for _ in $(seq 1 90); do
    if docker exec "${SIDECAR_NAME}" pg_isready -U postgres >/dev/null 2>&1; then
        ready_streak=$((ready_streak + 1))
        if (( ready_streak >= 5 )); then
            break
        fi
    else
        ready_streak=0
    fi
    sleep 1
done
if ! docker exec "${SIDECAR_NAME}" pg_isready -U postgres >/dev/null 2>&1; then
    log "FAIL: sidecar postgres never became stable-ready (90s timeout)"
    docker logs "${SIDECAR_NAME}" 2>&1 | tail -20 >&2
    docker rm -f "${SIDECAR_NAME}" 2>/dev/null || true
    exit 2
fi

# ─── 4. copy dump into sidecar + restore ───────────────────────────
docker cp "${LATEST_DUMP}" "${SIDECAR_NAME}:/tmp/restore.dump"

# We do NOT use --exit-on-error: the production Patroni image (Spilo)
# ships a few extensions (pg_stat_kcache, etc.) that the plain
# postgres:15-alpine sidecar lacks. CREATE EXTENSION for those will
# fail, but the DATA tables restore cleanly — and that's what we
# care about for a backup-restore drill. The sanity-query block
# below is the actual "did the restore work" gate.
t0=$(date +%s)
RESTORE_LOG="/tmp/restore_drill_$$.log"
docker exec "${SIDECAR_NAME}" pg_restore \
        --username=postgres \
        --dbname="${SIDECAR_DB}" \
        --no-owner --no-acl \
        /tmp/restore.dump 2>&1 | tee "${RESTORE_LOG}" || true
t1=$(date +%s)

# Classify pg_restore errors. The Spilo (production) image ships
# extensions (pg_stat_kcache, set_user, etc.) the pgvector sidecar
# lacks — those errors AND their cascading "does not exist" COMMENTs
# are expected. FK-violation errors usually mean orphan-row data
# anomalies in production — log them as findings but don't fail the
# drill on them; the sanity-query block below is the actual
# data-integrity gate.
TOTAL_ERRORS=$(grep -c "^pg_restore: error" "${RESTORE_LOG}" 2>/dev/null || echo 0)
EXT_NOT_AVAIL=$(grep -c "extension .* is not available" "${RESTORE_LOG}" 2>/dev/null || echo 0)
EXT_NOT_EXIST=$(grep -c "extension .* does not exist" "${RESTORE_LOG}" 2>/dev/null || echo 0)
FK_VIOLATIONS=$(grep -c "violates foreign key constraint" "${RESTORE_LOG}" 2>/dev/null || echo 0)
ACCOUNTED=$(( EXT_NOT_AVAIL + EXT_NOT_EXIST + FK_VIOLATIONS ))
UNCLASSIFIED=$(( TOTAL_ERRORS - ACCOUNTED ))

log "pg_restore done in $((t1 - t0))s — total_errors=${TOTAL_ERRORS} ext_missing=${EXT_NOT_AVAIL} ext_cascade=${EXT_NOT_EXIST} fk_violations=${FK_VIOLATIONS} unclassified=${UNCLASSIFIED}"
if (( FK_VIOLATIONS > 0 )); then
    log "FINDING: ${FK_VIOLATIONS} FK violations in dump (orphan rows in production) — see ${RESTORE_LOG}"
fi
if (( UNCLASSIFIED > 0 )); then
    log "FAIL: ${UNCLASSIFIED} unclassified pg_restore errors (see ${RESTORE_LOG})"
    docker rm -f "${SIDECAR_NAME}" 2>/dev/null || true
    exit 3
fi

# ─── 5. sanity queries ─────────────────────────────────────────────
# These three together prove: schema present + data present +
# largest table queryable. Add more as cutover comfort requires.
SANITY_SQL=$(cat <<'SQL'
\set ON_ERROR_STOP on
\echo SANITY: counting key tables
SELECT
    (SELECT count(*) FROM ai_influencers)                            AS ai_influencers,
    (SELECT count(*) FROM conversations)                             AS conversations,
    (SELECT count(*) FROM messages)                                  AS messages,
    (SELECT count(*) FROM user_skill_state)                          AS user_skill_state,
    (SELECT count(*) FROM ai_influencers WHERE skill_slug IS NOT NULL) AS skilled_influencers;

\echo SANITY: most recent message timestamp
SELECT MAX(created_at) AS latest_message_at FROM messages;

\echo SANITY: sample message content readable
SELECT id, role, LEFT(content, 50) AS content_preview
FROM messages
WHERE content IS NOT NULL
ORDER BY created_at DESC
LIMIT 3;
SQL
)

if ! echo "${SANITY_SQL}" | docker exec -i "${SIDECAR_NAME}" \
        psql -U postgres -d "${SIDECAR_DB}" -v ON_ERROR_STOP=1; then
    log "FAIL: sanity queries errored"
    docker rm -f "${SIDECAR_NAME}" 2>/dev/null || true
    exit 4
fi

# Confirm at least the core tables have rows. A 0-row messages count
# usually means the dump didn't include data (schema-only dump).
COUNTS=$(docker exec -i "${SIDECAR_NAME}" psql -U postgres -d "${SIDECAR_DB}" -At <<'SQL'
SELECT
    (SELECT count(*) FROM ai_influencers) || ',' ||
    (SELECT count(*) FROM conversations) || ',' ||
    (SELECT count(*) FROM messages);
SQL
)
IFS=',' read -r INF_N CONV_N MSG_N <<< "${COUNTS}"
log "row counts → ai_influencers=${INF_N} conversations=${CONV_N} messages=${MSG_N}"
if [[ "${MSG_N}" -lt 100 ]]; then
    log "FAIL: messages table has only ${MSG_N} rows (suspicious)"
    docker rm -f "${SIDECAR_NAME}" 2>/dev/null || true
    exit 4
fi

# ─── 6. teardown ───────────────────────────────────────────────────
if ! docker rm -f "${SIDECAR_NAME}" >/dev/null; then
    log "WARN: sidecar teardown failed — manual cleanup needed (docker rm -f ${SIDECAR_NAME})"
    exit 5
fi

log "DRILL PASSED — ${LATEST_DUMP} restored cleanly to sidecar, $((t1 - t0))s pg_restore, ${MSG_N} messages verified"
exit 0
