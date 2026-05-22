# CLAUDE.md — AI agent operating instructions for user-memory-service

## ⚠️ Read these rules BEFORE touching any file in this service

### Rule 1 — Names must be English (per B1 + B5)
Every identifier — Python functions, variables, SQL column names, Docker labels, env var names, file names — must read as plain English to a non-programmer. No abbreviations except the B2 allowed list: `id`, `url`, `api`, `http`, `json`, `sql`, `utc`, `tls`, `dns`, `ssl`, `css`, `html`, `uuid`, `ip`, `app`, `init`, `ci`.

### Rule 2 — Document at 3 tiers (per B7)
Every code file must have: (a) file-header block with one-sentence summary + plain English explanation + ⭐ START HERE pointer; (b) each function with WHAT/WHEN/WHY block + line-level role comments; (c) RELATED FILES footer. Comments explain the ROLE in the bigger picture, not the syntax.

### Rule 3 — This service's Phase 1 scope
**Phase 1 = conversation history persistence (transactional, chronological).** Append-only turn storage, ordered reads, basic CRUD. NOT semantic memory, NOT pgvector, NOT RAG, NOT cross-conversation reasoning. If a change involves embeddings or vector search, STOP and ask Rishi — it's out of Phase 1 scope.

### Rule 4 — No SQLAlchemy ORM (per F12 / directive)
asyncpg + raw SQL + Pydantic. No SQLAlchemy models in `app/`. Alembic uses SQLAlchemy's AsyncEngine ONLY for migration execution (not for ORM models).

### Rule 5 — Postgres provisioning is a coordinator action
The `user_memory_role` Postgres role + `user_memory` database on the Patroni cluster must be provisioned by the coordinator / Session 1 before first deploy. See DEP-011 in `cross-session-dependencies.md`. Never attempt to CREATE ROLE or CREATE DATABASE yourself — that's an A1 hard-stop action.

### Rule 6 — The secret name must stay `POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE`
The D8 convention: per-service secret name includes the service-name suffix for blast-radius clarity. Do NOT shorten or rename this env var without a coordinator decision.

### Rule 7 — Sentry target is sentry.rishi.yral.com (per A7)
NEVER `apm.yral.com`. The SENTRY_DSN value must point at Rishi's self-hosted Sentry. This has been reinforced 3+ times.

## ⭐ Key file to start reading: `app/migrations/versions/001_initial_schema.py`
This is the heart of Phase 1 — the schema definition. Read it before any other code file.

## Session 5 ownership
This service is owned by Session 5. The owning branch convention is `session-5/<feature>`. Code changes from other sessions must be coordinated via cross-session-dependencies.md.
