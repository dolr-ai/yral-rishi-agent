# Session 5 LOG — ETL + Tests + Memory
> Append-only diary. Most recent entries at TOP. Auto-appended by `.claude/hooks/post-tool-use.sh` on every git commit. Manual milestone entries welcome.

---

### 2026-05-25 — PR #147 round-7: B2 constant renames + 5 new migration behaviour tests

**Branch**: session-5/d3-etl-migration
**Trigger**: Codex round-6 returned:
  BLOCKER (B2): `_CONV_ID`, `_MSG_ID`, `_INF_ID`, `conv_resp`, `msg_ids`, `msgs1`,
  `asst_msg_id` in test_etl_transforms.py + test_conversation_routes.py.
  CONCERN: no tests for keyset pagination, COPY-to-staging, ON CONFLICT idempotency,
  message_count update, or CheckViolationError fallback.

**What ships**:

1. `tests/test_etl_transforms.py` — B2 constant renames:
   - `_CONV_ID` → `_CONVERSATION_ID`, `_MSG_ID` → `_MESSAGE_ID`, `_INF_ID` → `_INFLUENCER_ID`

2. `yral-rishi-agent-user-memory-service/tests/test_conversation_routes.py` — B2 renames:
   - `conv_resp` → `conversation_response` (13 occurrences; `conv_resp2` → `conversation_response_2`)
   - `msg_ids` → `message_ids`, `msgs1` → `messages_first_batch`
   - `asst_msg_id` → `assistant_message_id`
   - `batch_resp` → `batch_response`, `resp1` → `get_response_1`, `resp2` → `get_response_2`

3. `tests/test_etl_transforms.py` — 5 new migration behaviour tests (sections 7–11):
   - §7 `test_migrate_conversations_keyset_cursor_advances_to_last_row_of_batch`:
     batch_size=2, full batch → assert second fetch call's cursor args =
     (row_b.created_at, row_b.id); also asserts first call uses _ETL_EPOCH/_UUID_MIN
   - §8 `test_migrate_conversations_copies_correct_data_to_staging`:
     assert COPY target="conversations_staging", columns include id/user_id/
     conversation_type, user_id value appears in copied records
   - §9 `test_migrate_conversations_on_conflict_returns_zero_inserted`:
     INSERT SELECT returns "INSERT 0 0" → inserted total = 0 (idempotent re-run)
   - §10 `test_update_message_counts_dry_run_logs_affected_count`:
     fetchval returns 7 → log contains "7" + "DRY RUN"; no UPDATE execute() call
   - §11 `test_migrate_conversations_check_violation_falls_back_to_per_row`:
     bulk INSERT raises; first per-row INSERT raises (bad row); second succeeds →
     inserted=1, ≥2 WARNING logs (fallback notice + SKIPPING), fallback fires

   New mock infrastructure added to support these tests:
   - `_MockCheckViolationError`: real Exception subclass patched into
     sys.modules["asyncpg"].CheckViolationError before ETL module load
   - `_MigrationSourceConnection` + `_MigrationSourcePool`: records fetch_call_args
     for keyset cursor assertion
   - `_MigrationDestinationConnection` + `_MigrationDestinationPool`: captures
     copy_calls + execute_calls; configurable raise_on_bulk_insert +
     raise_on_first_per_row_insert for the fallback test

**Test results**: 24/24 pass (19 original + 5 new). B2 check: 0 hits.

---

### 2026-05-25 — PR #147 round-6: B2 rename sweep + CheckViolationError per-row fallback

**Branch**: session-5/d3-etl-migration
**Trigger**: Codex round-5 returned:
  BLOCKER (B2): dsn, src, dst, conn, batch_num, cursor_ts, conv, msg, resp, uid, asst
  in etl-scripts/chat_ai_to_user_memory_etl.py + tests + helper code.
  CONCERN (industry): etl-plan §7 says CheckViolationError rows are logged+skipped,
  but the script had no per-row exception handling — bad row aborted the whole batch.

**What ships**:

