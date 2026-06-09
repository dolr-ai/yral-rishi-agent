#!/bin/bash
# run-migrations.sh — apply pending SQL migrations to the v2 database.
# Adapted from chat-ai's migration runner for the v2 Patroni cluster.
#
# Usage:
#   APP_DIR=/path/to/repo bash scripts/ci/run-migrations.sh
#   APP_DIR=/path/to/repo bash scripts/ci/run-migrations.sh --dry-run

set -euo pipefail

DRY_RUN="false"
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN="true" ;;
    esac
done

if [ -n "${APP_DIR:-}" ]; then
    MIGRATIONS_DIR="${APP_DIR}/migrations"
    if [ -z "${POSTGRES_DB:-}" ] && [ -f "${APP_DIR}/project.config" ]; then
        set -a; source "${APP_DIR}/project.config"; set +a
    fi
else
    MIGRATIONS_DIR="$(cd "$(dirname "$0")/../.." && pwd)/migrations"
fi

if [ ! -d "${MIGRATIONS_DIR}" ]; then
    echo "[migrations] no migrations/ directory found — skipping"
    exit 0
fi

MIGRATION_FILES=$(find "${MIGRATIONS_DIR}" -name '*.sql' ! -name '*.down.sql' -type f | sort)
if [ -z "${MIGRATION_FILES}" ]; then
    echo "[migrations] no .sql files in migrations/ — skipping"
    exit 0
fi

echo "[migrations] checking for pending migrations..."

# v2 uses Patroni on rishi-4/5/6 — find a local Patroni container
SWARM_STACK="${SWARM_STACK:-agent-db}"

find_local_patroni() {
    docker ps -qf "name=${SWARM_STACK}_patroni" 2>/dev/null | head -1
}

# Connect via the local UNIX socket inside the Patroni container — NOT
# TCP. Patroni's pg_hba.conf is:
#     local   all   all                   trust         ← UNIX socket: no password
#     host    all   all   127.0.0.1/32    md5           ← TCP: password required
# Passing `-h localhost` forces TCP → md5 path; the runner used to do
# that, then tried to read /run/secrets/postgres-superuser-password to
# satisfy the password prompt. The file is mounted but its value
# (rendered at stack-up time) doesn't match what Patroni currently
# accepts for the postgres role over TCP. Rather than chase the
# password drift, we use the trust path the platform already provides:
# default `-h` (the socket at /var/run/postgresql/.s.PGSQL.5432) +
# `-U postgres`. No PGPASSWORD env needed; no /run/secrets read needed.
#
# This is the same path you get with `docker exec $CID psql -U postgres
# -d yral_agent_db` — already known to work (verified against rishi-4
# 2026-06-09 during the migration-runner auth audit).

wait_for_db() {
    echo "[migrations] waiting for database (up to 120s)..."
    for i in $(seq 1 40); do
        LOCAL_C=$(find_local_patroni)
        if [ -n "$LOCAL_C" ]; then
            if docker exec "$LOCAL_C" psql -U postgres -d "${POSTGRES_DB}" -tAc "SELECT 1;" >/dev/null 2>&1; then
                echo "[migrations] database reachable after $((i*3))s (via UNIX socket trust auth)"
                return 0
            fi
        fi
        sleep 3
    done
    echo "[migrations] FATAL: database not reachable after 120s"
    return 1
}

run_sql() {
    local sql="$1"
    LOCAL_C=$(find_local_patroni)
    [ -z "$LOCAL_C" ] && { echo "[migrations] FATAL: no local Patroni container"; exit 1; }
    docker exec -i "$LOCAL_C" \
        psql -U postgres -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 <<< "$sql"
}

wait_for_db || exit 1

run_sql "CREATE TABLE IF NOT EXISTS schema_migrations (
    filename VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);"

LOCAL_C=$(find_local_patroni)
APPLIED=$(docker exec -i "$LOCAL_C" psql -U postgres -d "${POSTGRES_DB}" -tA \
    -c "SELECT filename FROM schema_migrations ORDER BY filename;" 2>/dev/null || true)

