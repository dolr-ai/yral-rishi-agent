# SECURITY.md — security model for user-memory-service

## What data this service holds

- **Conversation rows**: user_id (opaque UUID), influencer_id, conversation type, timestamps
- **Message rows**: content text (chat messages), media_urls (presigned URLs), role, timestamps, Gemini LLM call metadata

**Classification**: HIGH — this service holds all user chat history. A data breach would expose private conversations.

## Threat model

### Unauthorized read of another user's conversations
- **Mitigation**: every RPC call from the orchestrator or public-api includes a `user_id` claim validated upstream by public-api's JWT middleware. The RPC endpoints (Deliverable 2) will assert that the requesting user_id matches the conversation's user_id.
- **Defense in depth**: Postgres role `user_memory_role` has no access to other services' schemas (F3 isolation).

### Conversation ID enumeration
- **Mitigation**: UUIDs are random (gen_random_uuid()) — not sequential integers. An attacker cannot enumerate IDs.

### Database credential leak
- **Mitigation**: per D8, the connection string is in a Swarm secret, never in code or env vars in compose files. The D8 name (`_USER_MEMORY_SERVICE` suffix) limits blast radius to this service.

### Log-based PII exfiltration
- **Mitigation**: `app/logging.py` ships an allowlist-based PII redaction processor (per H6). Message content (`content` field) is NOT on the allowlist — it will never appear in log output.

## Secret management

| Secret | Storage | Rotation |
|---|---|---|
| `POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE` | GitHub Secret → Swarm secret | Every 90 days |
| `SENTRY_DSN` | GitHub Secret → Swarm secret | Stable (rotate if Sentry migrates) |
| `LANGFUSE_PUBLIC_KEY` | GitHub Secret → Swarm secret | Every 180 days |
| `LANGFUSE_SECRET_KEY` | GitHub Secret → Swarm secret | Every 180 days |

## A1 carve-outs granted (deletion that is explicitly safe)

The following deletions are pre-authorised and do NOT require Rishi YES:

1. **`alembic downgrade base` in test environments** — drops the `conversations` and `messages` tables created moments earlier by the migration under test (testcontainers-postgres, ephemeral). Evidence in `001_initial_schema.py`'s A1 JUSTIFICATION block.

## What requires Rishi YES

- Running `alembic downgrade` on the production Patroni cluster
- Any direct `UPDATE` or `DELETE` query against `conversations` or `messages` on production (user data, per A1 hard-stop)
- Reading the live chat-ai database for ETL (per A14)
- The Day-9 ETL run itself (per A14)

## Non-root container

The Dockerfile creates and switches to `appuser` (UID 1001) before `CMD`. The running process does NOT have root access inside the container.
