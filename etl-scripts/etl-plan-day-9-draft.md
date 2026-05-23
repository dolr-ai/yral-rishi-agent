# ETL Migration Plan — Day 9: chat-ai → user-memory-service
> Status: DRAFT — NOT YET APPROVED
> Author: Session 5
> A14 gate: **Coordinator MUST obtain explicit Rishi YES before executing the live data pull.**
> This document is the plan to submit for that approval. The Python script is at `chat_ai_to_user_memory_etl.py`.

---

## 0. Overview

Port chat-ai's conversation history into v2 `yral-rishi-agent-user-memory-service` so mobile
users see their existing conversations when they switch to the v2 app surface.

| Source | Destination | Rows (as of 2026-05-22 audit) |
|---|---|---|
| chat-ai Postgres (`conversations`) | v2 user-memory (`conversations`) | ~284,000 |
| chat-ai Postgres (`messages`) | v2 user-memory (`messages`) | ~3,300,000 |

Both tables share the same column schema in spirit; the mapping below documents every
column, every transform rule, and every drop decision.

---

## 1. Pre-conditions (coordinator must verify before Rishi YES)

1. `POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE` Swarm secret is live
   (`docker secret inspect POSTGRES_CONNECTION_STRING_USER_MEMORY_SERVICE` returns metadata).
2. `alembic upgrade head` has been run on the v2 user-memory DB — all 3 migrations applied
   (001 base schema, 002 message fields, 003 dedup indexes).
3. A **read-only** Postgres connection string for chat-ai's DB is available in Swarm secrets
   or passed as an env var to the ETL runner. Session 5 does NOT store this secret — 
   coordinator provisions it for the ETL run only.
4. The ETL runner machine has network access to both Postgres clusters
   (chat-ai on rishi-1/2/3, v2 on rishi-4/5/6 via pgBouncer).
5. Row count snapshot of chat-ai BEFORE the run (for post-ETL verification):

   ```sql
   -- Run on chat-ai Postgres BEFORE migration window:
   SELECT count(*) FROM conversations;   -- save this number
   SELECT count(*) FROM messages;        -- save this number
   ```

6. Maintenance window agreed with Rishi: no new conversations should be created in chat-ai
   DURING the ETL run (to keep pre/post counts clean). If a maintenance window is not
   possible, the +/- delta tolerance in verification (§6) applies.

---

## 2. Column mapping — `conversations`

| chat-ai column | v2 column | Transform rule |
|---|---|---|
| `id` UUID | `id` UUID | **Direct copy** |
| `user_id` TEXT | `user_id` TEXT | **Direct copy** |
| `influencer_id` TEXT (nullable) | `influencer_id` TEXT (nullable) | **Direct copy** — NULL preserved |
| `participant_b_id` TEXT (nullable) | `participant_b_id` TEXT (nullable) | **Direct copy** — NULL preserved |
| `conversation_type` TEXT | `conversation_type` TEXT | **Direct copy** — chat-ai values 'ai_chat'/'human_chat' are a subset of v2's check constraint ('ai_chat' | 'human_chat' | 'chat_as_human') |
| `created_at` TIMESTAMPTZ | `created_at` TIMESTAMPTZ | **Direct copy** |
| `updated_at` TIMESTAMPTZ | `last_message_at` TIMESTAMPTZ | **Rename** — chat-ai's `updated_at` is updated by trigger on each message INSERT; v2 equivalent is `last_message_at` |
| `metadata` JSONB (nullable) | *(dropped)* | **Drop** — v2 Phase 1 has no `metadata` column. The `memories` sub-key will be Phase 2 (pgvector semantic memory). Any non-null metadata is logged during ETL for future Phase 2 import. |
| *(not in chat-ai)* | `message_count` INT DEFAULT 0 | **Computed** — populated via `UPDATE conversations SET message_count = (SELECT count(*) FROM messages WHERE conversation_id = c.id) WHERE ...` after all messages are loaded |
| *(not in chat-ai)* | `soft_deleted_at` TIMESTAMPTZ DEFAULT NULL | **Default NULL** — chat-ai uses hard-delete; all migrated conversations are treated as active (not soft-deleted) |

**Data loss note**: The `metadata.memories` JSONB field (user fact memory extracted by the
AI) is NOT migrated in Phase 1. Those memories will be empty for migrated conversations until
Phase 2 (pgvector memory service) rebuilds them from the message history. This is intentional
and agreed per the Phase 1 scope definition.

---

## 3. Column mapping — `messages`

