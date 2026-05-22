# GLOSSARY.md — plain-English definitions for user-memory-service

## Domain terms

**Conversation** — one chat thread between a user and an AI Influencer (or between two users for H2H chat). A conversation has many messages. The mobile inbox shows a list of conversations.

**Message** — one chat turn: either something the user typed, or the AI's reply. Messages belong to exactly one conversation. They are never edited after creation (append-only).

**AI Influencer** — an AI persona on YRAL that users can chat with. In this service's schema, `influencer_id` on a conversation row is the UUID of the AI Influencer. "AI Influencer" is the DOLR product term — never say "bot" (per B4).

**Soft delete** — marking a row as deleted without physically removing it from the database. In this service: `DELETE /conversations/{id}` sets `soft_deleted_at = NOW()`. The row still exists; the data is recoverable by the coordinator. Mobile queries filter `WHERE soft_deleted_at IS NULL` so deleted conversations don't appear.

**conversation_type** — one of three values: `ai_chat` (user ↔ AI Influencer), `human_chat` (user ↔ another user), `chat_as_human` (AI Influencer role-plays as a human). Matches the public-api `ConversationResponse.conversation_type` field exactly.

**role** — who sent a message: `user` (the human on mobile), `assistant` (the AI's reply), or `system` (internal messages never sent to mobile; used by the orchestrator for context framing).

**gemini_metadata** — JSONB column on the `messages` table. Stores LLM call metadata for assistant messages: `prompt_tokens`, `completion_tokens`, `model`, `latency_ms`. Null for user messages.

**Alembic** — the Python database migration tool. When this service needs to change its schema, a new file is added to `app/migrations/versions/` and `alembic upgrade head` is run once on the cluster.

**asyncpg** — the Python library this service uses to talk to Postgres. Async (non-blocking), so many concurrent chat turns can proceed without queuing.

**pgBouncer** — a connection pooler that sits between this service and Postgres. Multiplexes many service connections into fewer actual Postgres connections, reducing load. Always in the connection path (per G3).

**Patroni** — the high-availability Postgres cluster running on rishi-4/5/6. This service's data lives there. Not something this service configures — it's owned by Session 1.

**testcontainers** — a Python library used in tests that spins up a real Postgres container in Docker for the duration of the test run, then tears it down. Used instead of mocking so migrations + queries run against a real DB.

**ETL** — Extract, Transform, Load. The Day-9 operation that copies all 284K conversations + 3.3M messages from chat-ai (v1) into this service's tables. Needs Rishi's explicit YES per A14 before running.

**DEP** — dependency entry in `cross-session-dependencies.md`. DEP-011 is the open request for the coordinator / Session 1 to provision the `user_memory_role` Postgres role.

**Phase 1 / Phase 2** — Phase 1 = this service's current scope (conversation history persistence, transactional). Phase 2 = deferred scope (semantic memory, pgvector, RAG).

## Abbreviations used in this service

Per B2: only these abbreviations are allowed:
- `id` — identifier (UUID)
- `url` — uniform resource locator
- `api` — application programming interface
- `json` — JavaScript Object Notation (schema format)
- `sql` — Structured Query Language
- `utc` — coordinated universal time
- `uuid` — universally unique identifier
- `app` — application (module path)
- `init` — initialization function
