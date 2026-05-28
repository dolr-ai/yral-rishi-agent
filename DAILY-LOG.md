# Daily Log

## 2026-05-28 (later) — Phase 4.6: user profile memory

### What changed
- `migrations/009_user_profile_memory.sql` — rebuilds the unique index on `user_memories` with `NULLS NOT DISTINCT` so two rows with `(user_id, NULL, key)` collapse into one. Postgres 15 feature; Spilo 15 supports it.
- `app/services/memory.py` — new `GLOBAL_CATEGORIES = {"identity"}`. Extraction now writes identity-category memories with `influencer_id=NULL` so the user's name / age / location / occupation / language apply across every bot they chat with.
- `app/repositories/memory_repo.py` — `upsert` type hint widened to `influencer_id: str | None`. Behavior already supported NULL at runtime (asyncpg coerces); this is documentation + clarity.
- `scripts/consolidate_identity_memories.py` (new) — one-off backfill that takes existing per-influencer identity rows, picks the most-recent value per `(user_id, key)`, upserts it as global, deletes the per-influencer copies. Idempotent.
- `tests/test_user_profile_memory.py` — guards that `identity` stays in GLOBAL_CATEGORIES and per-relationship buckets stay out.

### Why
Today the user tells influencer A "my name is Rahul" → memory stored as `(rahul, influencer-A, name='Rahul')`. Same convo with influencer B → another row. Across 200 bots a user actively chats with, that's 200 copies of the same fact, all eating prompt-token budget. With Phase 4.6, identity stays in one global row per `(user, key)` and gets unioned in by the existing `get_all_for_user` query — no retrieval changes needed.

### What's retrievable today vs after
- Today's `get_memories_for_prompt` already merges per-influencer + global via `WHERE (influencer_id = $1 OR influencer_id IS NULL)`. So even existing rows with influencer_id=NULL (if any) were already being read — the GAP was only on write.
- Phase 4.6 closes the write-side gap.