| chat-ai column | v2 column | Transform rule |
|---|---|---|
| `id` UUID | `id` UUID | **Direct copy** |
| `conversation_id` UUID FK | `conversation_id` UUID FK | **Direct copy** |
| `role` TEXT ('user'/'assistant') | `role` TEXT | **Direct copy** — chat-ai only uses 'user' and 'assistant'; v2 also supports 'system' but that's orchestrator-only |
| `content` TEXT | `content` TEXT | **Direct copy** — if NULL, map to empty string '' (NOT NULL constraint in v2) |
| `media_urls` JSONB (nullable) | `media_urls` JSONB (nullable) | **Direct copy** |
| `client_message_id` TEXT (nullable) | `client_message_id` TEXT (nullable) | **Direct copy** — dedup key preserved |
| `created_at` TIMESTAMPTZ | `created_at` TIMESTAMPTZ | **Direct copy** |
| `token_count` INT (nullable) | `gemini_metadata` JSONB (nullable) | **Transform**: if `token_count IS NOT NULL` then `gemini_metadata = '{"total_tokens": <token_count>}'`; else NULL. Captures billing-relevant data in the JSONB envelope without a new column. |
| `sender_id` TEXT (nullable) | *(dropped)* | **Drop** — v2 Phase 1 has no `sender_id` column. For H2H conversations, sender attribution is lost. Phase 2 can add this if H2H requires it. |
| `message_type` TEXT | *(dropped)* | **Drop** — v2 has no `message_type` column. Message type is inferred from `media_urls` presence by the client. |
| `audio_url` TEXT (nullable) | *(dropped)* | **Drop** — v2 has no `audio_url` column. Audio content is referenced via `media_urls` in v2. |
| `audio_duration_seconds` FLOAT (nullable) | *(dropped)* | **Drop** — v2 has no `audio_duration_seconds` column. |
| `is_read` BOOLEAN | *(dropped)* | **Drop** — v2 tracks read state differently (not per-message). |
| `status` TEXT | *(dropped)* | **Drop** — v2 has no `status` column. |
| `metadata` JSONB (nullable) | *(dropped)* | **Drop** — v2 has no `metadata` column on messages. |
| *(not in chat-ai)* | `count_toward_paywall` BOOLEAN DEFAULT TRUE | **Default TRUE** — conservative fail-safe per E7. All migrated messages count toward paywall. Cannot retroactively know which historical messages were auto-greet exemptions. |

**Total columns migrated**: id, conversation_id, role, content, media_urls, client_message_id,
created_at, gemini_metadata (transformed from token_count).

**Total columns dropped**: sender_id, message_type, audio_url, audio_duration_seconds, is_read,
status, metadata. Each drop is logged during the ETL run.

---

## 4. Migration algorithm

The Python script `chat_ai_to_user_memory_etl.py` implements the following:

```
Phase 1: Conversations
  FOR batch OF 10,000 rows FROM chat-ai.conversations ORDER BY created_at ASC:
    Transform each row per column map (§2)
    INSERT INTO v2.conversations (...) VALUES (...)
      ON CONFLICT (id) DO NOTHING   ← idempotent: re-runs are safe
    LOG: batch number, rows inserted, rows skipped (conflicts = already loaded)
    LOG: any row where metadata JSONB is not null (for Phase 2 recovery)

Phase 2: Messages
  FOR batch OF 10,000 rows FROM chat-ai.messages ORDER BY created_at ASC:
    Transform each row per column map (§3)
    INSERT INTO v2.messages (...) VALUES (...)
      ON CONFLICT (id) DO NOTHING   ← idempotent: re-runs are safe
    LOG: batch number, rows inserted, rows skipped

Phase 3: message_count update
  UPDATE v2.conversations c
  SET message_count = (
    SELECT count(*) FROM v2.messages
    WHERE conversation_id = c.id
  )
  WHERE message_count = 0
  ← Only updates rows where message_count wasn't already set by live traffic
```

**Why ORDER BY created_at ASC?** So that if the ETL is interrupted mid-run and restarted,
the `ON CONFLICT DO NOTHING` skips already-loaded rows and continues where it left off.
The ORDER BY is consistent across runs so partial-then-resume produces deterministic results.

**Why 10K batch size?** Balances memory pressure on the ETL runner (each batch fits in RAM)
against network round-trips (fewer trips = faster total time). Can be tuned via `--batch-size`
CLI flag.

---

## 5. Idempotency guarantee

Both INSERT statements use `ON CONFLICT (id) DO NOTHING`. This means:
- Running the ETL script twice produces the same result as running it once
- A crash mid-run + restart is safe: rows already in v2 are skipped
- Live traffic that creates new conversations in v2 during the ETL window is NOT disrupted
  (their IDs won't collide with chat-ai IDs because both use `gen_random_uuid()` independently
  for new rows; the only collision risk is if the same UUID was used in both — vanishingly rare
  with UUIDv4)

---

## 6. Post-migration verification queries

Run these on v2 user-memory-service AFTER the ETL completes:

