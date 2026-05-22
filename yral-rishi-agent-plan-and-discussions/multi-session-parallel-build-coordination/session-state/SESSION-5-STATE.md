# Session 5 STATE — ETL + Tests + Memory
> Updated: 2026-05-22 (Deliverable 1 complete — schema + Alembic migration)

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 5. Phase 1 scope (rescoped on 2026-05-22): conversation history
persistence for `yral-rishi-agent-user-memory-service`. NOT semantic memory /
pgvector / RAG (those remain Phase 2). Mobile testing exposed conversation
history as the #1 parity gap (chat-ai: 284K conversations + 3.3M messages;
v2: zero equivalent storage).

Three Phase-1 deliverables:
- D1 (DONE): Schema + Alembic migration → branch session-5/user-memory-schema-and-migration
- D2 (NEXT): RPC endpoints (POST/GET conversations + messages)
- D3 (TODO): ETL migration plan + DEP asking coordinator to port the data

## LAST THING I DID

Deliverable 1: Created `yral-rishi-agent-user-memory-service/` from scratch:
- `app/` package: config.py, database.py, main.py, sentry_middleware.py,
  langfuse_middleware.py, logging.py, request_id_middleware.py
- `app/migrations/`: alembic env.py + 001_initial_schema.py (conversations +
  messages tables with correct FK, check constraints, partial index for soft-delete)
- `tests/`: conftest.py (testcontainers session-scoped fixtures) +
  test_schema_migrations.py (7 tests: round-trip, column checks, insert smoke,
  FK/JSONB, check-constraint rejection)
- Infrastructure: pyproject.toml, Dockerfile (two-stage), docker-compose.yml
  (local: service + postgres + pgbouncer), docker-compose.swarm.yml
  (staging default per PR #125 lesson), alembic.ini, project.config, secrets.yaml
- 8 F8 docs: README.md, CLAUDE.md, READING-ORDER.md, DEEP-DIVE.md, GLOSSARY.md,
  WHEN-YOU-GET-LOST.md, WALKTHROUGH.md, RUNBOOK.md, SECURITY.md
- DEP-012 raised in cross-session-dependencies.md: coordinator / Session 1 must
  provision `user_memory_role` + `user_memory` DB on Patroni cluster before deploy

## CURRENT TASK

Deliverable 1 PR: push branch session-5/user-memory-schema-and-migration + open PR.
After PR merges → Deliverable 2 (RPC endpoints).

## NEXT 3 PLANNED ACTIONS

1. Push branch + open PR for Deliverable 1 (schema + migration)
2. Start Deliverable 2 on branch session-5/user-memory-rpc-endpoints:
   - POST /v1/conversations
   - POST /v1/conversations/{id}/messages
   - GET /v1/conversations/{id}/messages (offset/limit pagination)
   - GET /v1/conversations/by-user/{user_id}
3. Start Deliverable 3 (ETL migration plan draft) — column mapping chat-ai → v2

## BLOCKERS

- DEP-012 OPEN: user-memory-service cannot deploy to staging until coordinator /
  Session 1 provisions `user_memory_role` + `user_memory` DB on Patroni cluster.
  No block on local docker-compose up (local compose brings its own Postgres).
- Day-9 ETL run BLOCKED until Rishi types YES per A14.

## PENDING PRs (mine)

- PR for Deliverable 1: session-5/user-memory-schema-and-migration (opening now)

## CROSS-SESSION DEPS (mine)

- DEP-012 OPEN: Postgres provisioning for user_memory_role + user_memory DB
  (coordinator / Session 1 operator action on Patroni cluster)
- Day-9 ETL approval: Rishi YES per A14 (will surface as separate DEP closer to Day 9)
