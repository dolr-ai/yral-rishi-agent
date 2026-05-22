# yral-rishi-agent-user-memory-service

**Purpose:** Phase 1 — conversation history persistence. Stores and serves every chat conversation and message for the yral v2 mobile chat surface.

**Status:** Deliverable 1 (schema + Alembic migration) complete. Deliverable 2 (RPC endpoints) in progress.

## What this service does

Every time a user sends a chat message to an AI Influencer on YRAL, the orchestrator saves both the user's message and the AI's reply here. The mobile inbox screen reads conversation history from here. This is the source of truth for all 284K conversations + 3.3M messages that will be ported from chat-ai on Day 9 (per CONSTRAINTS A4).

## Phase 1 scope (this service)

- `conversations` table — one row per chat thread
- `messages` table — one row per chat turn (append-only)
- Alembic migration with upgrade + downgrade round-trip tested
- Soft-delete on conversations (v2 improvement over chat-ai's hard-delete)
- RPC endpoints (Deliverable 2): POST/GET conversations, POST/GET messages

## Phase 2 scope (deferred — NOT this service's current scope)

Semantic memory, pgvector embeddings, RAG, cross-conversation reasoning — deferred per 2026-05-22 rescope. Phase 1 parity gap (0 v2 equivalent for 3.3M chat-ai messages) was the #1 mobile parity gap.

## Quick start

```bash
# Local dev
docker compose up --build
# In another terminal, after containers are healthy:
alembic upgrade head

# Run tests (requires Docker for testcontainers)
pip install -e ".[dev]"
pytest tests/ -v
```

## Key files

- `app/migrations/versions/001_initial_schema.py` — the schema definition
- `tests/test_schema_migrations.py` — round-trip migration test
- `secrets.yaml` — secret declarations per D8
- `RUNBOOK.md` — operator procedures (migrations, deploy, rollback)

See `READING-ORDER.md` for the full file reading sequence.