### Deploy steps (post-merge)
1. `pg_dump` snapshot (rule #9)
2. Apply migration 009 on the leader
3. Rebuild + deploy the agent image
4. Run consolidate script inside a container (idempotent — safe even if no rows match)
5. 27/27 endpoint suite — should stay green (no API changes)

## 2026-05-28 — Phase 4.4 shipped (semantic memory) + 2 Phase 0 lessons

### What landed
- **PR #174** — Phase 4.4 backend (pgvector schema + embedding service + memory_repo semantic_search + hot-path wiring + backfill script + diagnostic endpoint + tests)
- **PR #175** — Custom Patroni image `ghcr.io/dolr-ai/yral-rishi-patroni-pgvector:spilo-15-3.0-p1` (Spilo 3.0-p1 doesn't ship pgvector; this fix added the apt package)
- **PR #176** — Gemini embedding model fix (`text-embedding-004` was retired between PR #174 landing and rollout; switched to `gemini-embedding-001` with `outputDimensionality=768` Matryoshka truncation)
- **Swarm env update (no PR)** — `DATABASE_URL` repointed to multi-host with `target_session_attrs=read-write` via `docker service update --env-add` (no code change required since asyncpg 0.30 supports the libpq option natively). Agent now survives Patroni failovers without manual intervention. Verified via switchover round-trip rishi-4 → rishi-5 → rishi-4, writes succeeded both times. **Tech debt:** logged as Infra-Y — the env var lives only in swarm service spec, not in repo. Codify when we add `bootstrap/scripts/agent-stack.yml`.
- **Cluster:** all 3 Patroni nodes on the new pgvector image, TL=20 after the rolling restart + failover-test round-trip, all lag=0.
- **Backfill:** 8/8 user_memories embedded successfully.
- **Endpoint suite:** 27/27 PASS (including new `GET /api/v1/users/me/memories`).
- **Latency on `/messages`:** P50 4.58s, P95 8.08s (n=10). Up from ~2.5s pre-Phase-4.4 — embedding adds the lower bound (~150ms via asyncio.gather'd with history fetch), the rest is Gemini LLM variance per prompt. Will gather more data points after Phase 4.5/4.6 to separate signal from noise.

### Two Phase 0 lessons (worth a re-audit before production cutover)
1. **"assumed-included" — Spilo doesn't ship pgvector.** Caught at migration 008. Fixed via PR #175. The "Spilo bundles X" claim needs empirical verification per extension.
2. **"assumed-transparent" — pgbouncer's `DB_HOST: patroni-rishi-4` is hardcoded.** Caught after the rolling Patroni restart left rishi-6 as leader; pgbouncer kept routing to rishi-4 (a sync standby) → writes broke. Also: the agent's `DATABASE_URL` pinned `patroni-rishi-5` directly, bypassing pgbouncer entirely. PR #177 fixes the agent path with multi-host + `target_session_attrs`; pgbouncer's hardcoding still affects any future pooled-connection service (logged as Infra-X in PROGRESS.md backlog).

Net takeaway: cluster bootstrap notes should enumerate every "assumed-included" / "assumed-transparent" piece and verify each empirically before cutover. Candidates that haven't been re-audited yet: WAL-G restore drill, Redis Sentinel failover, Caddy cert renewal, Langfuse S3 retention.

### Sequence (for tomorrow's debugging)
1. `pg_dump` snapshot on rishi-5: `~/yral-backups/pre-migration-008-pgvector-20260528-173210.dump` (522 MB, SHA256 `8f6da138...`)
2. Built + pushed `yral-rishi-patroni-pgvector:spilo-15-3.0-p1` via CI workflow
3. Rolling restart rishi-6 → rishi-4 → rishi-5, verified pgvector available on each via direct SSH
4. Applied migration 008 on the (then) leader rishi-6 — extension/column/index all created
5. Built + deployed `yral-rishi-agent:phase-4-4` on rishi-4/5 (both replicas)
6. Hit Gemini 404 on embed → patched to `gemini-embedding-001`, rebuilt as `phase-4-4-fix1`, deployed
7. Backfill failed on read-only — DATABASE_URL pointed at the now-replica rishi-5
8. Patronictl switchover rishi-6 → rishi-4 (restored intended leader topology)
9. Swarm `docker service update --env-add DATABASE_URL=...?target_session_attrs=read-write` → backfill succeeded 8/8
10. Failover round-trip rishi-4 → rishi-5 → rishi-4 to prove `target_session_attrs` works under live failover

### Next
Phase 4.6 — user profile memory (name, city, job — permanent / cross-influencer). Continues under standing approval.

## 2026-05-26 — Phase 0 + Phase 1 Days 2-14 (all in one session)

### What completed
- **Phase 0**: Archived 17 v2 service folders, removed 7 worktrees, closed PRs #147 and #157, deleted 130 stale branches, created CLAUDE.md + GLOSSARY.md + README.md, created CI workflows
- **Day 2**: config.py + database.py + auth.py + main.py + health routes (4 endpoints)
- **Day 3**: models.py + influencer READ endpoints + migrations (3 endpoints)
- **Day 4**: conversation routes + chat_v2 bot-aware inbox (6 endpoints)
- **Day 5**: ai_client (Gemini + OpenRouter) + send-message — the HEART (1 endpoint)
- **Day 6**: influencer CREATE flow — generate prompt, validate, create, update, delete, admin ban/unban (8 endpoints)
- **Day 7**: media upload + image generation in conversations (2 endpoints)
- **Day 8**: human-to-human chat — create, list, send message (3 endpoints)
- **Day 9**: unified inbox v3 — AI + human chats in one list (1 endpoint)
- **Day 10**: billing paywall — RESOLVED. Billing is 100% client-side. Mobile app calls `billing.yral.com/google/chat-access/check` directly. No backend code needed.
- **Day 11**: WebSocket inbox — real-time events (1 WS + 1 docs endpoint)
- **Day 12**: ETL script written + deploy scripts + project/servers config
- **Day 13**: DEPLOYED TO CLUSTER
  - Created `yral_agent_db` database on Patroni leader (rishi-5)
  - Applied both migrations (001_initial.sql, 002_influencer_trending_stats.sql)
  - Built Docker image on rishi-4 and rishi-5
  - Deployed as Swarm service `yral-rishi-agent` with 2 replicas
  - Updated internal Caddy config to route `agent.rishi.yral.com` → `yral-rishi-agent:8000`
  - All health checks passing through internal Caddy
- **Day 14**: 24 unit tests across 4 files

### Verified endpoints (via internal Caddy on rishi-5)
```
curl agent.rishi.yral.com/        → {"service":"Yral Agent API","version":"2.0.0","status":"running"}
curl agent.rishi.yral.com/health  → {"status":"OK","database":"reachable"}
curl agent.rishi.yral.com/status  → {"service":"Yral Agent API",...,"database":"reachable","gemini_model":"gemini-2.5-flash"}
curl agent.rishi.yral.com/api/v1/influencers → {"influencers":[],"total":0,"limit":50,"offset":0}
```

### Public URL status
`curl https://agent.rishi.yral.com/health` returns 503 — the rishi-1/2 edge Caddy needs a config reload to recognize the updated upstream on the v2 cluster. Internal Caddy on rishi-4/5 works perfectly.

**To fix:** Reload or redeploy the Caddy snippet on rishi-1/2 that proxies to the v2 cluster. The v2 internal Caddy is correctly routing to `yral-rishi-agent:8000`.

### Cluster state
- Swarm service: `yral-rishi-agent` — 2/2 replicas on rishi-4 + rishi-5
- Database: `yral_agent_db` on Patroni (leader: rishi-5, replicas: rishi-4, rishi-6)
- Old microservices still running (can be removed after cutover)

### Endpoint count
29 HTTP endpoints + 1 WebSocket = 30 total. All accounted for per the plan.

### Line count
- app/ code: 3,984 lines
- chat-ai baseline: 6,780 lines
- Ratio: 59% of chat-ai — same functionality, no bloat

## 2026-05-26 — Phase 2.1: Langfuse Tracing

### What completed
- Added `langfuse==2.60.2` to requirements
- Created `app/services/langfuse_tracing.py` — Langfuse client wrapper with `trace_generation()` helper
- Integrated tracing into `ai_client.generate_response()` — every Gemini and OpenRouter call is now traced with provider, model, input/output tokens, latency, user_id, conversation_id
- Error traces logged at ERROR level so failed LLM calls are visible in Langfuse
- Langfuse flush on app shutdown
- Config: `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` env vars (no-op if not set)

### To activate
Set LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, and LANGFUSE_HOST env vars on the Swarm service. Can point to the self-hosted Langfuse on the cluster (once scaled up from 0 replicas) or Langfuse Cloud.

## 2026-05-26 — Phase 2.5: Request ID Tracing
- `app/middleware.py`: RequestIdMiddleware assigns UUID to every request, propagates to Sentry + response header

## 2026-05-26 — Phase 2.6: Redis WebSocket Pub/Sub
- Rewrote `app/services/websocket_manager.py` to publish all WS events via Redis pub/sub
- Background subscriber on each node delivers events to local WebSocket connections
- Falls back to local-only if Redis is not available (safe default)
- Added `redis==5.2.1` to requirements
- Subscriber started in main.py lifespan, cancelled on shutdown

## 2026-05-26 — Phase 2.2: LLM Client Abstraction
- Added `LlmResponse` frozen dataclass (content, provider, model, input_tokens, output_tokens, latency_ms, is_fallback)
- `generate_response()` returns `LlmResponse` instead of raw tuple
- Provider + model info preserved for observability

## 2026-05-26 — Phase 2.3: Soul File 4-Layer Composer
- `app/services/soul_file.py` — composes prompts from 4 layers:
  L1 (Global rules) → L2 (Archetype: companion/advisor/entertainer/educator/creator) → L3 (Per-influencer system_instructions) → L4 (Per-user memories)
- Deterministic output enables provider-side prompt caching
- Integrated into send-message flow

## 2026-05-26 — Phase 2.4: Enhanced Memory Extraction
- Structured categories: identity, preferences, goals, context, emotional
- Explicit-facts-only rule (no inferences from conversation)
- Concise values (under 50 chars)
- Correction-aware: user corrections override old memories

## PRs merged
- **#158** (Phase 0 + Phase 1): squash-merged to main
- **#159** (Codex review workflow): squash-merged to main
- **#160** (Phase 2.1 + 2.5 + 2.6): squash-merged to main

## Phase 2 status
| # | Feature | Status |
|---|---------|--------|
| 2.1 | Langfuse tracing | Merged (#160) |
| 2.2 | LLM client abstraction | In PR |
| 2.3 | Soul File 4-layer composer | In PR |
| 2.4 | Memory enhancement | In PR |
| 2.5 | Request ID tracing | Merged (#160) |
| 2.6 | Redis WebSocket pub/sub | Merged (#160) |
| 2.7 | Streaming responses (SSE) | Deferred — needs mobile coordination |

## 2026-05-26 — ETL: chat-ai → v2 data migration
- pg_dump from chat-ai DB on rishi-1 → load into yral_agent_db on rishi-5
- Influencers: 3,941 rows ✓
- Conversations: 284,763 rows ✓
- Messages: 3.3M rows (1.2GB dump, loading in progress)

## 2026-05-26 — Phase 3: Content Safety
- `app/services/content_safety.py` — three safety layers on every user message:
  1. Crisis detection: self-harm/suicide keywords → helpline response (India, US, intl)
  2. Prompt injection: regex patterns for jailbreak/DAN mode → blocked
  3. Adult content filter: NSFW keywords blocked for non-NSFW influencers
- Integrated into send-message flow: safety check before LLM call
- Crisis detection runs even for NSFW influencers (always)
- 14 new tests pass (8 content_safety + 6 soul_file)

## 2026-05-27 — Langfuse tracing fixed and operational
- Root cause: Redis auth (WRONGPASS) + Sentinel vs primary confusion + missing S3 creds
- Fix: pointed Langfuse at redis-primary:6379 with password, Hetzner S3 at fsn1.your-objectstorage.com
- Langfuse UI live at https://langfuse-agent.rishi.yral.com
- Traces flowing: status 207, all ingestion succeeding

## 2026-05-27 — Phase 4: Tiered User Memory
- `migrations/003_user_memories.sql` — user_memories table with category/key/value, per (user, influencer) pair
- `app/repositories/memory_repo.py` — upsert, get_for_user, get_all (influencer-specific + global)
- `app/services/memory.py` — extract_and_store() replaces old flat JSON approach, structured categories
- Send-message flow updated: reads from user_memories table, writes via background extraction
- pgvector not available on PG15 Spilo — designed for later upgrade (add embedding column)

## 2026-05-27 — Phase 5: Proactive Messages
- `migrations/004_proactive_messages.sql` — proactive_messages table (scheduling + delivery tracking)
- `app/services/proactive.py` — generate_proactive_message() uses influencer personality + user memories
- Trigger types: welcome_back (24h idle), follow_up, morning_greeting
- find_inactive_conversations() query for cron integration
- Delivery via existing push notification + WebSocket broadcast

## 2026-05-27 — Phase 6: First-Turn Nudge
- `app/services/nudge.py` — should_nudge() checks idle time + message count
- generate_nudge() creates personality-consistent follow-up for idle conversations
- Triggers: 5 min for 1-2 message convos, 10 min for 3-4 message convos
- Background task wired in main.py _engagement_loop() — runs every 15 min

## 2026-05-27 — Phase 7: Creator Studio
- `app/routes/creator.py` — 4 endpoints:
  - GET /creator/influencers — list creator's own bots with stats
  - GET /creator/influencers/{id}/analytics — conversation/user/message counts, 24h/7d active
  - GET /creator/influencers/{id}/conversations — Chat-as-Human view
  - GET /creator/influencers/{id}/soul-file — get editable system instructions

## 2026-05-27 — Phase 8: Creator Monetization
- `migrations/005_creator_earnings.sql` — creator_earnings table (amount, source, period, status)
- `app/routes/earnings.py` — 3 endpoints:
  - GET /creator/earnings — total summary (confirmed/pending/paid_out)
  - GET /creator/earnings/by-influencer — per-bot breakdown
  - GET /creator/earnings/history — paginated transaction history
- Ready for billing.yral.com webhook integration

## 2026-05-27 — Phase 9: Eval Harness
- `app/eval/gold_prompts.py` — 50 diverse prompts from real chat-ai conversations
  - Categories: companion, health, astrology, education, business, entertainment, fashion,
    family, romance, social, lifestyle, arts, food, technology, travel, beauty, gaming, fantasy
  - Includes Hinglish, Telugu, Hindi prompts for language mirror testing
  - Edge cases: minimal input, math, translation, "are you AI?" character break test
- `app/eval/runner.py` — eval harness that:
  1. Runs each prompt through generate_response()
  2. Scores response using Gemini-as-judge on 5 criteria (1-5 scale):
     in_character, helpful, concise, language_match, safe
  3. Posts scored traces to Langfuse for dashboard analysis
  4. Prints summary with per-criterion averages
- Run: `cd app && python -m eval.runner`

## 2026-05-28 — Phase 4.4: pgvector semantic memory
- `migrations/008_pgvector_semantic_memory.sql` — `CREATE EXTENSION vector`, `ALTER TABLE user_memories ADD COLUMN embedding vector(768)`, ivfflat cosine index. Spilo 3.0 image already ships pgvector so no Patroni rebuild.
- `app/services/embeddings.py` — Gemini `text-embedding-004` wrapper. `embed_text` (single), `embed_batch` (uses `:batchEmbedContents`). 768-dim. Failures return `None` non-fatally.
- `app/repositories/memory_repo.py` — added `update_embedding`, `list_missing_embedding`, `semantic_search` (cosine `<=>`); `upsert` now takes optional embedding; `_vector_literal` formats list[float] → pgvector text literal.
- `app/services/memory.py` — extraction now embeds inline (background, non-hot-path); `get_memories_for_prompt` accepts optional `query_embedding` for semantic top-K (8). Falls back to all-memories for proactive/short-message paths.
- `app/routes/chat.py` — hot path uses `asyncio.gather(history_fetch, embed_query)` to overlap ~150ms Gemini embed with ~10ms history DB fetch. Skips embedding for messages <5 chars.
- `app/routes/memories.py` (new) — `GET /api/v1/users/me/memories` diagnostic endpoint. Owner-only; lists global memories, optional `?influencer_id=` for per-bot view.
- `scripts/backfill_memory_embeddings.py` — idempotent batch backfill (50 rows/batch, `:batchEmbedContents`). Re-runnable. To be run during PR rollout per Rishi A11.
- `scripts/test_all_endpoints.py` — added `GET /users/me/memories` test → suite is now 27 tests.
- `tests/test_embeddings.py` — 3 unit tests: embed-text format stability, 768-dim constant guard, `_vector_literal` formatting.
- **Latency target:** +140-160ms on send-message hot path (Gemini embed dominates). Will measure exact P50/P95 after deploy.
- **PR:** opening as #174.
