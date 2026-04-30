# Session Sharding and Ownership

> Each session has ONE owner-scope. It can read everything in the monorepo (for context) but can WRITE only inside its scope.

## Visual map

```
yral-rishi-agent/   (monorepo root)
│
├── yral-rishi-agent-plan-and-discussions/   ← COORDINATOR only
│   ├── CONSTRAINTS.md
│   ├── README.md
│   ├── TIMELINE.md
│   ├── V2_INFRASTRUCTURE_AND_CLUSTER_ARCHITECTURE_CURRENT.md
│   └── multi-session-parallel-build-coordination/
│       └── (this folder)
│
├── bootstrap-scripts-for-the-v2-docker-swarm-cluster/   ← SESSION 1 only
│   ├── cluster.hosts.yaml
│   ├── secrets-manifest.yaml
│   ├── scripts/
│   ├── systemd/
│   └── chaos-tests/
│
├── yral-rishi-agent-new-service-template/   ← SESSION 2 only
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── docker-compose.swarm.yml
│   ├── app/
│   ├── docs/
│   └── scripts/new-service.sh
│
├── yral-rishi-agent-public-api/   ← SESSION 3 only
│   └── (full FastAPI service, owned by Session 3)
│
├── yral-rishi-agent-conversation-turn-orchestrator/   ← SESSION 4 only
├── yral-rishi-agent-soul-file-library/                ← SESSION 4 only
├── yral-rishi-agent-influencer-and-profile-directory/ ← SESSION 4 only
│
├── yral-rishi-agent-user-memory-service/   ← SESSION 5 only
├── etl-scripts/                            ← SESSION 5 only
├── tests/                                  ← SESSION 5 only
│
├── shared-library-code-used-by-every-v2-service/   ← COORDINATOR only
│   (Sessions request additions via PR-to-coordinator)
│
└── .github/workflows/   ← COORDINATOR only
    (CI workflows, Codex review action, lint workflows)
```

## Session 1 — Infra & Cluster

**Identity:** "I am Session 1. I own everything that makes rishi-4/5/6 a usable Docker Swarm cluster, plus the rishi-1/2 Caddy routing snippet for v2."

**Scope (write-allowed):**
- `bootstrap-scripts-for-the-v2-docker-swarm-cluster/**`
- `latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/**` (the Sentry baseline cron — Session 1 owns the script even though docs are coordinator-owned)
- The single new Caddy snippet file added to `yral-rishi-hetzner-infra-template` (a SEPARATE repo Rishi owns) — Session 1 opens that PR

**First-week deliverables:**
- Day 0.5: `pull-sentry-baseline.py` + launchd cron (per `project_v2_first_build_task_sentry_baseline_pull.md`)
- Day 4: Docker Swarm bootstrap on rishi-4/5/6 (init, manager join, overlay networks, UFW)
- Day 5: Patroni cluster across rishi-4/5/6 (sync commit, pgBouncer in front, WAL-G to S3)
- Day 5: Redis Sentinel (primary rishi-4, replica rishi-5, sentinels on 4/5/6)
- Day 6: Langfuse self-hosted on rishi-6, Beszel agents, Uptime Kuma monitors
- Day 6: Caddy as Swarm service on rishi-4/5 (TLS internal, fronts overlay)
- Day 6: Chaos tests pass (kill rishi-6, kill Patroni container, fill disk, partition rishi-6)
- Day 7: Caddy snippet PR to `yral-rishi-hetzner-infra-template` for `agent.rishi.yral.com` upstream
- Day 8: First deploy of hello-world (built by Session 2) lands on cluster, Motorola hits it

