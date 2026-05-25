# RUNBOOK.md — operator procedures for user-memory-service

## Prerequisites

- `user_memory_role` Postgres role + `user_memory` database provisioned on the Patroni cluster (DEP-011 — coordinator / Session 1 action)
- `POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE` Swarm secret created
- Docker Swarm cluster up and all overlays present

---

## Schema migration (standard deploy)

Run once per new database instance (new cluster, test environment, fresh staging reseed):

```bash
# From inside a one-off task container with the secret injected:
docker run --rm \
  -e POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE="<value>" \
  ghcr.io/dolr-ai/yral-rishi-agent-user-memory-service:<sha> \
  alembic upgrade head
```

Or from a shell on the cluster node with the env var exported:

```bash
export POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE="<value>"
cd /path/to/service
alembic upgrade head
```

Expected output:
```
INFO  [alembic.runtime.migration] Running upgrade  -> 001_initial_schema, Phase 1 initial schema
```

---

## Verify migration

```bash
# Connect to Postgres and check:
psql "$POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE"
\dt                         -- should show conversations + messages
\d conversations            -- should show all 9 columns
\d messages                 -- should show all 7 columns
```

---

## Deploy to cluster

```bash
# Build + push image (CI does this automatically on merge to main)
docker build -t ghcr.io/dolr-ai/yral-rishi-agent-user-memory-service:$GIT_SHA .
docker push ghcr.io/dolr-ai/yral-rishi-agent-user-memory-service:$GIT_SHA

# Deploy Swarm stack
IMAGE_TAG=$GIT_SHA \
IMAGE_REPO=ghcr.io/dolr-ai/yral-rishi-agent-user-memory-service \
source project.config
docker stack deploy -c docker-compose.swarm.yml $SWARM_STACK
```

---

## Health check

```bash
# Intra-cluster (from another service container):
curl http://yral-rishi-agent-user-memory-service:8000/health/ready

# Expected response:
{"status": "ok", "service": "yral-rishi-agent-user-memory-service"}
```

---

## Rollback a bad migration

```bash
# Step back one migration (reverts 001_initial_schema → empty schema)
alembic downgrade base

# Or step back exactly one revision:
alembic downgrade -1
```

⚠️ **WARNING**: `downgrade base` on the production cluster drops the `conversations` and `messages` tables. Only do this if the deployment has ZERO real user data (i.e. before Day-9 ETL or in a staging environment).

---

## Soft-delete a conversation (operator recovery)

To un-soft-delete a conversation that a user accidentally deleted:

```sql
UPDATE conversations
SET soft_deleted_at = NULL
WHERE id = '<conversation_uuid>';
```

This is a A1-gated operation (user data). Requires Rishi YES.

---

## Postgres provisioning (Session 1 / coordinator action)

⚠️ This is NOT a user-memory-service operator action — it's a cluster bootstrap step owned by Session 1.

The coordinator needs to run (on the Patroni cluster as the `postgres` superuser):

```sql
CREATE ROLE user_memory_role WITH LOGIN PASSWORD '<strong-password>';
CREATE DATABASE user_memory OWNER user_memory_role;
GRANT CONNECT ON DATABASE user_memory TO user_memory_role;
-- Inside the user_memory database:
CREATE SCHEMA user_memory AUTHORIZATION user_memory_role;
GRANT ALL ON SCHEMA user_memory TO user_memory_role;
```

Then create the Swarm secret:
```bash
echo -n "postgresql://user_memory_role:<password>@<pgbouncer-host>:6432/user_memory?options=-csearch_path%3Duser_memory" \
  | docker secret create POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE -
```

See DEP-011 in `cross-session-dependencies.md`.

---

## ETL Day-9 — migrate chat-ai conversations + messages to v2

🚨 **A14 GATE**: This operation reads live chat-ai data. Coordinator MUST obtain
explicit Rishi YES before running ANY of the steps below.

### Pre-requisites

1. `alembic upgrade head` has been run on the v2 user-memory DB (all 4 migrations: 001, 002, 003, **004**).
   Migration 004 (`004_add_sequence_in_conversation`) adds the `sequence_in_conversation` column,
   backfills existing rows (none in a fresh DB), and creates `messages_by_conversation_sequence_idx`.
   The ETL's Phase 4 `backfill_sequence_in_conversation()` step sets the correct ROW_NUMBER() ordinals
   for all migrated messages after they are loaded.
