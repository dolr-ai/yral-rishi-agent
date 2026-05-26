# WHEN-YOU-GET-LOST.md — north-star orientation for user-memory-service

## The restaurant analogy

Think of this service as the **order history book** at a restaurant:

- Each **conversation** is a table booking (one thread between a customer + the AI waiter)
- Each **message** is one food order or response at that table
- The **inbox** (mobile's chat list) is the host's view of all active tables
- **Soft delete** is when a table booking is "cancelled but kept in the records"
- **Day-9 ETL** is the one-time process of copying all the old restaurant's order history into the new restaurant's system

## The one sentence that explains everything

> This service is a PostgreSQL-backed append-only store that remembers every conversation and message in the v2 chat system.

## If you don't know where you are

1. **Open `app/migrations/versions/001_initial_schema.py`** — it defines the two tables (`conversations` + `messages`). Everything this service does is either reading from or writing to these two tables.

2. **Open `app/database.py`** — it shows how the service connects to Postgres. If something is broken with the DB connection, start here.

3. **Open `app/main.py`** — it shows the FastAPI app entry point + health routes. If the service won't start, read the lifespan hook here.

## The three questions to ask yourself before changing anything

1. **Does this change the schema?** If yes → write an Alembic migration (`app/migrations/versions/002_...py`). Never ALTER a table directly.

2. **Does this add a secret or config value?** If yes → add it to `secrets.yaml` (for secrets) or `project.config` (for non-secret values). Never hardcode.

3. **Am I going out of Phase 1 scope?** Phase 1 = transactional storage, no embeddings, no vectors. If you're thinking about pgvector or ML → STOP and check with Rishi first.

## Where the important things live

| I want to... | I should look at... |
|---|---|
| See the database schema | `app/migrations/versions/001_initial_schema.py` |
| Understand the connection to Postgres | `app/database.py` |
| Change service config (non-secret) | `project.config` |
| Add / change a secret | `secrets.yaml` |
| Understand how Alembic runs migrations | `app/migrations/env.py` |
| Run the schema tests locally | `pytest tests/test_schema_migrations.py -v` |
| Deploy to the cluster | `RUNBOOK.md` → "Schema migration" section |
| See what RPC endpoints exist | `app/main.py` (Phase 1: none yet; Deliverable 2) |

## The parity gap context

chat-ai (v1) has 284K conversations + 3.3M messages. v2 had zero equivalent storage before 2026-05-22. Mobile parity testing exposed this. This service closes that gap. Day-9 ETL will port all the v1 data in once Rishi types YES.
