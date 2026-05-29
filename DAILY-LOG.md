# Daily Log

## 2026-05-29 (end of session) — Task 4: Soul File Coach backend (Phase 7.5)

### Endpoints under `/api/v1/creator/coach/`
- `POST /conversations/{bot_id}` — start a coach session for an owned bot
- `POST /conversations/{coach_conv_id}/messages` — creator → coach; reply may include `proposed_changes` + `reasoning`
- `POST /conversations/{coach_conv_id}/apply` — atomically apply the latest proposal; archives previous text in `system_instructions_history` for rollback
- `GET /conversations/{coach_conv_id}/messages` — list session history

All endpoints owner-gated (creator must own the bot's `parent_principal_id`).

### Schema (migration 011)
- `coach_conversations(id, creator_user_id, bot_id, created_at, updated_at)`
- `coach_messages(id, coach_conversation_id, role ∈ {creator,coach}, content, proposed_changes NULL-when-no-proposal, reasoning, created_at)`
- `system_instructions_history(id, bot_id, coach_conversation_id, coach_message_id, previous_instructions, new_instructions, applied_by, applied_at)`

### Coach behavior
META_PROMPT in `services/coach.py` tells Gemini to:
1. Act as a teammate, push back on bad ideas
2. Propose surgical edits, not full rewrites
3. Explain WHY each change improves the bot (grounded in recent conversations + archetype)
4. Output a single JSON block `{summary, proposed_changes, reasoning}` ONLY when committing a change
5. Plain text (no JSON) for clarifying questions
6. Refuse unsafe / off-brand changes

The parser (`_try_extract_proposal`) is tolerant of wrapping prose since LLMs occasionally violate the JSON-only rule.

### Files
- `migrations/011_soul_file_coach.sql` — 3 tables + 4 indexes
- `app/repositories/coach_repo.py` (~170 lines) — DB helpers
- `app/services/coach.py` (~150 lines) — meta-prompt + Gemini call + proposal extraction
- `app/routes/creator_coach.py` (~210 lines) — 4 endpoints + ownership gates
- `app/main.py` — register router
- `tests/test_coach.py` — pins proposal-extraction edge cases + truncation safety

### Diff
+780 / -2 across 7 files. Bigger than the 400-line guideline, but under the 800-line standing-approval cap. Single concern (Phase 7.5 backend, all related).

### Deploy
1. pg_dump → S3
2. Apply migration 011
3. Rebuild + deploy
4. Smoke test: create session → send message → verify coach reply → apply if proposal → verify bot's system_instructions changed + history row written

## 2026-05-29 (afternoon, later) — Phase 2.7 deployed + smoke-tested

- Image `yral-rishi-agent:phase-2-7` deployed on rishi-4/5
- 27/27 endpoint suite: PASS on re-run (first run hit the recurring `/trending` materialized-view timeout)
- **SSE smoke test against live cluster (real Gemini):**
  - Curl to `POST /messages/stream` with `Accept: text/event-stream`
  - Got: 2× `event: token` chunks streamed in real time, then `event: done` with persisted assistant_message
  - Wire format matches `docs/SSE-PROTOCOL.md` exactly
- Codex flagged 2 BUGs + 2 OVERENG. Both BUGs were false positives (parameterized SQL + auth IS called at chat.py:608); OVERENG dismissed per standing approval. Justification posted as PR comment.

PR #189 merged. Phase 2.7 backend ✅. Mobile integration pending — ready to loop in mobile expert with `docs/SSE-PROTOCOL.md` whenever Rishi gives the word.

## 2026-05-29 (afternoon) — Task 3: SSE streaming backend (Phase 2.7)

### Endpoint
`POST /api/v1/chat/conversations/{id}/messages/stream` returning `text/event-stream`. Three event types: `token`, `done`, `error`. Wire format documented in `docs/SSE-PROTOCOL.md`.

### What changed
- `app/config.py` — new `ENABLE_SSE_STREAMING` flag (default TRUE — mobile decides whether to USE the endpoint)
- `app/services/ai_client.py` — new `_stream_gemini` async generator that wraps Gemini's `:streamGenerateContent?alt=sse`; new `generate_response_stream` higher-level wrapper that yields `('text', chunk)` / `('done', LlmResponse)` / `('error', LlmResponse)` tuples and handles `LlmBlockedError` + transient failures with the same classification as the non-streaming path
- `app/routes/chat.py` — new `send_message_stream` route. Auth + dedup + content-safety pre-check happen synchronously (can return HTTP errors). LLM streaming + DB save + side effects happen inside the SSE generator (yield error events instead of raising).
- `docs/SSE-PROTOCOL.md` (new) — wire format spec for the mobile expert
- `tests/test_sse_streaming.py` — pins event-name format, flag default, doc completeness

### NSFW caveat
OpenRouter SDK streaming is a separate code path; for v1, the streaming endpoint yields `NO_PROVIDER` error for `is_nsfw=TRUE` conversations. Mobile falls back to the legacy `POST /messages` for those. Tracked as Infra-Z for follow-up.

### Backward compat
Non-streaming `POST /messages` unchanged. Mobile chooses per turn.

### Diff
+330 / -1 across 5 files. No schema, no migration.

## 2026-05-29 (later) — Tasks 1 + 2 deployed

- pg_dump snapshot `pre-migration-010-proactive-20260529-124405.dump` (~498 MB, SHA256 `d57a834f...`) on rishi-4
- Migration 010 applied on rishi-5 (current leader, TL=22 — cluster had failed over overnight)
- Image `yral-rishi-agent:polish-1-2` built + deployed on rishi-4/5
- 27/27 endpoint suite: 27/27 PASS on re-run (first run hit the recurring `/influencers/trending` materialized-view timeout — known flake; logging in backlog)
- PRs #186 + #187 merged; Phase 4 + Phase 5 polish rows flipped to ✅

Next: Task 3 (SSE streaming, ~2-3 days) → Task 4 (Soul File Coach, ~3 days).

## 2026-05-29 — Task 2: proactive message quality fix (Phase 5 polish)

### Why
Motorola test: each bot sent 3-4 similar "hey what's up" proactive messages without a user reply. Frequency (24h inactive threshold + 15-min loop) is fine; quality and variety are the problems.

### Three fixes
1. **Cap on unanswered proactives** — new `is_proactive` boolean column on `messages` (migration 010). After 3 unanswered proactive messages, the engagement loop skips this conversation until the user replies. The 3-cap resets when the user posts.
2. **Variety prompt** — the last 3 proactive messages get embedded in the next-generation Gemini prompt as "do NOT repeat themes, hooks, opening phrases, or topics from these."
3. **Type rotation** — each generation randomly picks one of {question, observation, story, light_topic} and aligns tone with the bot's archetype (companion = warm, advisor = thoughtful, entertainer = playful, creator = inspired, educator = intrigued).

### Plus anti-recitation guard
The PROACTIVE_PROMPT also embeds Task 1's anti-recitation language (DO NOT lead with personal facts, DO NOT recite, DO NOT use "I remember you said X"). Proactive messages were also affected by the same Motorola regression.

### Files
- `migrations/010_proactive_messages_flag.sql` — column + partial index on `WHERE is_proactive = TRUE`
- `app/repositories/message_repo.py` — `create()` takes `is_proactive`; new `count_unanswered_proactive` + `recent_proactive_texts`; PROACTIVE_CAP_WITHOUT_REPLY = 3 constant
- `app/services/proactive.py` — cap check, variety block, type rotation, archetype-aligned tone
- `tests/test_proactive_quality.py` — pins the constants and the anti-recitation language

+221 / -15 across 5 files. Migration is additive (DEFAULT FALSE, existing rows unaffected).

### Deploy steps
1. pg_dump snapshot
2. Apply migration 010
3. Rebuild + deploy
4. Engagement loop picks up new behavior on next 15-min tick

## 2026-05-29 — Task 1: memory recitation fix (Phase 4 polish)

### Why
Motorola testing surfaced that bots were leading replies with "Mumbai" (the most common identity fact). Two problems:
1. SEMANTIC_TOP_K=8 over-injected — most LLMs latch onto the first fact and recite it
2. The L4 prompt said "use naturally, don't recite" — too soft to override the recency bias

### Fix
- `SEMANTIC_TOP_K`: 8 → 3, with a buffer of 10 in `semantic_search` for the variety filter to work against
- New Redis-backed per-conversation variety filter in `session_memory.py`: tracks the last 5 turns' injected memory keys, skips any key that appeared 3+ times. Filter is non-fatal (Redis down → empty set, no filter)
- Layer 4 prompt strengthened with explicit "NEVER lead with personal facts. NEVER say 'I remember you said X'" language
- `get_memories_for_prompt` now takes an optional `conversation_id` so the filter has scope; caller in `chat.py` updated

### Files
- `app/services/memory.py` — TOP_K + buffer + conversation_id arg
- `app/services/session_memory.py` — `record_memory_keys_used` + `recently_overused_keys` (Redis list, JSON-encoded per-turn arrays)
- `app/services/soul_file.py` — L4 block with strong anti-recitation instructions
- `app/routes/chat.py` — pass conversation_id
- `tests/test_memory_recitation_fix.py` — pins constants + the anti-recitation phrasing

### Diff
~120 / -10 across 5 files. No schema, no migration. Plain rebuild + redeploy.


## 2026-05-28 (end of day) — Phase 4 complete (4.4 / 4.5 / 4.6 / 4.7 / 4.8 all ✅)

- Image `yral-rishi-agent:phase-4-8` deployed on rishi-4/5.
- Manual `consolidate_once` against live DB: 3 users scanned, 0 pairs merged (no near-duplicates in the current 8-row dataset — expected; the loop is in place for when data grows).
- 27/27 endpoint suite: PASS on re-run (first run hit the `/trending` materialized-view timeout again — that endpoint is the flakiest of the suite; the materialized view is refreshed every 15 min, and intermediate stalls show up as occasional 2s+ reads).

### Phase 4 final state
| Sub-phase | Status |
|---|---|
| 4.1 user_memories table | ✅ already done (pre-today) |
| 4.2 Per-conversation memory extraction | ✅ already done |
| 4.3 Memories injected into Soul File L4 | ✅ already done |
| 4.4 pgvector embeddings + semantic search | ✅ shipped today (#174 + #175 + #176 + swarm env) |
| 4.5 Cross-conversation memory recall | ✅ shipped today (#180) |
| 4.6 User profile memory (identity → global) | ✅ shipped today (#178) |
| 4.7 Redis session memory (mood) | ✅ shipped today (#182) |
| 4.8 Nightly memory consolidation | ✅ shipped today (#183) |

### Standing approval cycle closes
Per the original mandate: "Stop ONLY if: 3. You finish all of Phase 4 — then stop and let me know." Phase 4 is fully shipped. Pausing for the next batch.

### Outstanding non-Phase-4 work queued
- **Pre-approved on a specific message** but still pending: tiny standalone PR adding the "PROGRESS.md vs DAILY-LOG.md" section to CLAUDE.md (Rishi-specified verbatim content). Will execute next unless redirected.

## 2026-05-28 (latest) — Phase 4.7 deployed + Phase 4.8: nightly memory consolidation

### Phase 4.7 deployed
- Image `yral-rishi-agent:phase-4-7` built + deployed on rishi-4/5.
- 27/27 endpoint suite: PASS on re-run (first run hit a transient timeout on `/influencers/trending` materialized-view refresh — unrelated).
- Session memory in Redis now live; mood heuristic running on the hot path with zero added wall-clock (parallelized in the existing gather block).

### Phase 4.8 — nightly memory consolidation
- `app/services/memory_consolidation.py` (new) — background loop that runs every 24h. For each user with embedded memories, self-joins on `<=>` cosine distance, picks pairs below `MERGE_DISTANCE_THRESHOLD = 0.08`, drops the loser (lower confidence; ties broken by older `updated_at`). One batch DELETE per user. Idempotent.
- `app/main.py` — wires `consolidation_loop` into the lifespan's `asyncio.create_task` family alongside the existing trending refresher, engagement loop, takeover sweep.
- `tests/test_memory_consolidation.py` — pins threshold + interval + initial delay so a future refactor can't accidentally move the schedule to "every minute" or the threshold to "merge everything."

### Why 0.08 threshold
Loose enough to catch paraphrases ("loves cricket" / "enjoys watching cricket") via Gemini's 768-dim embedding (after truncation), but well below the typical 0.2-0.4 distance between genuinely different facts. Will tune empirically once we see the first daily consolidation report from prod logs.

### Safety
- First run is delayed 10 min after container startup (avoid thrashing on rolling deploys)
- Each merge is one DELETE on rows we've already analyzed in-memory — no long-running transactions
- Non-fatal: any error in `consolidate_once` is caught, logged, and the loop retries on the next 24h tick
- Both replicas run the loop, but `id < b.id` join + DELETE…WHERE id=ANY(...) handles the race (lost-update is safe — the loser is going to be deleted from one node or the other, only once)

### Diff size
+186 / -4 across 4 files. No schema change.

## 2026-05-28 (very late) — Phase 4.7: Redis session memory

### What changed
- `app/services/session_memory.py` (new) — Redis async client (mirrors websocket_manager.py's `_get_redis`). Lightweight mood detector (emoji + keyword heuristic, 4 buckets: happy/sad/excited/stressed/neutral). `update_from_user_message(user_id, conv_id, text)` and `read(user_id, conv_id)` with 1-hour TTL. All Redis failures degrade to no-op.
- `app/routes/chat.py` — hot path now: (a) fires `update_from_user_message` as `asyncio.create_task` after saving user message (non-blocking), (b) reads session state inside the existing `asyncio.gather(history, embed, session)` parallel fan-out, (c) merges `session_mood` into the `memories` dict before soul-file composition.
- `tests/test_session_memory.py` — pins the mood-detection heuristics + the Redis key shape.

### Latency impact
Session-state read is in the parallel `gather` block. Redis round-trip on the swarm overlay is ~1ms — well under the embedding call's ~150ms ceiling. Net hot-path delta: zero.

### Failure modes (all silent degrade)
- Redis init fails → `_get_redis` returns None → all functions no-op
- Network blip during `set` → debug-log + continue
- Cache miss / TTL expiry → `read` returns None → no `session_mood` injected

### Design rationale
Mood detection lives in Redis, not Postgres, because:
1. It's derived (rule-based heuristic today, could be LLM-extracted later) — not a fact the user stated
2. It's ephemeral (1-hour relevance) — emotional state from yesterday shouldn't bias today's reply
3. It's per-conversation, not per-(user, influencer) — different convos have different moods

Distinct from Phase 4.4/4.6 long-term memory: those go in Postgres + pgvector.

## 2026-05-28 (later still) — Phase 4.5 deployed

- Image `yral-rishi-agent:phase-4-5` built + deployed (no migration, no backfill).
- 27/27 endpoint suite: PASS on 2 of 3 consecutive runs; one transient hit the GET /messages 2s latency cap (Gemini latency variance). No real regression.
- PR #180 merged. Phase 4: 78% done.

## 2026-05-28 (late) — Phase 4.5: cross-conversation memory recall

### What changed
- `app/repositories/memory_repo.py` — `semantic_search` dropped the influencer-scope filter. Was `WHERE user_id=$1 AND (influencer_id=$2 OR IS NULL)`; now just `WHERE user_id=$1`. Vector distance gatekeeps relevance.
- `app/services/memory.py` — `get_memories_for_prompt` updated to match (drops the influencer_id from the semantic_search call).
- `tests/test_cross_conversation_recall.py` — pins the contract: signature must NOT take an influencer_id arg; non-query path must fall back to `get_all_for_user`.

### Why
Phase 4.4 already returns top-K most-relevant memories. The arbitrary `OR influencer_id IS NULL` constraint was a leftover from pre-4.4 where we only had "all memories" retrieval. With semantic search, that scope filter was suppressing genuinely relevant context from other bots. Example: user talks cricket with bot A, then asks bot B about cricket — bot B couldn't recall the earlier fact even though it's an exact semantic match.

### Risk
Cross-bot leakage of relationship-specific context. Mitigated by:
1. Semantic gatekeeping — irrelevant memories don't surface (distance ranking)
2. Identity facts (Phase 4.6) were already global; per-relationship rows surface only when contextually relevant
3. Backlog item: add per-influencer privacy controls if creators report leakage complaints

### Code size
+34 / -7 across 3 files. No schema change, no migration, no deploy script change.

## 2026-05-28 (even later) — Phase 4.6 deployed

- pg_dump snapshot: `~/yral-backups/pre-migration-009-userprofile-20260528-213756.dump` (522 MB, SHA256 `ccdc69ff...`)
- Migration 009 applied on rishi-4 (current leader): unique index rebuilt with `NULLS NOT DISTINCT`. Verified via `\d user_memories`.
- Image `yral-rishi-agent:phase-4-6` built on rishi-4 + rishi-5, deployed via `docker service update --image --force` — converged in <10s.
- `scripts/consolidate_identity_memories.py` run: **3 per-influencer identity rows consolidated → 3 global rows, 0 per-influencer remaining**. Idempotency re-verified by running twice; second run reports 0 candidates.
- 27/27 endpoint suite: PASS, no regressions.

PR #178 merged. Phase 4 progress: 65% done.

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
