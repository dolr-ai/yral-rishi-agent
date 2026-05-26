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

## Known failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `RuntimeError: POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE is empty` | Secret not injected or bridge wrapper not running | Check docker-compose.swarm.yml `command:` has the for-loop wrapper |
| `asyncpg.exceptions.InvalidPasswordError` | Wrong password in connection string | Re-create Swarm secret with correct password |
| `alembic.exc.CannotSendRequest: alembic.ini not found` | alembic.ini not in image | Check Dockerfile has `COPY alembic.ini ./alembic.ini` |
| `/health/ready` returns 503 | DB pool failed to init | Check logs for asyncpg error; verify Patroni is up |