# Defensive check: if schema_migrations is empty but the schema clearly
# already has non-trivial tables (e.g. ai_influencers from migration
# 001_initial.sql), refuse rather than replay 001+ on a populated DB.
#
# This is exactly the trap the 2026-06-09 #314 deploy fell into:
# schema_migrations had never been populated on prod (migrations had
# been applied manually before this runner existed), so the runner saw
# "nothing applied, everything pending" and started trying to replay
# 001_initial.sql against a database that already had `conversations`,
# `messages`, `ai_influencers`, etc. The only thing that stopped it
# from creating real damage was the pg_dump → S3 step failing first.
#
# Skip via FORCE_RUN_ON_EMPTY_SCHEMA_MIGRATIONS=true for the explicit
# bootstrap path (a fresh cluster, or after the one-shot backfill
# workflow has seeded schema_migrations with already-applied filenames).
if [ -z "$APPLIED" ]; then
    SCHEMA_HAS_TABLES=$(docker exec -i "$LOCAL_C" psql -U postgres -d "${POSTGRES_DB}" -tA \
        -c "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='ai_influencers');" 2>/dev/null || echo "f")
    SCHEMA_HAS_TABLES=$(echo "$SCHEMA_HAS_TABLES" | tr -d '[:space:]')
    if [ "$SCHEMA_HAS_TABLES" = "t" ] && [ "${FORCE_RUN_ON_EMPTY_SCHEMA_MIGRATIONS:-false}" != "true" ]; then
        echo "[migrations] FATAL: schema_migrations is empty but ai_influencers already exists."
        echo "[migrations] This means migrations were applied OUTSIDE this runner (e.g. manually before"
        echo "[migrations] PR #309), and the runner would otherwise try to replay 001_initial.sql on a"
        echo "[migrations] populated database — which would either fail mid-transaction or corrupt data."
        echo "[migrations]"
        echo "[migrations] To unblock: run the one-shot bootstrap workflow that seeds schema_migrations"
        echo "[migrations] with the already-applied filenames, then re-run this deploy."
        echo "[migrations]"
        echo "[migrations] If you are SURE this is a fresh cluster and you want to actually apply"
        echo "[migrations] 001_initial.sql onward, set FORCE_RUN_ON_EMPTY_SCHEMA_MIGRATIONS=true."
        exit 1
    fi
fi