**Out of scope (forbidden):**
- Editing template app code (Session 2's job)
- Editing public-api or any service code (Sessions 3-5)
- Touching CONSTRAINTS or README (coordinator)
- Pulling live yral-chat-ai data (needs Rishi YES via coordinator)

**Branch naming:** `session-1/<feature>` (e.g., `session-1/sentry-baseline-cron`, `session-1/patroni-bootstrap`)

**Startup prompt to paste when opening Session 1:**
```
You are Session 1 (Infra & Cluster) for the yral-rishi-agent v2 build.

Read these files in order before doing anything:
1. ~/Claude Projects/yral-rishi-agent/yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
2. ~/Claude Projects/yral-rishi-agent/yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/00-MASTER-PLAN.md
3. ~/Claude Projects/yral-rishi-agent/yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/01-SESSION-SHARDING-AND-OWNERSHIP.md (THIS FILE)
4. ~/Claude Projects/yral-rishi-agent/yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/02-AUTO-MODE-GUARDRAILS.md
5. ~/Claude Projects/yral-rishi-agent/yral-rishi-agent-plan-and-discussions/TIMELINE.md (Phase 0 sections)

Confirm back:
- The folders you can write to
- The folders you must NOT touch
- Your plan for Day 0.5 (Sentry baseline cron)
- Any constraints you're unsure about

Do NOT start coding until I type "build". After "build", you operate in Auto-mode per 02-AUTO-MODE-GUARDRAILS.md.
```

## Session 2 — Template & Hello-World

**Identity:** "I am Session 2. I own the v2 template that all 13 services inherit from."

**Scope (write-allowed):**
- `yral-rishi-agent-new-service-template/**`
- `yral-rishi-agent-hello-world/**` (throwaway, spawned from template, kept until Day 8 verification, then archived per A1 — never deleted)

**First-week deliverables:**
- Day 1: template skeleton (pyproject, Dockerfile, docker-compose, project.config, shared-config)
- Day 2: app-layer scaffolding (auth, redis_client, llm_client, sentry middleware, langfuse middleware, idempotency, pii_redaction, prompt_injection_defense)
- Day 3: CI workflows (deploy, lint, eval), 8 required docs (5 inherited + WALKTHROUGH + GLOSSARY + WHEN-YOU-GET-LOST per F8 upgraded), `new-service.sh`
- Day 3: spawn `yral-rishi-agent-hello-world` from template, verify all middleware wired
- Day 4: simple Tier-0 browser debug page (HTML + JS) for sanity-check curl

**Out of scope:**
- Cluster bootstrap (Session 1)
- Real service code (Sessions 3-5)
- Editing CONSTRAINTS / README / TIMELINE (coordinator)

**Branch naming:** `session-2/<feature>`

**Startup prompt:** identical structure to Session 1's, swap "Infra & Cluster" → "Template & Hello-World".

## Session 3 — Public-API + Auth

**Identity:** "I am Session 3. I own the front door — the only service mobile clients talk to directly. I do NOT generate LLM responses (that's Session 4's orchestrator); I route requests."

**Scope (write-allowed):**
- `yral-rishi-agent-public-api/**`

**First-week deliverables (starts Day 9 after Sessions 1+2 unblock):**
- Day 9: `public-api` spawned from Session 2's template
- Day 10: JWT validation middleware (JWKS from `auth.yral.com`, Redis 1hr cache, dual-validate flag default OFF per E9)
- Day 11: yral-billing pre-check integration (Redis 60s cache for access decisions per E7)
- Day 12: routing layer — forwards to orchestrator (Session 4) for chat turns; to influencer-directory (Session 4) for catalog
- Day 13: idempotency middleware default-on per F10
- Days 14-15: WebSocket inbox endpoint (parity with chat-ai's `WS /api/v1/chat/ws/inbox/{user_id}`)
- Days 16-18: SSE streaming skeleton (parity-mode = no streaming yet; just the framework, ready for Phase 3)

**Out of scope:**
- LLM calls (Session 4)
- Soul File composition (Session 4)
- Memory reads (Session 5)
- DB schema design beyond what public-api owns (`agent_public_api` schema only)

**Branch naming:** `session-3/<feature>`

## Session 4 — Orchestrator + Soul File + Influencer Directory

**Identity:** "I am Session 4. I own the brain (orchestrator turn lifecycle) plus the recipe book (Soul File library) plus the bot catalog (influencer directory)."

**Scope (write-allowed):**
- `yral-rishi-agent-conversation-turn-orchestrator/**`
- `yral-rishi-agent-soul-file-library/**`
- `yral-rishi-agent-influencer-and-profile-directory/**`

**First-week deliverables (starts Day 9):**
- Day 9: three services spawned from template
- Days 10-12: orchestrator turn lifecycle — receives request, parallel reads (memory/soul-file/influencer/billing-cache), composes prompt, calls LLM, persists, emits events
- Days 13-15: 4-layer Soul File composer (global / archetype / per-influencer / per-user-segment) with version pinning + warm Redis cache
- Days 9-15 in parallel: influencer directory CRUD (parity with chat-ai endpoints — full CRUD + 3-step creation flow + system-prompt editing + video-prompt + admin ban/unban)
- Day 15: Tara LLM routing rule installed per A10 (per-influencer-id → OpenRouter; `is_nsfw` → OpenRouter; archetype defaults → Gemini Flash)

**Out of scope:**
- Memory extraction (Session 5)
- ETL (Session 5)
- Public-api routing (Session 3)

**Branch naming:** `session-4/<feature>`

> **Note on Session 4's load:** this session owns 3 services. If it gets overloaded, coordinator can split influencer-directory off into a new Session 6 once parity work is rolling.

## Session 5 — ETL + Memory + Tests

**Identity:** "I am Session 5. I move chat-ai data into v2 (Day 9 ETL), I build memory features (Phase 2), and I write the contract tests that prove v2 is a drop-in for chat-ai."

**Scope (write-allowed):**
- `etl-scripts/**`
- `yral-rishi-agent-user-memory-service/**`
- `tests/**` (cross-service contract tests, integration tests, eval gold prompts)

**First-week deliverables:**
- Day 9 morning: ETL plan submitted to Rishi via coordinator (per A14: explicit YES required)
- Day 9 afternoon: ETL script runs after YES; chat-ai data lives in v2 cluster Postgres
- Day 9 evening: row-count verification (chat-ai count == v2 count for every table)
- Days 10-12: contract tests against chat-ai endpoints (every endpoint mobile calls returns same JSON shape from v2 as from chat-ai)
- Day 13: Langfuse eval gold-prompt set seeded from chat-ai's "best" and "worst" prod conversations
- Phase 2 (after Phase 1 ends ~Day 25): memory service stub, semantic_facts table, embeddings via pgvector, async memory-extractor worker

**Out of scope:**
- Service code in other services (Sessions 3-4)
- Template (Session 2)
- Cluster (Session 1)

**Branch naming:** `session-5/<feature>`

## Coordinator session (you + me, here)

**Identity:** "I am the Coordinator. I hold cross-cutting context. I do not write service code; I write plans, constraints, integration specs, and review what other sessions produce."

**Scope (write-allowed):**
- `yral-rishi-agent-plan-and-discussions/**`
- `shared-library-code-used-by-every-v2-service/**` (curate; sessions PR additions to me)
- `.github/**` — entire `.github` tree (workflows + scripts + PR template + issue templates)
- `.claude/**` — Claude Code config + hooks + scripts + agent definitions
- `interface-contracts/**` (which services expose what to whom — historical; now lives under `multi-session-parallel-build-coordination/interface-contracts/`)
- Root-level top-of-repo files: `README.md`, `.gitignore`, `LICENSE` (when added)
- `~/Library/LaunchAgents/com.yral.rishi.agent.*.plist` files installed locally on Rishi's laptop (not committed; deployed via instructions in plan-and-discussions)

**Coordinator daily loop:**
1. Morning: read each session's SESSION-N-LOG.md from yesterday
2. Identify blockers + cross-session conflicts
3. Update interface contracts if needed
4. Read PRs from sessions, read Codex reviews, summarize for Rishi
5. Ask Rishi for merge YES on cleared PRs
6. Apply merges, watch deploys
7. Update CONSTRAINTS / TIMELINE if anything new emerged

**Out of scope:**
- Writing service code (delegated to sessions)
- Anything Rishi hasn't approved

## What if two sessions need the same change?

**Scope leak protocol:**
1. Session A discovers it needs to edit a file owned by Session B
2. Session A STOPS. Writes a request to coordinator: "I need X in Session B's scope because Y"
3. Coordinator either:
   - (a) Asks Session B to make the change in its next PR
   - (b) Decides the change belongs in shared-library and makes it themselves
   - (c) Re-shards scopes if the conflict is structural
4. Session A waits, OR works on something else in its own scope, until resolved

**Never:** Session A directly edits Session B's files. CI lint enforces this; PR fails if scope is violated.

## Session retirement

A session that's done with its work doesn't disappear — it stays available for follow-ups (bug fixes, late-discovered parity gaps, doc updates). But it's not actively driving forward.

When all 5 sessions report "Phase 1 complete," coordinator declares Phase 1 done, Rishi tests on Motorola, then we plan Phase 2 sharding (might be different shape — memory-heavy work, fewer parallel sessions).