1. `etl-scripts/chat_ai_to_user_memory_etl.py` — comprehensive B2 rename:
   - `dsn` → positional arg (asyncpg's `create_pool` first param; removes `dsn=` keyword)
   - `src` / `dst` function params → `source` / `destination` (all migrate functions + main)
   - `open_dest_pool` → `open_destination_pool`
   - `conn` → `connection` in all internal variable names
   - `cursor_ts` → `cursor_timestamp`
   - `batch_num` → `batch_number`
   - `src_dsn` / `dst_dsn` → `source_database_connection_string` / `destination_database_connection_string`
   - `src_pool` / `dst_pool` → `source_pool` / `destination_pool`
   - `src_convs` / `dst_convs` / `src_msgs` / `dst_msgs` →
     `source_conversations` / `destination_conversations` / `source_messages` / `destination_messages`
   - `conv_delta` / `msg_delta` → `conversations_delta` / `messages_delta`
   - `conv_status` / `msg_status` → `conversations_status` / `messages_status`
   - `parsed` in `cli()`: `args` → `parsed` (argparse object; `args` was the B2 violation)
   - CheckViolationError fallback (see below)

2. `etl-scripts/chat_ai_to_user_memory_etl.py` — CheckViolationError per-row fallback:
   Implements plan §7 "log + skip" contract. Both migrate_conversations and
   migrate_messages now wrap the bulk INSERT SELECT in try/except asyncpg.CheckViolationError:
   - Fast path: INSERT SELECT succeeds → use bulk result count
   - Fallback: CheckViolationError fires → retry the batch row-by-row using rows_to_insert
     (staging table data intact — failed INSERT SELECT committed zero rows); individual
     per-row exceptions log the offending row ID + constraint name and skip it
   This matches the plan doc §7 "Script logs the offending row + skips it" exactly.

3. `tests/test_etl_transforms.py` — B2 rename:
   - `_MockConn` → `_MockConnection` (class + all references)
   - `self._conn` → `self._connection` in `_MockPool`
   - `src_pool` / `dst_pool` → `source_pool` / `destination_pool`
   - `_make_msg_row` → `_make_message_row`
   - `exc_info` → `exit_info` (in SystemExit assertions)
   - Log variable `message` in PII tests → `log_text` (to avoid shadowing `message` field names)

4. `yral-rishi-agent-user-memory-service/app/api/conversation_routes.py` — B2 rename:
   - All 19 `conn` variable usages → `connection`

5. `yral-rishi-agent-user-memory-service/tests/test_conversation_routes.py` — B2 rename:
   - `conv = conversations[0]` → `conversation = conversations[0]` (2 locations)
   - `lm = conv["last_message"]` → `last_message = conversation["last_message"]`
   - `lambda uid: uuid.UUID(uid)` → `lambda message_id: uuid.UUID(message_id)`
   - `conn` → `connection` in soft-delete fixture block
   - `resp = ` → `post_response = ` in cursor pagination loop
   - `{uid}` in docstrings → `{user_id}`

**Test results**: 19 ETL unit tests pass; B2 check: 0 hits across all 4 files.

---

### 2026-05-24 — PR #147 round-5: security fix + ETL keyset+COPY rewrite

**Branch**: session-5/d3-etl-migration
**Trigger**: Codex round-4 returned two CONCERNs:
  CONCERN 1 (SECURITY): `conversation_routes.py` — `before=` cursor subquery
  resolved any message id globally, enabling cross-conversation cursor manipulation.
  A caller could pass a message id from conversation B while querying conversation A,
  anchoring pagination at B's row coordinates (wrong page boundary + info leak).
  CONCERN 2 (perf): `chat_ai_to_user_memory_etl.py` — `migrate_messages` used
  LIMIT/OFFSET pagination (O(n²) on 3.3M rows) + per-row INSERT (no batching).

**What ships**:

1. `app/api/conversation_routes.py` — cross-tenant cursor isolation (security fix):
   - Pre-check `fetchval` validates `before=` cursor belongs to the queried conversation
     (`WHERE id = $1 AND conversation_id = $2`) before resolving its coordinates
   - Returns 404 (not 403 — consistent with tenant-isolation pattern) if cursor message
     not found in this conversation
   - Defense-in-depth: cursor subquery ALSO pins `AND conversation_id = $1` so even
     if the pre-check is somehow bypassed, the coordinates are from the correct conversation

2. `etl-scripts/chat_ai_to_user_memory_etl.py` — `migrate_messages` full rewrite:
   - Keyset pagination: `WHERE (created_at, id) > ($cursor_ts, $cursor_id)` eliminates
     the O(n²) OFFSET re-scan on 3.3M message rows
   - COPY-to-temp staging: `messages_staging` TEMP TABLE holds TEXT for JSONB columns
     (media_urls, gemini_metadata) — binary COPY protocol cannot encode Postgres JSONB
     directly; INSERT SELECT casts TEXT → JSONB atomically
   - Single dst connection held for entire phase (TEMP TABLE is session-scoped)
   - Idempotent: ON CONFLICT (id) DO NOTHING means a crash + restart re-runs from
     `_ETL_EPOCH` / `_UUID_MIN` and skips already-loaded rows
   - Progress log every 10 batches (every 100K rows at default batch size)
   - Docstring updated: WHAT/WHEN/WHY + JSONB TEXT bridge rationale

3. `yral-rishi-agent-user-memory-service/tests/test_conversation_routes.py` — new test:
   - `test_get_messages_before_cursor_from_different_conversation_returns_404`:
     creates two conversations A and B; inserts one message in each; queries A
     with the message id from B as the `before=` cursor; asserts 404
     Documents the cross-tenant cursor isolation contract (round-5 security fix)

**Key decisions**:
- `messages_staging` uses TEXT columns for media_urls + gemini_metadata because
  asyncpg's `copy_records_to_table` uses the binary COPY protocol which cannot
  encode Postgres JSONB natively — TEXT → JSONB cast in the INSERT SELECT is
  the correct bridge (same pattern as pg_dump restores)
- 404 (not 422) for cross-tenant cursor: the cursor UUID is syntactically valid,
  so 422 would be wrong. 404 matches the "not found in this conversation" semantics
  and avoids confirming that the message exists elsewhere (tenant isolation)
- `_ETL_EPOCH` / `_UUID_MIN` as keyset starting cursors: guaranteed to precede
  all real rows in both chat-ai DBs (no row predates 1970; nil UUID sorts first)

**Tests**: 19 ETL unit tests pass; user-memory-service route tests include new
  cross-tenant cursor test; B2 clean on all new/changed code

---

### 2026-05-24 — PR #147 round-4: B7 sweep on ETL script + ETL unit test suite

**Branch**: session-5/d3-etl-migration
**Trigger**: Codex round-3 returned two findings:
  BLOCKER (B7): `chat_ai_to_user_memory_etl.py` — imports lacked role comments,
  `cli()` lacked WHAT/WHEN/WHY block, non-trivial lines missing role comments.
  CONCERN (test): no tests for transform correctness, dry-run, verification failure,
  CLI mutual-exclusivity, or PII-safe logging.

**What ships**:

1. `etl-scripts/chat_ai_to_user_memory_etl.py` — B7 sweep:
   - Import block: grouped with one-line role comment per import
     (standard-library group + third-party group clearly separated)
   - `cli()`: expanded from one-liner to full WHAT/WHEN/WHY block explaining
     the sync/async boundary, argparse exit-code 2 contract, and testability rationale
   - RELATED FILES footer: DEP-014 relic → DEP-015

2. `tests/test_etl_transforms.py` — new, 19 tests:
   - Conversation transform: full column mapping, metadata drop, H2H nulls
   - Message transform: token_count→gemini_metadata, NULL content→'', paywall default,
     dropped columns absent, client_message_id preserved
   - JSONB serialization: None, dict, list, already-string passthrough
   - Verification failure: passes within tolerance, exits 1 on large conv delta,
     exits 1 on large message delta
   - CLI mutual-exclusivity: --conversations-only + --messages-only → exit 2
   - PII-safe logging: message content + metadata values never appear in log records
   - All 19 pass locally (Python 3.14.5, pytest 9.0.3)

**Key decisions**:
- `asyncpg` stubbed via `sys.modules["asyncpg"] = MagicMock()` before module load —
  pure-function tests don't need a real asyncpg install; consistent with user-memory-
  service's isolation of DB-calling code from transform logic
- `sys.path.insert(0, etl-scripts-dir)` + `import ... as etl` avoids the
  `importlib.util` import path (B2 lint would flag `util`); `chat_ai_to_user_memory_etl`
  is a valid Python module name so direct import works once the directory is on sys.path
- `_MockPool` / `_MockConn` minimal in-process stand-ins for `run_verification()` tests —
  no subprocess, no real DB, deterministic fetchval sequences
- `asyncio.run()` inside test functions instead of `@pytest.mark.asyncio` — avoids
  pytest-asyncio dependency at the repo level (not yet in any root-level pyproject.toml)

---

### 2026-05-24 — PR #140 → PR #144 → PR #146 → PR #146-closed + branch rename → PR #147 webhook-glitch recreation (audit trail per I11)

**Branch**: session-5/d3-etl-migration (clean branch from main — root cause fix)
**Trigger**: Coordinator confirmed zero workflow runs on PR #140, #144, #146 since creation.
All other branches firing normally (PR #137, #141, #142 with Codex+linters). Root cause:
`session-5/etl-plan-day-9-draft` branch had merge conflicts with main (D2 commits already
squash-merged as PR #132 / fffeadc). GitHub skips CI on PRs with CONFLICTING mergeability.

**What happened**:
- PR #140 opened 2026-05-23 — zero CI runs (mergeable: CONFLICTING)
- 3× no-op pushes + close+reopen — still zero checks
- PR #144, PR #146 created from same conflicting branch — still zero checks
- Diagnosis: `git log HEAD..origin/main` showed D2 commits (119dd7e, 967ceec, 2e76bfc)
  present in branch but already squash-merged to main as fffeadc (PR #132)

**Fix**: Created `session-5/d3-etl-migration` from `origin/main`, cherry-picked only
D3-specific commits (e22c2ed, 9190196), resolved merge conflicts cleanly.

**PRs closed**: #140, #144, #146 — all had webhook/merge-conflict issues

---

### 2026-05-23 — D3: ETL migration plan + script + RUNBOOK + DEP-015

**Branch**: session-5/etl-plan-day-9-draft
**Trigger**: PR #132 merged (fffeadc); coordinator green-lit D3 scope.

**What ships**:

1. `etl-scripts/etl-plan-day-9-draft.md` — full migration plan:
   - §2: column mapping conversations (8 source columns → 9 v2 columns)
   - §3: column mapping messages (14 source columns → 8 v2 columns)
   - §4: 3-phase algorithm (conversations → messages → message_count UPDATE)
   - §5: idempotency (ON CONFLICT (id) DO NOTHING — safe to re-run)
   - §6: post-migration verification queries (count comparison)
   - §7: failure modes + recovery table
   - §8: PII handling (content never logged, READ ONLY source connection)
   - §9: A14 approval checklist for Rishi YES (exact text for coordinator to surface)

2. `etl-scripts/chat_ai_to_user_memory_etl.py` — Python migration script:
   - asyncpg READ ONLY source pool + write destination pool
   - `transform_conversation_row()`: updated_at → last_message_at rename,
     metadata JSONB dropped (Phase 2), soft_deleted_at → NULL
   - `transform_message_row()`: token_count → gemini_metadata JSONB,
     content NULL → '', count_toward_paywall → True,
     sender_id/message_type/audio_url/is_read/status/metadata dropped
   - Batched SELECT + INSERT ON CONFLICT (id) DO NOTHING (10K rows/batch)
   - message_count UPDATE after all messages loaded (WHERE message_count = 0)
   - Verification: count comparison with ±500 conv / ±5K msg tolerance
   - CLI: --batch-size, --dry-run, --conversations-only, --messages-only

3. `RUNBOOK.md` — new "ETL Day-9" section with pre-requisites, run commands,
   verification output, and live-data-pulls-log.md entry format

4. `cross-session-dependencies.md` — DEP-014:
   coordinator must get Rishi YES (A14) before running the script

5. `tests/test_conversation_routes.py` — Codex follow-up test tightening:
   - Updated `test_messages_ordering_with_same_created_at_timestamp`:
     content-positional role assertions + id presence verification
   - New `test_before_cursor_within_same_timestamp_batch_returns_correct_subset`:
     cursor pagination handles same-timestamp batch correctly

Total tests: 8 schema + 25 route = **33 tests**.

**Key decisions**:
- `conversations.metadata` (memories) dropped — Phase 2 pgvector will rebuild from
  message history; no recovery needed for Phase 1 conversation persistence
- `messages.sender_id` dropped — H2H sender attribution not in v2 Phase 1 schema;
  documented as data loss in §3
- `token_count` → `gemini_metadata {"total_tokens": N}` — preserves billing-relevant
  data without adding a new column; consistent with v2's JSONB envelope pattern
- `count_toward_paywall = True` for all migrated messages — conservative fail-safe per E7;
  cannot retroactively know which historical messages were auto-greet exemptions
- A14 approval checklist embedded in §9 of the plan — coordinator uses exact text
  to surface to Rishi, no ambiguity about what's being approved

**Next**: Await D3 PR review. Day-9 ETL execution after Rishi YES (DEP-014).

---

### 2026-05-23 — PR #132 Round-3: GET /v1/conversations/{id} + migration 003 + Codex tests

**Branch**: session-5/user-memory-rpc-endpoints
**Trigger**: Coordinator relayed Codex round-2 CONCERN (test coverage gaps) + expanded
scope with ITEM 2 (GET /v1/conversations/{id} for Session 3 PR-B2 trust-boundary fix).

**Three items in one round-3 commit**:

1. `app/migrations/versions/003_add_dedup_indexes.py`:
   - `conversations_natural_key_active_unique_idx`: partial unique expression
     index using COALESCE(influencer_id, '') + COALESCE(participant_b_id, '')
     WHERE soft_deleted_at IS NULL — enables atomic upsert ON CONFLICT
   - `messages_client_message_id_dedup_idx`: partial unique on (conversation_id,
     client_message_id) WHERE NOT NULL — enables message idempotency

2. `app/api/conversation_routes.py` — 4 changes:
   - `create_or_get_conversation`: atomic `INSERT ... ON CONFLICT DO UPDATE RETURNING`
   - `append_messages`: `ON CONFLICT DO NOTHING` + SELECT existing for client_message_id dedup;
     only increments message_count for genuinely new rows
   - `list_messages` ORDER BY: added `id DESC/ASC` tiebreaker for same-timestamp determinism
   - NEW `GET /v1/conversations/{conversation_id}`: X-User-Id tenant isolation,
     404 for not-found/soft-deleted/wrong-user, returns ConversationResponse + last_message inline

3. Test additions (8 new tests + 1 fix):
   - Fixed `>=` → `>` strict timestamp assertion (Codex concern)
   - `test_create_or_get_handles_concurrent_first_calls` (asyncio.gather)
   - `test_append_message_idempotency_via_client_message_id`
   - `test_messages_ordering_with_same_created_at_timestamp`
   - 5× GET /v1/conversations/{id} tests (happy path, 404×3, null last_message)
   - `test_migration_003_unique_indexes_exist` in test_schema_migrations.py

**Total tests now**: 8 schema + 24 route = 32 tests.

**Key decisions**:
- ON CONFLICT expression inference: Postgres supports COALESCE(...) in ON CONFLICT
  target when a matching expression index exists — used for NULLable natural key columns
- Soft-delete test: uses `database_pool` fixture alongside `test_client` for direct SQL
  UPDATE; both fixtures truncate at setup time (no conflict) — clean dual-pool pattern
- message_count increments only for new rows (`is_new` flag per INSERT result) — retries
  don't double-charge the paywall counter
- GET /v1/conversations/{id} returns 404 (not 403) for wrong-user — standard tenant
  isolation practice; 403 would confirm conversation existence

**Next**: Awaiting Codex round-3 verdict + coordinator merge. D3 (ETL plan) after merge.

---

### 2026-05-22 — Deliverable 2: user-memory-service RPC endpoints

**Branch**: session-5/user-memory-rpc-endpoints
**Trigger**: PR #127 (D1) merged at 2026-05-22T12:21:22Z; coordinator green-lit D2.
Codex returned FAILURE on D1 PR (truncation false-positive — coordinator override per
documented escape hatch). DEP-012 confirmed canonical; Session 3's shadowed DEP-012
rerouted to DEP-013 by coordinator before push.

**What ships in this PR**:

1. `app/migrations/versions/002_add_message_fields.py`: adds two columns to messages
   that are required by the locked MessageResponse wire contract:
   - `client_message_id TEXT` (nullable) — F10 dedup ID mobile sends with user messages
   - `count_toward_paywall BOOLEAN NOT NULL DEFAULT TRUE` — E7 paywall counter

2. `app/api/__init__.py` — package marker

3. `app/api/models.py` — Pydantic models:
   - Request: ConversationCreateRequest, MessageCreateItem, AppendMessagesRequest
   - Response: MessageResponse + ConversationResponse — MIRROR public-api's
     response_models.py exactly (per A8 + A16); kept in sync by design

4. `app/api/conversation_routes.py` — 4 FastAPI RPC route handlers:
   - POST /v1/conversations — upsert by natural key (user_id, conversation_type,
     influencer_id, participant_b_id) WHERE soft_deleted_at IS NULL;
     IS NOT DISTINCT FROM handles NULL equality correctly
   - POST /v1/conversations/{id}/messages — atomic batch insert in a transaction;
     updates last_message_at + message_count; filters system role from response
   - GET /v1/conversations/by-user/{user_id} — LATERAL JOIN for last_message
     inline; one round-trip for the full inbox payload
   - GET /v1/conversations/{id}/messages — DESC-then-ASC subquery for "most
     recent N" semantics with chronological page ordering; before= cursor
     for load-older pagination

5. Updated `app/main.py`:
   - Mounts conversation_router via app.include_router(conversation_router)
   - Upgrades /health/ready from static stub → live Postgres ping (SELECT 1)
     so Swarm stops routing to replicas with a broken DB connection

6. Updated `tests/conftest.py`:
   - Adds `test_client` fixture: creates fresh asyncpg pool per test, injects into
     `app.database._pool` before lifespan fires (idempotent `init_pool()` check
     means no double-connect), yields httpx.AsyncClient with ASGITransport
   - Updates FIXTURE HIERARCHY comment to include test_client

7. Updated `tests/test_schema_migrations.py`:
   - `test_messages_table_has_correct_columns` now expects 9 columns (adds
     client_message_id + count_toward_paywall from migration 002)

8. New `tests/test_conversation_routes.py` — 13 tests covering:
   - POST /v1/conversations: response shape, upsert idempotency, H2H mode
   - POST .../messages: user+assistant response, system filter, 404 for missing conv,
     conversation stats update (last_message_at advances, last_message populated)
   - GET /v1/conversations/by-user: empty list, last_message inline, limit param
   - GET .../messages: chronological order, system filter, limit param (most-recent N),
     before= cursor (older history), 404 for missing conv
   - /health/ready: 200 with connected pool

**Key technical decisions**:
- DB `influencer_id` → wire `ai_influencer_id` mapping in `_row_to_conversation_response()`
  (single mapping point per convention)
- LATERAL JOIN in list_conversations_by_user: avoids N+1 reads for inbox load
- DESC-then-ASC subquery in list_messages: "most recent N in chronological order"
  semantic matches both orchestrator LLM context fetch and mobile scroll-up UX
- No auth on RPC routes: C3 Swarm overlay trust + E6 X-User-Id trust; external
  JWT validation lives in public-api
- IS NOT DISTINCT FROM for upsert NULL equality: correct NULL=NULL comparison
  without special-casing NULLable columns

**Next**: Deliverable 3 (ETL migration plan draft on session-5/etl-plan-day-9-draft).

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
