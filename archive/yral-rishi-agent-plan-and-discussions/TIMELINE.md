# Implementation Timeline — Agentic Chat V2 (REVISED 2026-04-24)

**Author:** Claude · **Reviewing:** Rishi · **Status:** DRAFT for Rishi's thorough review · **Not locked until Rishi explicitly approves**

## Sequencing principle (Rishi 2026-04-24)

**Template first → Hello-world from template → Feature-parity services → 1000× services**

Every service is spawned from the v2 template. Building the template well is upstream of everything. Hello-world proves the template works before we invest in real service code. Feature-parity services come next, tested locally against a full port of production data. Only after Rishi is satisfied with parity do we layer in the 1000× improvements.

## Data port rule (updated 2026-04-24)

When testing locally: port ALL data from the current live chat-ai to the local v2 Postgres. Includes AI influencers, conversations, messages, read states, user profiles — everything. Rishi wants realistic fidelity during local testing, not empty tables.

## Guardrails

- Plan-only until Rishi explicitly approves this plan
- Local implementation authorized (laptop backend + mobile local changes) per A13
- **V2 MUST be ≥50% faster than Python yral-chat-ai on user-interactive endpoints (E1, HARD, 2026-04-24 upgrade)** — every architectural decision measured against this
- **ALL data MUST port — AI influencers AND full user chat history (A4+A13 reversed 2026-04-24)** — applies to local testing AND cutover
- **Claude pushes back on non-standard or likely-wrong decisions (I6, 2026-04-24)** — technical/architectural/product/UX; state concern + alternative + tradeoff + let Rishi decide
- **Pulling live chat-ai DB data requires explicit per-operation Rishi YES (A14, 2026-04-24)** — Sentry aggregated reads pre-authorized; DB reads not
- **Motorola testing via real cluster from Day 8+ (A9+A15, refined 2026-04-24 evening)** — Cloudflare DNS → rishi-1/2 Caddy → rishi-4/5 Swarm. Debug APK has `CHAT_BASE_URL = "agent.rishi.yral.com"` per A16. NO Cloudflare Tunnel. NOT Tier-0 browser as primary loop
- Every mobile change = ONE change at a time, documented, never pushed (A12)
- Every feature in chat-ai must survive (A8 — can't cut without good reason + explicit approval)
- Naming: explicit English everywhere (B1, B5)
- No hardcoded IPs in code; secrets via manifest (C6, D7)
- Sentry = `sentry.rishi.yral.com` (A7, C4)

---

## Phase 0 — Template + Cluster (Days 1-8) — REFINED 2026-04-24 evening

**Goal:** by end of Day 8, Motorola debug APK sends a real chat message to `agent.rishi.yral.com`, which routes through rishi-1/2 Caddy → rishi-4/5 Swarm cluster → v2 public-api hello-world, returns a response. Real production-shape routing from Day 8 onwards.

**Structural shift:** earlier plan had everything local Days 1-5. Refined plan (per Rishi 2026-04-24 evening): laptop for template dev Days 1-3, then rishi-4/5/6 cluster + rishi-1/2 Caddy routing Days 4-7, Motorola hits real cluster Day 8+.

### Day 0.5 — Sentry baseline pull script (BEFORE template)
Per `project_v2_first_build_task_sentry_baseline_pull.md`:
- Read Sentry API key from `~/.config/dolr-ai/sentry-api-key` (Rishi confirms exact path)
- Write `latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/pull-sentry-baseline.py`
- Pulls yral-chat-ai p50/p95/p99 + error counts per user-interactive endpoint
- Appends to `daily-baseline.csv`, writes `latest-baseline.md`
- Install launchd job (runs 9am local daily)
- Today's number becomes the "what v2 must beat" anchor

### Day 1 — Local template skeleton
Local folder: `~/Claude Projects/yral-rishi-agent/yral-rishi-agent-new-service-template/` (inside the monorepo; no new GitHub repo per F16).

Fork structure from existing `yral-rishi-hetzner-infra-template` (untouched per no-delete). Evolve:

- `pyproject.toml` — Python 3.12, FastAPI, asyncio, asyncpg, redis-py, httpx, pydantic, alembic, pytest
- `Dockerfile` — Python 3.12-slim, non-root user, multi-stage build
- `docker-compose.yml` — service + Postgres + Redis + pgBouncer + Langfuse (local dev convenience)
- `docker-compose.swarm.yml` — Swarm stack variant (used from Day 8)
- `project.config` — per-service single source of truth (name, domain, ports, replica tier)
- `shared-config.yaml` — cross-service shared values (URLs, hostnames, feature-flag defaults)

Sibling at monorepo root (per F7): `bootstrap-scripts-for-the-v2-docker-swarm-cluster/`
  - `cluster.hosts.yaml` (shape only — values from GitHub Secrets per C6)
  - `secrets-manifest.yaml` (declarative per D7)
  - `scripts/` (render-cluster-config.py, generate-ssh-config.sh, apply-node-labels.sh, node-bootstrap.sh)
  - `systemd/yral-v2-swarm-resync.service` (reboot resilience per H1)

**Day 1 exit criterion:** laptop `docker compose up` starts the template stack, `curl localhost:8000/health/ready` returns 200. Sentry baseline script is running daily. NO Motorola testing yet.

### Day 2 — Template app-layer scaffolding
Every file a service spawned from template inherits:

- `app/main.py` — FastAPI app, lifespan hooks, graceful shutdown
- `app/health.py` — three-tier `/health/live` `/health/ready` `/health/deep`
- `app/database.py` — asyncpg pool via pgBouncer
- `app/redis_client.py` — Sentinel-aware client (production mode); simple Redis for local dev
- `app/auth.py` — JWT middleware w/ JWKS + dual-validate flag default OFF per E9
- `app/llm_client.py` — LLM abstraction; dual routing per A10 (per-id Tara → OpenRouter; `is_nsfw` → OpenRouter; archetype defaults → Gemini + Claude for crisis)
- `app/sentry_middleware.py` — DSN from env, `service=<name>` tag per D3
- `app/langfuse_middleware.py` — auto-trace LLM calls
- `app/event_stream.py` — Redis Streams emit + consumer-group helpers
- `app/feature_flags.py` — Postgres-table-based flags, 30s polling per F11
- `app/idempotency_middleware.py` — default-on for non-GET, Redis 24hr dedup per F10
- `app/pii_redaction.py` — structured logger with allowlist per H6
- `app/prompt_injection_defense.py` — pre-orchestrator classifier per H5

**Day 2 exit criterion:** full `app/` scaffolding in place; unit tests green; `docker compose up` still works.

### Day 3 — Template CI/CD + scripts + docs + hello-world spawn
- `scripts/new-service.sh` — 1-command spawn that creates a NEW SUBFOLDER in the monorepo (per F16), not a new repo
- `scripts/generate-deploy-env-block.py` — reads secrets-manifest, emits env block for CI
- `scripts/validate-secrets-for-this-service.sh` — CI gate per D7
- `.github/workflows/deploy.yml.template` — canary deploy pattern (rishi-4 → rishi-5 → rishi-6 with auto-rollback); ONLY activates from Day 8+
- `.github/workflows/lint-naming.yml` — CI enforcing B5 + C6
- `.github/workflows/eval-diff.yml` — Langfuse eval on LLM-touching PRs per F14 + H8
- 5 required docs per F8 (DEEP-DIVE / READING-ORDER / CLAUDE / RUNBOOK / SECURITY)
- Spawn throwaway `yral-rishi-agent-hello-world` via `scripts/new-service.sh`; verify it boots on laptop, has all middleware wired, emits Sentry + Langfuse, passes health checks

**Day 3 exit criterion:** template proven via hello-world on laptop end-to-end. If any check fails, fix the TEMPLATE, re-spawn hello-world, repeat. Fold-learnings-back-into-template principle (per I4).

🤳 **Rishi-test checkpoint #0A:** Rishi sees `docker compose up` on his laptop runs the hello-world through template; structured logs look right; Sentry + Langfuse tagged events land. No Motorola yet.

### Days 4-6 — rishi-4/5/6 cluster provisioning
🚨 **Requires explicit Rishi "provision cluster" go-ahead** — this is a build-mode lift beyond Day-3 local work. CONSTRAINTS A13 authorizes the intent; the actual SSH-and-bootstrap execution gets a go/no-go at Day 3 end.

Sequenced work (detailed node-by-node in `bootstrap-scripts-for-the-v2-docker-swarm-cluster/`):

- Day 4 morning — Saikat grants time-limited root on rishi-4/5/6 per allocation. `rishi-deploy` user + narrow sudoers configured (matches legacy convention).
- Day 4 afternoon — Node bootstrap: Docker Engine + Swarm init on rishi-4 (manager), rishi-5 and rishi-6 join as managers, UFW rules (only :443 inbound per C3), three encrypted overlays (`yral-v2-public-web`, `yral-v2-internal`, `yral-v2-data-plane`).
- Day 5 — Patroni HA Postgres across rishi-4/5/6 (sync commit on ≥1 replica per F3), pgBouncer in front per G3, WAL-G continuous archive to Hetzner S3 per D2 L2, schema-per-service bootstrap script (per F3).
- Day 5 — Redis Sentinel per C11 (primary rishi-4, replica rishi-5, sentinels across 4/5/6).
- Day 6 — Langfuse self-hosted on rishi-6 per D4. Beszel agents on rishi-4/5/6. Uptime Kuma monitors registered via API per D5.
- Day 6 — Caddy as Swarm service (rishi-4/5) receiving TLS from rishi-1/2 per C10 (no ACME on v2 cluster at this phase). `tls internal` for internal overlay; public cert terminated on rishi-1/2.
- Day 6 afternoon — Chaos tests per H3 exit criteria: kill rishi-6 drain, kill rishi-4 Patroni container, fill rishi-5 disk 80%, partition rishi-6 from 4/5 for 10 minutes. ALL must pass.

**Day 6 exit criterion:** cluster is running, no v2 services deployed yet, chaos tests green, HA verified.

### Day 7 — rishi-1/2 Caddy snippet + first cluster deploy
🚨 **Requires explicit Rishi "go" on rishi-1/2 Caddy change** — per A2 exception, authorized but worth typing YES once more before it lands.

- Write v2-routing snippet for `yral-rishi-hetzner-infra-template` repo:
  - `caddy/conf.d/agent.rishi.yral.com.caddy` — reverse-proxies to `https://rishi-4:443 https://rishi-5:443` with health checks, round-robin, fail-over
  - Follows existing snippet pattern (e.g., `chat-ai.rishi.yral.com.caddy`)
  - SHA-rotating config names per H2
- PR against `yral-rishi-hetzner-infra-template`, CI green, review, merge.
- Deploy via existing template pipeline: snippet lands on rishi-1/2, `docker exec caddy caddy reload --force`.
- DNS: `agent.rishi.yral.com` already covered by wildcard `*.rishi.yral.com` → rishi-1, rishi-2.
- Deploy `yral-rishi-agent-hello-world` stack to v2 Swarm via GitHub Actions canary workflow (rishi-4 → health check → rishi-5 → health check → rishi-6 per I2).

**Day 7 exit criterion:** `curl https://agent.rishi.yral.com/health/ready` from Rishi's laptop returns 200 served from the cluster.

🤳 **Rishi-test checkpoint #0B:** Rishi curls `agent.rishi.yral.com/health/ready` from his laptop and phone browser. Both return 200. The full Cloudflare DNS → rishi-1/2 Caddy → rishi-4/5 Swarm → hello-world pipe is live.

### Day 8 — Motorola first test against real cluster
- Build first debug APK: `cd ~/Claude Projects/yral-mobile; ./gradlew assembleDebug`. Only local change: `AppConfigurations.kt` → `CHAT_BASE_URL = "agent.rishi.yral.com"`.
- Document change #0 in `mobile-client-change-log.md` (what, why, what might break, test evidence per A12).
- Rishi `adb install -r app/build/outputs/apk/debug/*.apk` on Motorola.
- APK talks to hello-world endpoint (synthetic shape matching the first chat-ai endpoint we'll replace).

🤳 **Rishi-test checkpoint #0C (THE MILESTONE):** Rishi's Motorola debug APK sends a request to `agent.rishi.yral.com`, it lands on rishi-4 or rishi-5 (we can see in Langfuse + Sentry), response comes back, UI renders. The full production-shape pipe is proven on real hardware.

If anything fails Day 7 or Day 8, we don't move forward until green. Phase 1 begins only after Checkpoint #0C is solid.

> **Note 2026-04-27 (Codex audit):** earlier drafts of this TIMELINE had a separate Day-2 app-scaffolding block + Day-3 hello-world checklist that appeared AFTER the Phase-0 boundary, creating structural drift. Both are now folded into Day 2 and Day 3 of Phase 0 above. The detailed app-layer files (`app/main.py`, `app/auth.py`, `app/llm_client.py` etc.) are the contents of Day 2 in Phase 0; the hello-world spawn + verification checklist is Day 3 in Phase 0. No separate "Day 2" block exists outside Phase 0.

---

## Phase 1 — Feature Parity Services (Days 9-25, re-numbered 2026-04-24 evening)

**Goal:** every feature in chat-ai works in v2, tested by Rishi on his Motorola against the REAL rishi-4/5/6 cluster via `agent.rishi.yral.com`, with full production data ported. ZERO mobile code changes beyond `CHAT_BASE_URL` (per A16).

> **Day-number note (2026-04-24):** the sub-day numbers in this section pre-date the Phase-0-expansion to Days 1-8. Treat each sub-day label as approximate and add ~3 days to pre-refactor numbers. I'll re-number precisely when Rishi wants a clean pass. The SEQUENCE is still correct; only the calendar days shift.
>
> **No mobile code changes in Phase 1** — per A16, the debug APK's single local edit (CHAT_BASE_URL in AppConfigurations.kt) is change #0, documented in `mobile-client-change-log.md` but not counted as a real mobile change. Firebase Remote Config overrides, SSE parsing, presence heartbeat, etc. are Phase 3+ work.

### Day 9 — Data port ETL (all of chat-ai → v2 cluster Postgres)

🚨 **Per CONSTRAINTS A14, every read from live chat-ai DB requires explicit Rishi YES.** Before Day 6, I submit the exact pull plan (tables, row counts, destination paths, retention plan, PII-handling) and Rishi types YES. No pull runs silently.

Build a one-time ETL script that:
- Connects to the current live chat-ai Postgres (read-only, via tunnel if needed — never writes)
- Dumps `ai_influencers`, `conversations`, `messages`, `users`, `read_states`, and any other live tables identified in the feature-parity audit
- Transforms minimally into v2 schema (v2 schemas are explicit-English e.g., `agent_influencer_directory.ai_influencers`, `agent_conversation.messages`) — greenfield schema, whatever's best for the 50%-faster goal (E1)
- Loads into local v2 Postgres
- Preserves all IDs (so mobile deep-links still work in testing)
- Every row in chat-ai must land somewhere in v2 — no row silently dropped (per A4 reversed; chat history MUST port)
- Snapshot stored at `~/Claude Projects/yral-rishi-agent/.local-snapshots/chat-ai-<timestamp>.sql`; path git-ignored; retention 7 days by default
- Re-runnable ETL script is CODE (in monorepo); the SNAPSHOT itself stays out of git

Verification:
- Count of influencers in local v2 == count in prod
- Count of conversations == count in prod
- Count of messages == count in prod
- Spot-check: 5 random influencers have identical data to prod

Rishi-visible: local Postgres now mirrors production state.

### Day 10 — Spawn `yral-rishi-agent-public-api` from template
> Old "Days 7-8" label was pre-Phase-0-expansion; corrected 2026-04-27 (Codex audit). Phase 0 ends Day 8; Phase 1 starts Day 9 with ETL above; Day 10 spawns public-api.

Single HTTP entry point. Implements endpoints in phased order below (Days 11-18). For now, skeleton: just health + JWT auth middleware.

### Days 11-12 — Core chat endpoints (Phase 1.A)
Implement against the ported data:

- `POST /api/v1/chat/conversations` — create conversation (or get existing) with an influencer
- `POST /api/v1/chat/conversations/{id}/messages` — send message, call Gemini (or per-id/is-nsfw routing), persist, return JSON (no streaming yet — parity mode)
- `GET /api/v1/chat/conversations/{id}/messages` — history with pagination
- `POST /api/v1/chat/conversations/{id}/read` — read receipt
- `DELETE /api/v1/chat/conversations/{id}` — delete conversation

Idempotency via `client_message_id` preserved. Presigned S3 URLs in responses. Image history window (3 messages) for LLM calls.

### Day 11 — Spawn `yral-rishi-agent-influencer-and-profile-directory`
- `GET /api/v1/influencers` (with Cache-Control 300s)
- `GET /api/v1/influencers/trending`
- `GET /api/v1/influencers/{id}`
- `POST /api/v1/influencers/generate-prompt`
- `POST /api/v1/influencers/validate-and-generate-metadata`
- `POST /api/v1/influencers/create`
- `PATCH /api/v1/influencers/{id}/system-prompt` (guardrails append-on-save / strip-on-display)
- `POST /api/v1/influencers/{id}/generate-video-prompt`
- `DELETE /api/v1/influencers/{id}` (soft-delete: `is_active='discontinued'`)
- `POST /api/v1/admin/influencers/{id}` + `/unban` (X-Admin-Key)

### Days 12 — Inbox endpoints (Phase 1.B)
- `GET /api/v1/chat/conversations` (v1 inbox)
- `GET /api/v2/chat/conversations` (v2 bot-aware inbox — this is what mobile actually uses)
- `GET /api/v3/chat/conversations` (v3 unified inbox — lower priority, confirm if mobile uses)

### Day 15 — Motorola check against ported data + parity endpoints
> Old "Day 13 — Firebase Remote Config flag" was REMOVED per A16 (mobile changes deferred to Phase 3+). Old "laptop-ip" testing block was REMOVED per A15 (real cluster from Day 8+, no laptop testing).

Build debug APK with `CHAT_BASE_URL = "agent.rishi.yral.com"` (single local edit in `AppConfigurations.kt` per A12 + A15). Install on Motorola via `adb install -r`. Verify:
- App opens, hits real cluster via Cloudflare → rishi-1/2 Caddy → rishi-4/5 Swarm → public-api
- Influencer list loads with REAL production data (ported via Day 9 ETL)
- Conversation history loads (full chat history ported per A4)
- Send a message — gets response via v2 (no streaming yet — parity mode)

🤳 **Rishi-test checkpoint #1:** Rishi's Motorola hits real v2 cluster, sends a message, gets an LLM response that looks identical (or better) to chat-ai. Influencer list + history all there.

### Days 14-15 — Media + image generation + audio
- Spawn `yral-rishi-agent-media-generation-and-vault`
- `POST /api/v1/media/upload` (S3 presigned URLs, Hetzner bucket)
- `POST /api/v1/chat/conversations/{id}/images` (Replicate FLUX)
- Audio transcription via Gemini when `message_type="audio"`

🤳 **Rishi-test checkpoint #2:** send audio message, generate image, upload photo. All work end-to-end on Motorola.

### Day 16 — Human chat + Chat-as-Human
- `POST /api/v1/chat/human/conversations` + list + send-message endpoints
- Bidirectional unique constraint preserved
- Chat-as-Human preserved via ICP CallerType resolution

🤳 **Rishi-test checkpoint #3:** H2H chat works (if mobile UI exposes it).

### Days 17-18 — Billing integration
- Spawn `yral-rishi-agent-payments-and-creator-earnings` (minimal for parity)
- Orchestrator calls yral-billing's `/google/chat-access/check` before each turn
- 60s Redis cache per (user, influencer)
- 50-message paywall flow preserved (this is enforced in mobile, but v2 supports it by serving chat access data)
- `TARA_SUBSCRIPTION` + `DAILY_CHAT` product distinction preserved
- For local testing: mock billing client (returns `hasAccess=true` for Rishi's test account); real yral-billing integration tested in staging later

### Days 19-20 — Push notifications + background tasks
- Background: memory extraction (writes to `conversations.metadata.memories` — old schema; v2 introduces proper memory service in Phase 2)
- Background: push notification via metadata service (preserves FCM/APNS format)
- Soft-delete of influencers (`is_active='discontinued'`)
- Auto-timestamping Postgres triggers (`conversations.updated_at` on message insert)

### Days 21-22 — End-to-end parity validation
🤳 **Rishi-test checkpoint #4 — PHASE 1 COMPLETE:** Rishi's Motorola exercises EVERY feature chat-ai has:
- Inbox loads with real conversations
- Sends message, gets real Gemini response (Tara routes to OpenRouter)
- Sends image, audio, media
- Creates new influencer via 3-step flow
- Edits system prompt
- Deletes conversation
- H2H chat (if applicable)
- Billing pre-check gates properly

Document feature-by-feature verification. Any bug → fix in v2 → retest.

**MILESTONE: v2 is a drop-in replacement for chat-ai from the user's perspective. Zero missing features.**

---

## Phase 2 — Memory + Depth (Days 23-28) — 1000× Priority #1

**Goal:** bot REMEMBERS across sessions. First 1000× improvement.

- Spawn `yral-rishi-agent-user-memory-service` from template
- New schema `agent_user_memory` — session_cache (Redis), episodic_events, semantic_facts, user_profiles, embeddings (pgvector)
- Background worker: extract facts from AI responses; dedup; consolidate
- On each turn: retrieve top-N semantic facts + top-N pgvector-similar episodic events → inject into prompt
- Scope: per (user, influencer) pair — isolation

🤳 **Rishi-test checkpoint #5:** tell influencer your dog's name; next session, same influencer remembers it.

---

## Phase 3 — Soul File + SSE Streaming (Days 29-36) — 1000× Priority #2

**Goal:** better response quality + first token <200ms.

### Backend (Days 29-31)
- Spawn `yral-rishi-agent-soul-file-library`
- 4-layer schema (global / archetype / per-influencer / per-user-segment)
- Composer function merges layers in priority order
- Tara's existing prompt imported as Layer 3 (Tara)
- Default global layer: response-length guardrail + tone normalizer
- Guardrails append-on-save / strip-on-display preserved
- SSE endpoint on `POST /messages`: content-negotiation (Accept header) switches between JSON and SSE

### Mobile Changes #2-#5 (Days 32-35)
Per A12, one at a time. Each documented + tested on Motorola.

- **Change #2:** Ktor SSE parser in `/shared/libs/http/SseClient.kt` (~100 lines)
- **Change #3:** `sendMessageStream()` in `ChatRemoteDataSource` (alongside existing `sendMessageJson()`)
- **Change #4:** Firebase flag `enable_chat_streaming` wiring + routing logic in ViewModel
- **Change #5:** ConversationViewModel consumes Flow, updates content live (Compose recomposes naturally)

### Day 36 — Quality validation
🤳 **Rishi-test checkpoint #6:** first token <200ms; tone feels consistent; Tara still feels like Tara.

---

## Phase 4 — Proactivity + First-Turn Nudge (Days 37-42) — 1000× Priorities #4 + #7

### Backend (Days 37-39)
- Spawn `yral-rishi-agent-proactive-message-scheduler`
- `POST /api/v1/chat/conversations/{id}/presence` (heartbeat endpoint)
- Scheduler: track presence per (user, conv); fire auto-follow-up after 25s inactivity
- Stop after 2nd silence
- SSE watch-mode: after streaming response, keep channel open; push new messages
- Cross-session proactive pings (throttled, 1 per user per day initially)

### Mobile Changes #6-#7 (Days 40-42)
- **Change #6:** presence heartbeat (every 10s while foreground)
- **Change #7:** SSE reader handles `new_message` events + chip dismissal on any new bot message

🤳 **Rishi-test checkpoint #7:** first-turn nudge fires on Motorola; cross-session proactive ping arrives as push notification.

---

## Phase 5 — Content Safety + Moderation (Days 43-46)

Critical before we go any further with proactive features. Per H4 — ships before any canary.

- Spawn `yral-rishi-agent-content-safety-and-moderation`
- Moderation classifier (pre and post)
- Crisis detector (mental health red flags → route to Claude for careful response)
- Prompt injection defense middleware (H5)
- NSFW classifier + age-gate scaffolding (for Plan G later)

🤳 **Rishi-test checkpoint #8:** safety filter active; no false-positive on normal conversation; known injection payloads blocked.

---

## Phase 6 — Programmatic AI Influencer Creation via MCP (Days 47-52) — 1000× Priority #5

- Spawn `yral-rishi-agent-skill-runtime`
- MCP server wrapping `/api/v1/influencers/*` endpoints
- API key auth (separate from JWT)
- Documentation for external tools (Claude Desktop, scripts)
- Rate limits + safety checks

🤳 **Rishi-test checkpoint #9:** Claude Desktop connects to v2's MCP server; creates an influencer programmatically; it shows up in Rishi's Motorola app.

---

## Phase 7 — Creator Tools + Analytics Backend (Days 53-60) — 1000× Priority #8

- Spawn `yral-rishi-agent-creator-studio` (Soul File Coach — creator chats with LLM to improve their bot)
- Spawn `yral-rishi-agent-events-and-analytics`
- Per-bot analytics (message counts, retention proxies, quality scores)
- Mobile UI deferred to v2.x (backend demonstrable first)

---

## Phase 8 — Meta-AI Advisor (Days 61-65) — 1000× Priority #10

- Spawn `yral-rishi-agent-meta-improvement-advisor`
- LLM reads yesterday's metrics + current backlog → emits top-3 recommendations
- Rishi-facing endpoint; no mobile work

---

## Phase 9 — Creator Monetization + Private Content (Days 66-80) — 1000× Priority #9

Deferred this long because it requires legal clarity + new mobile UI for tip jar / content unlock. Backend-first; mobile comes in v2.x releases.

- Spawn `yral-rishi-agent-media-generation-and-vault` extensions (if not already in Phase 1)
- Content request flow
- Consent + age-gate enforcement
- Safety gate (H6)
- Per-user/per-influencer rate limits

---

## Phase 10 — rishi-4/5/6 Cluster Deployment (Separate Approval Gate)

Requires explicit Rishi approval: "deploy v2 to rishi-4/5/6."

When approved:
- Provision rishi-4/5/6 (Docker Swarm, Patroni HA, Redis Sentinel, Langfuse, Caddy Swarm service, Prometheus + Grafana + Loki on rishi-5)
- 3-layer backups live (Patroni HA + WAL PITR + Backblaze B2 weekly)
- Chaos tests pass (10 tests per H3)
- Latency baselines captured (Phase 0 deliverable)
- Staging deploy first; production canary after
- Caddy on rishi-1/2 adds upstream route to v2 cluster (Rishi approves per-snippet)

Estimated 2-3 weeks of cluster-engineering work. Can overlap with Phase 7-9 if approved.

---

## Phase 11 — Cutover to V2 (Separate Approval Gate)

Requires explicit Rishi approval: "cut over to v2 now." Per A6 — no timeline, no pressure.

When approved:
- Caddy on rishi-1/2 starts routing percentage of `chat-ai.rishi.yral.com` traffic to v2 cluster
- Mobile's Firebase flag flips for a canary cohort (e.g., 1% of users)
- Metrics compared: D1/D7 retention, session length, payment conversion, latency, crash rate
- Ramp: 1% → 10% → 50% → 100%
- Old chat-ai stays alive as fallback for 90+ days
- Per-item retirement approval for every old service/repo/DNS record

---

## Summary

| Phase | Days | Checkpoint |
|---|---|---|
| 0 — Template + hello-world | 1-5 | 🤳 #0: template proven |
| 1 — Feature parity services + data port | 6-22 | 🤳 #1-#4: full parity on Motorola against ported production data |
| 2 — Memory + Depth | 23-28 | 🤳 #5: bot remembers |
| 3 — Soul File + SSE streaming | 29-36 | 🤳 #6: streaming + better quality |
| 4 — Proactivity + first-turn nudge | 37-42 | 🤳 #7: bot texts first |
| 5 — Safety + moderation | 43-46 | 🤳 #8: safety filter active |
| 6 — MCP influencer creation | 47-52 | 🤳 #9: Claude Desktop creates bots |
| 7 — Creator tools + analytics | 53-60 | Backend demo |
| 8 — Meta-AI advisor | 61-65 | Rishi gets daily top-3 |
| 9 — Monetization + private content | 66-80 | Backend first |
| 10 — rishi-4/5/6 deploy | Separate approval | Production infra |
| 11 — Cutover | Separate approval | Rishi's call |

**Total to Phase 1 parity on Rishi's Motorola: ~25 days at reasonable pace** (Phase 0 = Days 1-8, Phase 1 = Days 9-25).
**Total through Phase 4 (Billing integration — priority 3): ~50 days (~7 weeks).**
**Total through Phase 9 (Meta-AI advisor — priority 10, all backend built): ~90 days (~13 weeks).**

Pace flexes with your available hours. Every phase has a Rishi-on-Motorola checkpoint; we pause, test, iterate, then move on. Cutover (per A6) is NOT tied to any phase — entirely your call, no timeline.

---

## What I need from you BEFORE Day 1

> **Updated 2026-04-27 per Codex audit** — replaced stale "Laptop WiFi IP / Cloudflare tunnel / Firebase override" pre-launch questions with the current real checklist (see `multi-session-parallel-build-coordination/MASTER-STATUS.md` for live version).

1. **Type "build"** in coordinator session (per A5 + I1 — we're plan-only until you do)
2. **Codex API key** for OpenAI Codex review — store as GitHub repo secret `OPENAI_CODEX_API_KEY` per I10
3. **Sentry API key** for `sentry.rishi.yral.com` — confirm location (default `~/.config/dolr-ai/sentry-api-key`) per I7
4. **GitHub branch protection on main** — require PR + 1 approval + CI green per I10
5. **Saikat sign-off** on Phase 0 cluster provisioning (rishi-4/5/6) + the rishi-1/2 Caddy snippet via `yral-rishi-hetzner-infra-template` per A2 carve-out
6. **Gemini + OpenRouter API keys** for local backend — dev/test keys fine; secrets-manifest per D7
7. **Per-operation YES on chat-ai data port** when Day 9 ETL is ready (per A14 — pulling live chat-ai DB requires explicit approval each time)

NO laptop-IP needed (Day 8+ uses real cluster via `agent.rishi.yral.com` per A15). NO Cloudflare Tunnel needed (refined away 2026-04-24 evening). NO Firebase Remote Config flag needed during Phase 1 (deferred to Phase 3 per A16).

---

## What I am NOT doing until you say "build"

- Writing ANY code for ANY v2 service (including the template)
- Creating ANY GitHub repo
- SSH'ing into rishi-4/5/6 for provisioning
- Making ANY mobile change
- Touching rishi-1/2/3 in any way beyond read-only SSH
- Anything that materializes this plan into running systems

All edits so far are plan docs + memory + CONSTRAINTS. Zero code written. Zero systems affected.