2. A **read-only** Postgres connection string for chat-ai's DB is available.
3. `POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE` Swarm secret is live.
4. Row count snapshot taken on chat-ai BEFORE the run:

   ```bash
   # Run on chat-ai Postgres as a pre-ETL baseline:
   psql "$CHAT_AI_POSTGRES_URL" -c "SELECT count(*) FROM conversations;"
   psql "$CHAT_AI_POSTGRES_URL" -c "SELECT count(*) FROM messages;"
   # Record both numbers for post-ETL verification.
   ```

### Run the ETL script

```bash
# Install asyncpg on the runner machine:
pip install asyncpg

# Set env vars (NOT passed as CLI args — avoid appearing in ps aux):
export CHAT_AI_POSTGRES_URL="postgresql://..."
export POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE="postgresql://..."

# Dry run first — prints counts, no writes:
python3 etl-scripts/chat_ai_to_user_memory_etl.py --dry-run

# Full run (coordinator executes after Rishi YES):
python3 etl-scripts/chat_ai_to_user_memory_etl.py

# If the run is interrupted, it's safe to re-run:
#   Phase 1 + 2: ON CONFLICT (id) DO NOTHING — already-loaded rows are skipped.
#   Phase 3: UPDATE WHERE message_count = 0 — already-updated conversations are skipped.
#   Phase 4: ROW_NUMBER() UPDATE on ALL messages (idempotent — assigns the same
#            ordinals every time). Re-running Phase 4 is harmless.
python3 etl-scripts/chat_ai_to_user_memory_etl.py
```

### ETL phase run order

The script runs 4 phases automatically in this order:

| Phase | Name | Description | Idempotent? |
|-------|------|-------------|-------------|
| 1 | `migrate_conversations` | Keyset-paginated COPY + INSERT from chat-ai → v2 | Yes — `ON CONFLICT (id) DO NOTHING` |
| 2 | `migrate_messages` | Same pattern for 3.3M messages | Yes — `ON CONFLICT (id) DO NOTHING` |
| 3 | `update_message_counts` | Bulk UPDATE `conversations.message_count` WHERE = 0 | Yes — skips already-updated rows |
| 4 | `backfill_sequence_in_conversation` | ROW_NUMBER() window UPDATE assigns ordinals | Yes — ROW_NUMBER() produces the same output every run |

Phase 4 sets `sequence_in_conversation` on all migrated messages to enable deterministic
ordering within same-timestamp batches (see migration 004). Re-running the ETL script
will harmlessly re-apply Phase 4 with identical ordinals.

### Verify after the run

The script prints a VERIFICATION REPORT at the end. Expected output:

```
ETL VERIFICATION REPORT
  conversations:
    chat-ai (source) :    284,XXX
    v2 user-memory   :    284,XXX  [OK]
  messages:
    chat-ai (source) :  3,300,XXX
    v2 user-memory   :  3,300,XXX  [OK]
```

Acceptable delta: ≤ 500 conversations, ≤ 5,000 messages (live traffic during ETL window).
If the delta is larger, re-run the script (idempotent) and verify again.

### Log the pull

After successful verification, record the pull in:
`yral-rishi-agent-plan-and-discussions/running-coordination-asks-plus-mobile-team-memo-and-change-log/live-data-pulls-log.md`

Entry format:
```
## YYYY-MM-DD — chat-ai conversations + messages ETL

- Source: chat-ai Postgres (READ ONLY)
- Destination: v2 user-memory-service Postgres
- Rows: ~284K conversations + ~3.3M messages
- Verification: counts matched within tolerance
- Approved: Rishi YES at [timestamp]
- Run by: [coordinator name]
```

See `etl-scripts/etl-plan-day-9-draft.md` for the full column mapping and plan.

---

## Known failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE is empty` | Secret not injected or bridge wrapper not running | Check docker-compose.swarm.yml `command:` has the for-loop wrapper |
| `asyncpg.exceptions.InvalidPasswordError` | Wrong password in connection string | Re-create Swarm secret with correct password |
| `alembic.exc.CannotSendRequest: alembic.ini not found` | alembic.ini not in image | Check Dockerfile has `COPY alembic.ini ./alembic.ini` |
| `/health/ready` returns 503 | DB pool failed to init | Check logs for asyncpg error; verify Patroni is up |
