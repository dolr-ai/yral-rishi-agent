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

wait_for_db() {
    echo "[migrations] waiting for database (up to 120s)..."
    for i in $(seq 1 40); do
        LOCAL_C=$(find_local_patroni)
        if [ -n "$LOCAL_C" ]; then
            PG_PASS=$(docker exec "$LOCAL_C" cat /run/secrets/postgres_password 2>/dev/null || echo "")
            if docker exec -e PGPASSWORD="$PG_PASS" "$LOCAL_C" psql -h localhost -U postgres -d "${POSTGRES_DB}" -tAc "SELECT 1;" >/dev/null 2>&1; then
                echo "[migrations] database reachable after $((i*3))s"
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
    docker exec -i -e PGPASSWORD="$(docker exec "$LOCAL_C" cat /run/secrets/postgres_password 2>/dev/null)" \
        "$LOCAL_C" psql -h localhost -U postgres -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 <<< "$sql"
}

wait_for_db || exit 1

run_sql "CREATE TABLE IF NOT EXISTS schema_migrations (
    filename VARCHAR(255) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);"

LOCAL_C=$(find_local_patroni)
PG_PASS=$(docker exec "$LOCAL_C" cat /run/secrets/postgres_password 2>/dev/null || echo "")
APPLIED=$(docker exec -i -e PGPASSWORD="$PG_PASS" "$LOCAL_C" psql -h localhost -U postgres -d "${POSTGRES_DB}" -tA \
    -c "SELECT filename FROM schema_migrations ORDER BY filename;" 2>/dev/null || true)

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
    PG_PASS=$(docker exec "$LOCAL_C" cat /run/secrets/postgres_password 2>/dev/null || echo "")

    docker exec -i -e PGPASSWORD="$PG_PASS" "$LOCAL_C" \
        psql -h localhost -U postgres -d "${POSTGRES_DB}" -tAc \
        "SELECT pg_create_restore_point('pre-migration-${BASENAME}');" 2>/dev/null \
        && echo "[migrations]   restore point: pre-migration-${BASENAME}" \
        || echo "[migrations]   restore point failed (non-fatal)"

    {
        echo "BEGIN;"
        echo "SET lock_timeout = '5s';"
        cat "$FILE"
        echo "INSERT INTO schema_migrations (filename) VALUES ('${BASENAME}');"
        echo "COMMIT;"
    } | docker exec -i -e PGPASSWORD="$PG_PASS" "$LOCAL_C" psql -h localhost -U postgres -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 2>&1

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