```sql
-- Count verification (expect: close to pre-ETL chat-ai counts)
SELECT count(*) FROM conversations;  -- compare to pre-ETL snapshot
SELECT count(*) FROM messages;       -- compare to pre-ETL snapshot

-- Tolerance: +/- delta is expected if:
--   a) New conversations were created in chat-ai DURING the ETL window
--   b) New conversations were created in v2 by live app traffic during the window
-- Acceptable delta: < 500 conversations, < 5,000 messages (rough 0.2% threshold)

-- Spot-check: sample 5 conversations + their messages
SELECT id, user_id, influencer_id, conversation_type, last_message_at, message_count
FROM conversations
ORDER BY last_message_at DESC
LIMIT 5;

SELECT m.id, m.conversation_id, m.role, length(m.content) AS content_length, m.created_at
FROM messages m
JOIN conversations c ON m.conversation_id = c.id
ORDER BY m.created_at DESC
LIMIT 20;

-- message_count accuracy check (all conversations should have matching count)
SELECT count(*) FROM conversations
WHERE message_count != (
  SELECT count(*) FROM messages
  WHERE conversation_id = conversations.id
);
-- Expect: 0 rows (all counts correct)

-- Verify no NULL content was loaded as NULL (v2 has NOT NULL constraint)
SELECT count(*) FROM messages WHERE content IS NULL;
-- Expect: 0 rows
```

---

## 7. Failure modes and recovery

| Failure | Detection | Recovery |
|---|---|---|
| ETL script crashes mid-run | Log shows last batch number + last `created_at` cursor | Re-run script from beginning — `ON CONFLICT DO NOTHING` skips already-loaded rows |
| chat-ai Postgres read timeout | `asyncpg.exceptions.ConnectionDoesNotExistError` in logs | Reconnect and re-run; idempotent |
| v2 Postgres write timeout | `asyncpg.exceptions.ConnectionDoesNotExistError` in logs | Re-run; idempotent |
| CHECK constraint violation (bad `conversation_type`) | `asyncpg.exceptions.CheckViolationError` | Script logs the offending row + skips it; human reviews the skipped count post-run |
| post-ETL count mismatch > tolerance | Verification queries return delta > threshold | Re-run ETL for the conversations/messages table that missed rows; idempotent |

---

## 8. Data classification and PII handling

Chat-ai conversations contain:
- `user_id` (IC principal — pseudonymous identifier, NOT a real name)
- `content` (free-text chat messages — MAY contain PII if users typed personal info)
- `media_urls` (S3 keys — NOT URLs with personally identifiable tokens)

The ETL script:
- Does NOT log `content` values (only logs metadata: counts, IDs, timestamps)
- Does NOT write content to any file or stdout (content only flows Postgres → Postgres)
- Connection strings are passed via environment variables (NOT command-line args so they
  don't appear in `ps aux` output)
- The chat-ai Postgres read connection is opened in READ-ONLY transaction mode to prevent
  accidental writes

---

## 9. Approval gate (A14)

**This ETL MUST NOT run until Rishi types YES in the coordinator's chat.**

The coordinator must surface the following checklist to Rishi before execution:

```
ETL Day-9 checklist — please review + type YES to approve:

Source DB: chat-ai Postgres (read-only connection) 
  Tables: conversations (~284K rows), messages (~3.3M rows)

Destination DB: v2 user-memory Postgres
  Tables: conversations, messages

Column drops (data lost):
  conversations.metadata (memories — recoverable via Phase 2 pgvector rebuild)
  messages.sender_id (H2H sender attribution — not recoverable without new column)
  messages.message_type, audio_url, audio_duration_seconds, is_read, status, metadata

Data safety:
  - No deletes from chat-ai (read-only source connection)
  - Idempotent INSERTs (ON CONFLICT DO NOTHING) — safe to re-run
  - v2 conversations NOT disrupted during migration window

Post-ETL verification: count match within +/- 500 conversations / 5K messages

Type YES to approve execution:
```

See DEP-014 in `cross-session-dependencies.md` for the formal DEP tracking this approval.

---

## 10. Estimated duration

| Phase | Rows | Rate | Est. time |
|---|---|---|---|
| Conversations | ~284K | ~50K rows/min (Postgres→Postgres same datacenter) | ~6 minutes |
| Messages | ~3.3M | ~100K rows/min | ~33 minutes |
| message_count UPDATE | ~284K | bulk UPDATE | ~2 minutes |
| **Total** | | | **~45 minutes** |

These are estimates. Actual rate depends on network latency between the chat-ai and v2 clusters.

---

*Document: `etl-scripts/etl-plan-day-9-draft.md`*
*Last updated: 2026-05-23 by Session 5*
*Awaiting Rishi YES (A14) before execution.*
