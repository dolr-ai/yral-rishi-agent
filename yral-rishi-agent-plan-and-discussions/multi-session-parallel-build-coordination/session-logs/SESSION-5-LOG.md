# Session 5 LOG — ETL + Tests + Memory
> Append-only diary. Most recent entries at TOP. Auto-appended by `.claude/hooks/post-tool-use.sh` on every git commit. Manual milestone entries welcome.

---

### 2026-05-22 — Deliverable 1: user-memory-service schema + Alembic migration

**Branch**: session-5/user-memory-schema-and-migration
**Trigger**: Mobile testing 2026-05-22 exposed conversation history as the #1 parity
gap — chat-ai has 284K conversations + 3.3M messages; v2 had zero equivalent storage.
Rishi rescoped Session 5 Phase 1 to conversation history persistence (transactional,
chronological). Phase 2 (pgvector / embeddings / RAG) remains deferred.

**What shipped in this PR**:

1. Full `yral-rishi-agent-user-memory-service/` service from scratch (not spawned
   from template — built directly against the soul-file-library pattern):
   - `app/migrations/versions/001_initial_schema.py`: `conversations` table (9 cols:
     id, user_id, influencer_id, participant_b_id, conversation_type [CHECK constraint],
     created_at, last_message_at, message_count, soft_deleted_at) + `messages` table
     (7 cols: id, conversation_id [FK CASCADE], role [CHECK constraint], content, media_urls
     JSONB, created_at, gemini_metadata JSONB). Three indexes: partial
     `conversations_by_user_active_idx` (WHERE soft_deleted_at IS NULL), full
     `conversations_by_user_all_idx`, `messages_by_conversation_time_idx`.
   - `app/` package: config.py (pydantic-settings, validation_alias for
     POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE), database.py (asyncpg pool
     singleton, statement_cache_size=0 for pgBouncer), main.py (FastAPI + lifespan),
     sentry_middleware.py, langfuse_middleware.py (Phase-1 no-op), logging.py
     (structlog + PII allowlist), request_id_middleware.py.
   - `tests/conftest.py`: testcontainers session-scoped postgres fixture + autouse
     alembic upgrade + per-function database_pool with TRUNCATE.
   - `tests/test_schema_migrations.py`: 7 tests (round-trip upgrade/downgrade, column
     presence, insert smoke, FK + JSONB, check-constraint rejection for both tables).
   - Infrastructure: pyproject.toml, Dockerfile (two-stage), docker-compose.yml
     (local: service + postgres:17-alpine + pgbouncer), docker-compose.swarm.yml
     (ENVIRONMENT defaults to staging per PR #125 lesson), alembic.ini,
     project.config, secrets.yaml (D8 manifest, 4 secrets).
   - 8 F8 docs: README.md (updated from placeholder), CLAUDE.md, READING-ORDER.md,
     DEEP-DIVE.md, GLOSSARY.md, WHEN-YOU-GET-LOST.md, WALKTHROUGH.md, RUNBOOK.md,
     SECURITY.md.

2. DEP-012 raised in cross-session-dependencies.md: coordinator / Session 1 must
   provision `user_memory_role` + `user_memory` database on the Patroni cluster before
   user-memory-service can deploy to staging. Full SQL + Swarm secret creation
   procedure documented in RUNBOOK.md. Session 5 does NOT execute CREATE ROLE /
   CREATE DATABASE (A1 hard-stop).

**Key decisions**:
- No shared-config.yaml (Phase 1 has no YAML config at module-load time; added only
  when a consumer exists per A2.1).
- No Redis in docker-compose.yml (Phase 1 is Postgres-only; A2.1 forbids adding
  infra without a consumer).
- `soft_deleted_at TIMESTAMPTZ` on conversations — v2 improvement over chat-ai's
  hard-delete; partial index keeps mobile inbox hot path fast.
- `gemini_metadata JSONB` on messages — LLM call metadata (prompt_tokens,
  completion_tokens, model, latency_ms) stored per-assistant-message; null for
  user messages.
- `ENVIRONMENT: ${ENVIRONMENT:-staging}` in swarm compose — per PR #125 lesson
  (dev cluster is not production).
- `statement_cache_size=0` on asyncpg — required for pgBouncer transaction-mode
  compatibility (per G3, per soul-file-library pattern).

**Next**: Deliverable 2 (RPC endpoints) on branch session-5/user-memory-rpc-endpoints.
DEP-012 must be resolved before endpoints can be tested against the staging cluster
(local compose works without it).
