# Session 5 STATE — ETL + Tests + Memory
> Updated: 2026-05-23 (D3 — ETL migration plan + script + RUNBOOK + DEP-014)

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 5. Phase 1 scope: conversation history persistence for
`yral-rishi-agent-user-memory-service`. NOT semantic memory / pgvector / RAG.

Three Phase-1 deliverables:
- D1 (DONE — PR #127 merged a39e54c): Schema + Alembic migration
- D2 (DONE — PR #132 merged fffeadc): 5 RPC endpoints (rounds 1-3)
- D3 (IN PR — this branch): ETL migration plan + script + DEP-014

## LAST THING I DID

D3 PR on branch `session-5/etl-plan-day-9-draft`:

**(1) `etl-scripts/etl-plan-day-9-draft.md`** — full ETL plan:
- §2 column mapping: conversations (chat-ai → v2), all 8 columns documented
- §3 column mapping: messages (chat-ai → v2), all 14 source columns documented
- §4 migration algorithm: 3 phases (conversations, messages, message_count UPDATE)
- §5 idempotency guarantee (ON CONFLICT (id) DO NOTHING)
- §6 post-migration verification queries
- §7 failure modes + recovery
- §8 data classification + PII handling (content never logged)
- §9 A14 approval checklist for Rishi YES

**(2) `etl-scripts/chat_ai_to_user_memory_etl.py`** — Python migration script:
- asyncpg-based, READ ONLY source pool, write destination pool
- `transform_conversation_row()` + `transform_message_row()` per §2 + §3
- `migrate_conversations()`: batch SELECT + INSERT ON CONFLICT DO NOTHING
- `migrate_messages()`: same pattern, 10K rows/batch
- `update_message_counts()`: bulk UPDATE after all messages loaded
- `run_verification()`: count comparison with +/-500/5K tolerance
- CLI: `--batch-size`, `--dry-run`, `--conversations-only`, `--messages-only`

**(3) `RUNBOOK.md`** — new "ETL Day-9" section:
- Pre-requisites checklist
- Step-by-step run commands
- Verification output description
- live-data-pulls-log.md entry format

**(4) `cross-session-dependencies.md`** — DEP-014:
- Coordinator must get Rishi YES (A14) before running the ETL script
- References §9 approval checklist in etl-plan-day-9-draft.md

**(5) `tests/test_conversation_routes.py`** — Codex follow-up tightening:
- Updated `test_messages_ordering_with_same_created_at_timestamp`:
  now asserts content-positional roles (both roles present in fixed order)
  + id presence verification — not just call-stability
- New `test_before_cursor_within_same_timestamp_batch_returns_correct_subset`:
  asserts cursor pagination navigates same-timestamp batches correctly;
  documents the created_at-only cursor limitation

Total tests: 8 schema + 25 route = **33 tests**.

## CURRENT TASK

D3 PR open on branch session-5/etl-plan-day-9-draft. Awaiting coordinator review.

## NEXT 3 PLANNED ACTIONS

1. D3 PR merges → D3 DONE
2. Wait for Day-9 (2026-05-31): coordinator surfaces A14 approval checklist to Rishi,
   runs ETL under YES, logs in live-data-pulls-log.md
3. Phase 2 (pgvector semantic memory): spawn user-memory-service Phase 2 branch with
   embedding extraction, semantic_facts table, user_profiles; deferred to after D3

## BLOCKERS

- Day-9 ETL execution BLOCKED until Rishi types YES per A14 (DEP-014)
- Phase 2 out of scope until D3 fully lands

## PENDING PRs (mine)

- D3 PR (opening now): ETL plan + script + RUNBOOK + DEP-014 + Codex test tightening
- PR #132 (MERGED): D2 — 5 RPC endpoints (rounds 1-3)
- PR #127 (MERGED): D1 — schema + Alembic migration

## CROSS-SESSION DEPS (mine)

- DEP-014 OPEN: coordinator needs Rishi YES (A14) to run chat_ai_to_user_memory_etl.py
- DEP-012 RESOLVED: Postgres provisioning complete
