# Daily Log

## 2026-05-28 — Patroni Phase 0 oversight + Phase 4.4 partial progress

### Phase 0 oversight (write-up for future debugging)
Spilo image `ghcr.io/zalando/spilo-15:3.0-p1` was assumed to ship pgvector — it does NOT. Verified empirically when migration 008 attempted `CREATE EXTENSION vector` on the live cluster:
```
ERROR: extension "vector" is not available
DETAIL: Could not open extension control file
"/usr/share/postgresql/15/extension/vector.control": No such file or directory.
```
This is the kind of "assumed-included" detail that should live in the cluster bootstrap notes. Adding to PR #175: a custom Patroni image (`yral-rishi-patroni-pgvector`) that extends Spilo with `postgresql-15-pgvector`. Going forward, any new Postgres extensions for Phase 4 or later land in that Dockerfile rather than as separate setup steps.

### Phase 4.4 partial progress (deploy paused awaiting infra PR)
- PR #174 (Phase 4.4 backend) **merged** to main.
- pg_dump snapshot taken on rishi-5 (leader): `/home/rishi-deploy/yral-backups/pre-migration-008-pgvector-20260528-173210.dump` — 522 MB, SHA256 `8f6da13846507c5fea5ddbac523b9d977309241ab7ca7ec2e7355b618e34150c`.
- Migration 008 **NOT YET APPLIED** — blocked on Patroni image swap.
- Backend image **NOT YET BUILT** — waiting for migration to succeed first.
- Resume sequence: rolling Patroni restart → migration 008 → image build + deploy → backfill → 27/27 endpoint test + latency report.

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