PENDING=0
while IFS= read -r FILE; do
    [ -z "$FILE" ] && continue
    BASENAME=$(basename "$FILE")

    if echo "$APPLIED" | grep -qF "$BASENAME"; then
        continue
    fi

    PENDING=$((PENDING + 1))

    if [ "${DRY_RUN}" = "true" ]; then
        echo "[migrations] WOULD APPLY: ${BASENAME}"
        continue
    fi

    echo "[migrations] applying: ${BASENAME}"

    LOCAL_C=$(find_local_patroni)

    docker exec -i "$LOCAL_C" \
        psql -U postgres -d "${POSTGRES_DB}" -tAc \
        "SELECT pg_create_restore_point('pre-migration-${BASENAME}');" 2>/dev/null \
        && echo "[migrations]   restore point: pre-migration-${BASENAME}" \
        || echo "[migrations]   restore point failed (non-fatal)"

    # Phase 21αβ.I-Mig1 — automated pre-migration pg_dump.
    # The WAL restore point above is the *fast* recovery handle (PITR
    # via WAL-G). This is the *slow* recovery handle (a full plain dump
    # that can be restored into a fresh cluster without WAL replay).
    # Both serve a purpose: PITR is faster but assumes the WAL stream
    # is intact; pg_dump is slower but is a self-contained artifact.
    #
    # Fatal on failure — if we can't take the safety snapshot, we
    # don't apply the migration. Rule 9 in CLAUDE.md is "before any
    # schema change, take a pg_dump snapshot first."
    #
    # Skip via PRE_MIGRATION_DUMP_ENABLED=false (e.g. in local CI test
    # runs that have already verified the migration in I-Mig3's
    # ephemeral pg). Default is enabled.
    PRE_MIGRATION_DUMP_ENABLED="${PRE_MIGRATION_DUMP_ENABLED:-true}"
    if [ "${PRE_MIGRATION_DUMP_ENABLED}" = "true" ]; then
        DUMP_TS=$(date -u +%Y%m%dT%H%M%SZ)
        DUMP_NAME="pre-migration-${BASENAME%.sql}-${DUMP_TS}.sql.gz"
        DUMP_LOCAL="/tmp/${DUMP_NAME}"
        S3_PREFIX="${PRE_MIGRATION_DUMP_S3_PREFIX:-s3://rishi-yral/yral-rishi-agent-pre-migration-dumps}"

        echo "[migrations]   pg_dump → ${DUMP_NAME}"

        # Custom format (-Fc) + level-6 gzip (matches the nightly
        # backup convention from docs/BACKUP-RESTORE-DRILL-2026-06-04.md).
        # `--no-owner --no-acl` so the dump restores cleanly into a
        # fresh cluster with potentially different roles.
        # No `-h` / no PGPASSWORD — UNIX socket trust path, same as
        # everywhere else in this script (see wait_for_db comment).
        if ! docker exec "$LOCAL_C" \
            pg_dump -Fc -Z 6 --no-owner --no-acl \
            -U postgres -d "${POSTGRES_DB}" \
            -f "${DUMP_LOCAL}" 2>&1; then
            echo "[migrations] FATAL: pre-migration pg_dump failed for ${BASENAME} — refusing to apply migration without a safety snapshot"
            exit 1
        fi

        # Upload to S3 (Hetzner Object Storage). Uses the AWS_*
        # credentials already mounted into the patroni container for
        # WAL-G. Endpoint + region pinned for Hetzner hel1.
        if ! docker exec "$LOCAL_C" \
            sh -c "command -v aws >/dev/null 2>&1"; then
            echo "[migrations] FATAL: 'aws' CLI not present in patroni container — pre-migration dump cannot be uploaded"
            exit 1
        fi

        if ! docker exec "$LOCAL_C" \
            aws --endpoint-url "${AWS_ENDPOINT:-https://hel1.your-objectstorage.com}" \
                s3 cp "${DUMP_LOCAL}" "${S3_PREFIX}/${DUMP_NAME}" 2>&1; then
            echo "[migrations] FATAL: pre-migration dump upload failed for ${BASENAME} — refusing to apply migration without a remote-stored snapshot"
            exit 1
        fi

        # Local cleanup. The S3 copy is the durable artifact.
        docker exec "$LOCAL_C" rm -f "${DUMP_LOCAL}" 2>/dev/null || true

        echo "[migrations]   pre-migration dump uploaded: ${S3_PREFIX}/${DUMP_NAME}"
    else
        echo "[migrations]   pre-migration dump SKIPPED (PRE_MIGRATION_DUMP_ENABLED=false)"
    fi

    {
        echo "BEGIN;"
        echo "SET lock_timeout = '5s';"
        cat "$FILE"
        echo "INSERT INTO schema_migrations (filename) VALUES ('${BASENAME}');"
        echo "COMMIT;"
    } | docker exec -i "$LOCAL_C" psql -U postgres -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 2>&1

    if [ $? -ne 0 ]; then
        echo "[migrations] FATAL: ${BASENAME} failed — transaction rolled back, deploy halted"
        exit 1
    fi

    echo "[migrations] applied: ${BASENAME}"
done <<< "$MIGRATION_FILES"

if [ "${DRY_RUN}" = "true" ]; then
    [ "$PENDING" -eq 0 ] && echo "[migrations] dry-run: all migrations already applied" || echo "[migrations] dry-run: ${PENDING} migration(s) would be applied"
elif [ "$PENDING" -eq 0 ]; then
    echo "[migrations] all migrations already applied"
else
    echo "[migrations] ${PENDING} migration(s) applied successfully"
fi
