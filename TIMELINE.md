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
- Every mobile change = ONE change at a time, documented, never pushed (A12)
- Every feature in chat-ai must survive (A8 — can't cut without good reason + explicit approval)
- Naming: explicit English everywhere (B1, B5)
- No hardcoded IPs in code; secrets via manifest (C6, D7)
- Sentry = `sentry.rishi.yral.com` (A7, C4)

---

## Phase 0 — The V2 Template (Days 1-5)

**Goal:** a proven, reusable template for spawning every v2 service. Nothing else happens until this is solid.

### Day 1 — Template skeleton
Local repo: `~/Claude Projects/yral-rishi-agent/yral-rishi-agent-new-service-template/` (local git init; NO GitHub push yet).

Fork the structure from existing `yral-rishi-hetzner-infra-template` (that template stays untouched per no-delete). Evolve:

- `pyproject.toml` — Python 3.12, FastAPI, asyncio, asyncpg, redis-py, httpx, pydantic, alembic, pytest
- `Dockerfile` — Python 3.12-slim, non-root user, multi-stage build
- `docker-compose.yml` — service + Postgres + Redis + pgBouncer + Langfuse (for local dev)
- `docker-compose.prod.yml` — Swarm-targeting variant (for later, when Rishi approves prod deploy)
- `project.config` — per-service single source of truth (name, domain, ports, replica tier)
- `shared-config.yaml` — cross-service shared values (URLs, hostnames, feature-flag defaults)
- `bootstrap/` folder (per constraint F7 — INSIDE template repo, not separate):
  - `cluster.hosts.yaml` (shape only, no IPs — values from GitHub Secrets)
  - `secrets-manifest.yaml` (declarative spec of every env var every service needs, per D7)
  - `scripts/` (render-cluster-config.py, generate-ssh-config.sh, apply-node-labels.sh, etc.)
  - `systemd/yral-v2-swarm-resync.service` (reboot resilience)

### Day 2 — Template app-layer scaffolding
Every file a service spawned from template inherits:

- `app/main.py` — FastAPI app, lifespan hooks, graceful shutdown
- `app/health.py` — three-tier health endpoints (`/health/live`, `/health/ready`, `/health/deep`)
- `app/database.py` — asyncpg connection pool via pgBouncer
- `app/redis_client.py` — Redis Sentinel-aware client (for future HA; local mode is simple Redis)
- `app/auth.py` — JWT middleware with dual-validate (strict-sig flag default OFF per E9)
- `app/llm_client.py` — LLM abstraction (Gemini + Claude + OpenRouter + future self-hosted)
  - Dual routing per A10: per-influencer-id rules → `is_nsfw` flag → archetype defaults → Gemini
  - Tara's rule built in (per-id override to OpenRouter with her model)
- `app/sentry_middleware.py` — Sentry DSN from env, service-name tag
- `app/langfuse_middleware.py` — auto-trace LLM calls
- `app/event_stream.py` — Redis Streams emit + consumer-group helpers
- `app/feature_flags.py` — Postgres-table-based flags with 30s polling
- `app/idempotency_middleware.py` — default-on for non-GET, Redis 24hr dedup
- `app/pii_redaction.py` — structured logger with allowlist
- `app/prompt_injection_defense.py` — pre-orchestrator classifier (for LLM-consuming services)

### Day 3 — Template CI/CD + scripts
- `scripts/new-service.sh` (refactored from existing template) — 1-command spawn for new service
- `scripts/generate-deploy-env-block.py` — reads secrets-manifest, emits env block for CI
- `scripts/validate-secrets-for-this-service.sh` — CI gate, refuses deploy if required secrets missing
- `.github/workflows/deploy.yml` — canary deploy pattern (rishi-4 → rishi-5 → rishi-6 with auto-rollback), currently local-only; remote deploy gated
- `.github/workflows/lint-naming.yml` — CI lint enforcing explicit-English naming (per B5) + no-literal-IPs (per C6)
- `.github/workflows/eval-diff.yml` — Langfuse eval harness runs on LLM-touching PRs

### Day 4 — Template documentation (5 required docs per constraint F8)
- `docs/DEEP-DIVE.md` — architecture, every component explained
- `docs/READING-ORDER.md` — where to start
- `docs/CLAUDE.md` — opens with explicit-English naming rule (B5)
- `docs/RUNBOOK.md` — common operational scenarios
- `docs/SECURITY.md` — threat model, secrets handling, auth flow
- `README.md` — quick-start guide

### Day 5 — Template proving via hello-world
Spawn throwaway `yral-rishi-agent-hello-world` from the template:
```bash
cd ~/Claude\ Projects/yral-rishi-agent/yral-rishi-agent-new-service-template
bash scripts/new-service.sh --name yral-rishi-agent-hello-world --tier supporting
```

Verifications:
- [ ] CI passes locally (no GitHub; run via `act` or direct shell)
- [ ] `docker compose up` spins up the service
- [ ] `/health/live`, `/health/ready`, `/health/deep` all return 200
- [ ] Sentry receives a test exception (tag `service=yral-rishi-agent-hello-world`)
- [ ] Langfuse receives a test trace (LLM call or fake)
- [ ] Structured logs visible in `docker compose logs`
- [ ] Postgres schema `agent_hello_world` created and accessible
- [ ] Redis client connects
- [ ] Feature flag lookup works
- [ ] pgBouncer in the path

🤳 **Rishi-test checkpoint #0:** Rishi sees `docker compose up` on his laptop works end-to-end. Phone doesn't talk to it yet — just confirm template is solid.

**If any verification fails, we fix the TEMPLATE (not the hello-world), then re-spawn.** This is the "fold learnings back into template" principle.

---

## Phase 1 — Feature Parity Services (Days 6-22)

**Goal:** every feature in chat-ai works in v2, tested by Rishi on his Motorola, against local backend with full production data ported.

### Day 6 — Data port ETL (all of chat-ai → local v2 Postgres)

Build a one-time ETL script that:
- Connects to the current live chat-ai Postgres (read-only, via tunnel if needed — never writes)
- Dumps `ai_influencers`, `conversations`, `messages`, and any other live tables
- Transforms minimally if schema differs (v2 schemas are explicit-English e.g., `agent_influencer_directory.ai_influencers`)
- Loads into local v2 Postgres
- Preserves all IDs (so mobile deep-links still work in testing)

Verification:
- Count of influencers in local v2 == count in prod
- Count of conversations == count in prod
- Count of messages == count in prod
- Spot-check: 5 random influencers have identical data to prod

Rishi-visible: local Postgres now mirrors production state.

### Days 7-8 — Spawn `yral-rishi-agent-public-api` from template
Single HTTP entry point. Implements endpoints in phased order below (Days 9-18). For now, skeleton: just health + JWT auth middleware.

### Days 9-10 — Core chat endpoints (Phase 1.A)
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

### Day 13 — **Mobile Change #1: Firebase Remote Config URL override flag**
Per A12 workflow: one change at a time, documented.

Make exactly one edit in `~/Claude Projects/yral-mobile/`:
- `AppConfigurations.kt` → reads optional `chat_base_url_override` from Firebase Remote Config; falls back to hardcoded prod URL if empty
- `AppDI.kt` → Koin binding routes to the override when set
- Firebase Remote Config default = empty string → zero behavior change for all users except when override is set per-account

Document in `mobile-client-change-log.md` with all 6 fields.

Build debug APK, install on Motorola, verify:
- Flag empty → app hits prod chat-ai (existing behavior) ✅
- Flag set to `http://<laptop-ip>:8000` → app hits local backend ✅

🤳 **Rishi-test checkpoint #1:** Rishi's phone reaches local backend. Influencer list loads (real production data).

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

**Total to Phase 1 parity on Rishi's Motorola: ~22 days at reasonable pace.**
**Total through Phase 4 (memory + streaming + proactivity = bulk of 1000× UX): ~42 days (~6 weeks).**
**Total through Phase 9 (backend fully built): ~80 days (~11-12 weeks).**

Pace flexes with your available hours. Every phase has a Rishi-on-Motorola checkpoint; we pause, test, iterate, then move on.

---

## What I need from you BEFORE Day 1

1. **Approval of this plan** — explicit OK after review
2. **Gemini API key** (or OpenRouter key) for local backend — dev/test key is fine
3. **Laptop WiFi IP or preference for Cloudflare tunnel** — phone needs to reach the local backend
4. **Firebase Remote Config admin access** — to set `chat_base_url_override` for your account
5. **Read-only access to current chat-ai Postgres** — for the Phase 1 Day 6 data port ETL. SSH tunnel via `deploy@rishi-1` is fine (read-only queries)

---

## What I am NOT doing until you say "build"

- Writing ANY code for ANY v2 service (including the template)
- Creating ANY GitHub repo
- SSH'ing into rishi-4/5/6 for provisioning
- Making ANY mobile change
- Touching rishi-1/2/3 in any way beyond read-only SSH
- Anything that materializes this plan into running systems

All edits so far are plan docs + memory + CONSTRAINTS. Zero code written. Zero systems affected.
