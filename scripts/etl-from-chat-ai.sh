#!/bin/bash
# etl-from-chat-ai.sh — migrate data from chat-ai (rishi-1/2/3) to v2 (rishi-4/5/6)
#
# Safety: works from a pg_dump snapshot, never touches the live chat-ai DB.
# Loads into a temporary schema, validates row counts, then copies to production tables.
#
# Prerequisites:
#   - pg_dump snapshot file from chat-ai DB (taken separately, stored locally)
#   - psql access to the v2 Patroni leader (rishi-4/5/6)
#   - v2 migrations already applied (001_initial.sql, 002_influencer_trending_stats.sql)
#
# Usage:
#   SNAPSHOT_FILE=/path/to/chat_ai_dump.sql \
#   V2_DATABASE_URL=postgresql://postgres:pass@rishi-4:5432/chat_ai_db \
#   bash scripts/etl-from-chat-ai.sh

set -euo pipefail

: "${SNAPSHOT_FILE:?Set SNAPSHOT_FILE to the pg_dump file path}"
: "${V2_DATABASE_URL:?Set V2_DATABASE_URL to the v2 Patroni leader connection string}"

echo "[etl] Starting data migration from chat-ai snapshot"
echo "[etl] Snapshot: ${SNAPSHOT_FILE}"

if [ ! -f "${SNAPSHOT_FILE}" ]; then
    echo "[etl] FATAL: snapshot file not found: ${SNAPSHOT_FILE}"
    exit 1
fi

run_sql() {
    psql "${V2_DATABASE_URL}" -v ON_ERROR_STOP=1 -c "$1"
}

run_sql_file() {
    psql "${V2_DATABASE_URL}" -v ON_ERROR_STOP=1 -f "$1"
}

# Step 1: Create temporary staging schema
echo "[etl] Creating staging schema..."
run_sql "DROP SCHEMA IF EXISTS etl_staging CASCADE;"
run_sql "CREATE SCHEMA etl_staging;"

# Step 2: Load snapshot into staging schema
echo "[etl] Loading snapshot into staging schema (this may take a few minutes)..."
# Rewrite the snapshot to target etl_staging schema
sed 's/^SET search_path/SET search_path = etl_staging, pg_catalog;--/' "${SNAPSHOT_FILE}" | \
    psql "${V2_DATABASE_URL}" -v ON_ERROR_STOP=1 2>&1 | tail -5

# Step 3: Validate row counts
echo "[etl] Validating staging data..."
STAGING_INFLUENCERS=$(psql "${V2_DATABASE_URL}" -tAc "SELECT COUNT(*) FROM etl_staging.ai_influencers;" 2>/dev/null || echo "0")
STAGING_CONVERSATIONS=$(psql "${V2_DATABASE_URL}" -tAc "SELECT COUNT(*) FROM etl_staging.conversations;" 2>/dev/null || echo "0")
STAGING_MESSAGES=$(psql "${V2_DATABASE_URL}" -tAc "SELECT COUNT(*) FROM etl_staging.messages;" 2>/dev/null || echo "0")

echo "[etl] Staging counts: ${STAGING_INFLUENCERS} influencers, ${STAGING_CONVERSATIONS} conversations, ${STAGING_MESSAGES} messages"

if [ "${STAGING_INFLUENCERS}" = "0" ]; then
    echo "[etl] WARNING: zero influencers in staging — snapshot may be empty or schema mismatch"
    echo "[etl] Aborting. Check the snapshot file format."
    exit 1
fi

# Step 4: Copy staging data into production tables
echo "[etl] Copying data to production tables..."

# Disable triggers during bulk load (conversation timestamp trigger would be slow)
run_sql "ALTER TABLE messages DISABLE TRIGGER trigger_update_conversation_timestamp;"

# Influencers first (conversations reference them via FK)
run_sql "INSERT INTO ai_influencers SELECT * FROM etl_staging.ai_influencers ON CONFLICT (id) DO NOTHING;"

# Conversations next (messages reference them via FK)
run_sql "INSERT INTO conversations SELECT * FROM etl_staging.conversations ON CONFLICT (id) DO NOTHING;"

# Messages last
run_sql "INSERT INTO messages SELECT * FROM etl_staging.messages ON CONFLICT (id) DO NOTHING;"

# Re-enable triggers
run_sql "ALTER TABLE messages ENABLE TRIGGER trigger_update_conversation_timestamp;"

# Step 5: Verify production counts
PROD_INFLUENCERS=$(psql "${V2_DATABASE_URL}" -tAc "SELECT COUNT(*) FROM ai_influencers;")
PROD_CONVERSATIONS=$(psql "${V2_DATABASE_URL}" -tAc "SELECT COUNT(*) FROM conversations;")
PROD_MESSAGES=$(psql "${V2_DATABASE_URL}" -tAc "SELECT COUNT(*) FROM messages;")

echo "[etl] Production counts: ${PROD_INFLUENCERS} influencers, ${PROD_CONVERSATIONS} conversations, ${PROD_MESSAGES} messages"

# Step 6: Refresh materialized view
echo "[etl] Refreshing influencer_trending_stats..."
run_sql "REFRESH MATERIALIZED VIEW influencer_trending_stats;"

# Step 7: Clean up staging schema
echo "[etl] Dropping staging schema..."
run_sql "DROP SCHEMA etl_staging CASCADE;"

echo "[etl] Migration complete."
echo "[etl] Keep the snapshot file for 30 days: ${SNAPSHOT_FILE}"
