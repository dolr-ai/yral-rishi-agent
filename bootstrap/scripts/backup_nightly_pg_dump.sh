#!/usr/bin/env bash
# Nightly pg_dump for yral_agent_db on the Patroni cluster.
#
# Why this exists tonight (2026-06-04): WAL-G is NOT wired in the
# current Patroni stack (`USE_WALG_BACKUP=false`, `archive_command=
# /bin/true`). Until WAL-G lands, this nightly dump is the only
# refreshed backup. See memory project_walg_disabled_in_production.md
# for the WAL-G-wiring path.
#
# DESIGN:
#   - Streams pg_dump straight from a patroni-rishi-N container's
#     local Postgres (so we don't make a network round-trip)
#   - Always hits the LEADER (target_session_attrs=read-write via the
#     bundled DATABASE_URL) so we never accidentally dump a lagging
#     replica
#   - Uses pg_dump's custom format (-Fc) so pg_restore can pull
#     individual tables later
#   - Rotates to keep 7 dumps (~1 week); older dumps are removed
#   - Lives under /home/rishi-deploy/yral-backups/nightly/ so
#     manual Rule-9 dumps in ../yral-backups/ aren't touched
#
# CRON: install on rishi-deploy host (the swarm manager) as
#   0 3 * * * /home/rishi-deploy/yral-backups/bin/backup_nightly_pg_dump.sh \
#     >> /home/rishi-deploy/yral-backups/nightly.log 2>&1
# 03:00 UTC = 08:30 IST — Rishi-friendly morning artifact.
#
# EXIT CODES:
#   0  — dump produced + verified non-zero size + rotation done
#   1  — no patroni container found
#   2  — pg_dump failed
#   3  — produced dump is 0 bytes (catastrophic pg_dump silent failure)
#
# This script is INTENTIONALLY non-destructive of existing backups:
#   - Only rotates files under nightly/ (its own directory)
#   - NEVER touches ../yral-backups/*.dump (Rishi's Rule-9 manual dumps)

set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/home/rishi-deploy/yral-backups/nightly}"
RETENTION_COUNT="${RETENTION_COUNT:-7}"
PATRONI_CONTAINER_PREFIX="${PATRONI_CONTAINER_PREFIX:-patroni-rishi}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="${BACKUP_ROOT}/yral_agent_db_${TIMESTAMP}.dump"

mkdir -p "${BACKUP_ROOT}"

# Pick the first patroni container we find. pg_dump runs INSIDE the
# patroni container against its own local socket as the `postgres`
# superuser — this works whether the local node is the leader or a
# replica (pg_dump is read-only). Avoids needing the yral-rishi-agent
# container to be on this host (it runs on rishi-5/6 not rishi-4).
DUMP_DB="${DUMP_DB:-yral_agent_db}"
PATRONI_CONTAINER="$(docker ps --format '{{.Names}}' | grep -E "${PATRONI_CONTAINER_PREFIX}" | head -1)"
if [[ -z "${PATRONI_CONTAINER}" ]]; then
    echo "FAIL: no container matching ${PATRONI_CONTAINER_PREFIX}*" >&2
    exit 1
fi

echo "[$(date -u +%FT%TZ)] backup_nightly_pg_dump: starting → ${OUT_FILE}"
echo "[$(date -u +%FT%TZ)] backup_nightly_pg_dump: container=${PATRONI_CONTAINER} db=${DUMP_DB}"

# Stream pg_dump from the local socket. -Fc = custom format (pg_restore
# can pull individual tables later).
if ! docker exec "${PATRONI_CONTAINER}" pg_dump \
        --username=postgres \
        --dbname="${DUMP_DB}" \
        --format=custom \
        --no-owner \
        --no-acl \
        --compress=6 \
        > "${OUT_FILE}.partial"; then
    echo "FAIL: pg_dump exited non-zero" >&2
    rm -f "${OUT_FILE}.partial"
    exit 2
fi

# Sanity check: pg_dump can silently produce 0-byte dumps if connect
# fails mid-stream. Fail loud here so the cron log shows the issue.
if [[ ! -s "${OUT_FILE}.partial" ]]; then
    echo "FAIL: pg_dump produced 0-byte file" >&2
    rm -f "${OUT_FILE}.partial"
    exit 3
fi

mv "${OUT_FILE}.partial" "${OUT_FILE}"
SIZE_BYTES="$(stat -c%s "${OUT_FILE}")"
echo "[$(date -u +%FT%TZ)] backup_nightly_pg_dump: produced ${OUT_FILE} (${SIZE_BYTES} bytes)"

# Rotate: keep newest N dumps under nightly/, prune the rest.
ls -1t "${BACKUP_ROOT}"/yral_agent_db_*.dump 2>/dev/null \
    | tail -n +$((RETENTION_COUNT + 1)) \
    | xargs -r rm -v

echo "[$(date -u +%FT%TZ)] backup_nightly_pg_dump: rotation complete (keeping ${RETENTION_COUNT})"
exit 0
