# DEEP-DIVE.md — architecture decisions for user-memory-service

## What problem does this service solve?

chat-ai (the v1 Python monolith) stores 284K conversations and 3.3M messages in its Postgres database. v2 had no equivalent until 2026-05-22, when mobile testing exposed this as the #1 parity gap. Without conversation history, the mobile inbox screen cannot show previous chat threads, and the orchestrator cannot provide context from prior turns to the LLM.

This service IS the v2 equivalent of chat-ai's conversation history store.

## Data model rationale

### Why two tables?

**`conversations`** (one row per thread) and **`messages`** (one row per turn) have a 1:N relationship. Two tables keep the hot paths clean:

- **Inbox load** (mobile opens the chat list): reads `conversations` only — no `messages` needed. A single index scan.
- **History load** (user scrolls up in a thread): reads `messages` for one conversation — no `conversations` join needed.

Denormalizing into one table would require GROUP BY + window functions on the inbox load, which is the hottest path in the system.

### Why `soft_deleted_at` instead of hard-delete?

chat-ai hard-deletes conversations. v2 improves on this: when mobile sends DELETE /conversations/{id}, this service sets `soft_deleted_at = NOW()`. The row still exists; the data is recoverable. The inbox query filters `WHERE soft_deleted_at IS NULL` (partial index covers this exactly).

This matters for the Day-9 ETL: chat-ai source rows have no `soft_deleted_at` concept → they migrate with `soft_deleted_at = NULL` (all active).

### Why `gemini_metadata JSONB` instead of separate columns?

The orchestrator records `{prompt_tokens, completion_tokens, model, latency_ms}` for each Gemini call. Storing as JSONB means:
- No migration needed when a new field is added (e.g. `cost_rupees`)
- No null columns proliferate as the metadata schema evolves
- The shape is queryable via Postgres JSON operators if needed

### Why UUIDs for primary keys?

- **Non-enumerable**: clients can't guess other conversations' IDs
- **Globally unique**: safe to generate on the mobile client side and pass in the create request
- **Stable across ETL**: chat-ai's conversation UUIDs port forward unchanged so existing mobile state (locally cached conversation IDs) continues to resolve post-cutover

## Index design

```
conversations_by_user_active_idx  ON (user_id, last_message_at DESC)
                                  WHERE soft_deleted_at IS NULL
```
Partial index — mobile inbox hot path. Only active conversations; smaller + faster.

```
conversations_by_user_all_idx     ON (user_id, created_at DESC)
```
Full index — ETL + admin queries that need soft-deleted rows too.

```
messages_by_conversation_time_idx ON (conversation_id, created_at ASC)
```
History read hot path — messages for a thread in chronological order.

## Soft-delete design

`WHERE soft_deleted_at IS NULL` is the standard filter for "show me active conversations." The partial index makes this fast. The ETL script sets `soft_deleted_at = NULL` for all migrated rows (chat-ai had no concept of this column).

## Phase 1 vs Phase 2 separation

**Phase 1 (this service):** transactional, chronological, append-only. No ML, no vectors, no RAG.

**Phase 2 (deferred):** pgvector embeddings, semantic facts extraction, cross-conversation reasoning. Will be a separate PR / separate schema tables. Does NOT change Phase 1's tables.

## Connection to the rest of v2

- **Orchestrator** (Session 4) calls this service to persist turns at the end of each `run_turn` call (Deliverable 2).
- **Public-API** (Session 3) calls this service to serve the mobile inbox + history endpoints (Deliverable 2).
- **ETL** (Deliverable 3) ports chat-ai's conversations + messages into this service's tables.

## Constraints respected

- **A4** — ALL data MUST port: schema designed for the full ETL (no column drops)
- **A16** — same JSON shapes: `ConversationResponse` + `MessageResponse` models in public-api match this service's table columns
- **F3** — per-service schema on shared Patroni: `user_memory_role` owns `user_memory` schema
- **D8** — secrets manifest: `POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE`
- **G3** — pgBouncer in front of Patroni from day 1
