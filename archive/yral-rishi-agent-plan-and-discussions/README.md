# Making YRAL Chat the Best in the World — The Greenfield Plan (v3)

> **Companion docs in this repo** (dolr-ai/yral-rishi-agent — public monorepo):
> - [`CONSTRAINTS.md`](./CONSTRAINTS.md) — the tight reviewable list of every hard constraint, organized by category, status per row. Start here if you want a fast index.
> - [`V2_TEMPLATE_AND_CLUSTER_PLAN.md`](./V2_TEMPLATE_AND_CLUSTER_PLAN.md) — the canonical template + rishi-4/5/6 cluster design doc (Swarm-only networking, node role layout, bootstrap workflow, CI guardrails, net-new capabilities). This doc extends that one with product-facing capability plans, roadmap, memories, and cross-team integration.
> - [`FUTURE_PICKUPS.md`](./FUTURE_PICKUPS.md) — parking-lot for ideas surfaced during R&D that are NOT in the current plan. Each entry has trigger conditions for revisiting. Currently holds: double-lane request queueing, A2UI declarative agent-to-UI protocol (both from OpenClaw R&D 2026-04-27).
>
> 🚨 **CUTOVER NOTE (locked 2026-04-23):** any week/month/phase-number timelines mentioned below in the context of cutover (Phase 4, canary, DNS flip, deprovisioning old service) are **aspirational sequencing only**. Actual cutover timing is entirely at Rishi's discretion — I do not propose, schedule, or raise cutover until he explicitly says so. Yral-chat-ai Python WENT LIVE 2026-04-23 and is handling production load; users are reporting slow latency (directly motivates the 50%-faster HARD target in E1). V2 builds calmly alongside for as long as Rishi wants. See `feedback_no_cutover_without_explicit_approval.md` memory and CONSTRAINTS.md row A6.
>
> 🚨 **SENTRY REMINDER (locked 2026-04-23):** ALL v2 services use `sentry.rishi.yral.com` (Rishi's self-hosted on rishi-3). NEVER the team `apm.yral.com`. Reinforced 3 times by Rishi. See CONSTRAINTS.md row A7 + C4. **Sentry API access pre-authorized 2026-04-24** for reading yral-chat-ai perf/error data to establish and track baselines (CONSTRAINTS I7).
>
> 🚨 **HARD CONSTRAINTS (locked 2026-04-24):**
> 1. **V2 MUST be ≥50% FASTER than Python yral-chat-ai** on user-interactive endpoints (chat open, send message, streaming TTFT, list conversations). Stretch goal: match world-class AI companion apps (Character.ai, Replika, Talkie). Every decision flows from this. See CONSTRAINTS E1.
> 2. **Full greenfield + first-principles + ALL data MUST port** — reverses the earlier "chat history is discardable" position. User chat history, AI influencers, messages, read states all carry forward for LOCAL TESTING and PRODUCTION CUTOVER. V2 schema is free to be whatever's best for the goal; ETL transforms rows. See CONSTRAINTS A4 + A13.
> 3. **Claude pushes back** on non-industry-standard or likely-wrong decisions — technical, architectural, product, UX. Rishi is a non-programmer with ADHD and explicitly wants pushback. Format: state concern, give alternative, explain tradeoff, let Rishi decide. See CONSTRAINTS I6.
> 4. **Real YRAL mobile Android app tests against the real cluster from Day 8+** — refined 2026-04-24 evening from earlier "Cloudflare Tunnel to laptop" plan. Route: Cloudflare DNS → rishi-1/2 Caddy (Rishi-owned, set up via `yral-rishi-hetzner-infra-template`) → rishi-4/5 Swarm ingress. Debug APK built by Claude, `CHAT_BASE_URL = agent.rishi.yral.com`. See CONSTRAINTS A9 + A15.
> 5. **Feature parity FIRST, mobile code changes DEFERRED to Phase 3+** — Day 8 through ~Day 28, v2 is a drop-in replacement (same JSON shapes as chat-ai). Motorola debug APK needs ZERO code changes in this window, only `CHAT_BASE_URL` swap. Mobile changes (SSE streaming, presence, new screens) start Phase 3, one-at-a-time per A12. See CONSTRAINTS A16.
> 6. **3-tier code documentation standard for non-programmer + ADHD reading (locked 2026-04-27).** Every code file supports reading at three depths: Tier 1 (30s file-header skim), Tier 2 (1-2 min function/class headers with WHAT/WHEN/WHY), Tier 3 (line-by-line role-comments — ROLE not SYNTAX). Functions in priority order, RELATED FILES footers, no abbreviations. 8 required docs per service: DEEP-DIVE / READING-ORDER / CLAUDE / RUNBOOK / SECURITY (existing 5) + WALKTHROUGH (narrative trace of one user action through the code) + GLOSSARY (every domain term defined) + WHEN-YOU-GET-LOST (1-page recovery doc with restaurant/pantry analogies). CI-enforced: comment density, doc presence, AI agents re-comment as part of every code change. See CONSTRAINTS B7 + F8.
>
> 🚨 **REFINED PHASE-0 SEQUENCE (locked 2026-04-24 evening):**
> - Days 1-3: LOCAL template dev on laptop (Docker Compose, localhost). Fast iteration to prove template + hello-world. No Motorola testing.
> - Days 4-7: rishi-4/5/6 cluster provisioning (Swarm + Patroni + Redis Sentinel + Langfuse + Caddy Swarm service + chaos tests per H3). In parallel with final template work.
> - Days 7-8: rishi-1/2 Caddy snippet added via `yral-rishi-hetzner-infra-template` repo (Rishi owns that Caddy). DNS `agent.rishi.yral.com` routes through.
> - Day 8+: Motorola debug APK points at real cluster. Every checkpoint from here on = real production-shape routing.
>



> **Purpose:** Design a **brand-new, greenfield AI-companion chat platform** that is 1000× better than the current chat infrastructure. The existing Python `yral-chat-ai` service and everything preceding it stay live as a safety net throughout. Rishi brainstorming doc. Non-programmer friendly. ADHD-aware (extensive, structured, skim-friendly).
>
> **The constraint block (authoritative, binding):**
>
> - **GitHub org**: everything lives under [github.com/dolr-ai](https://github.com/dolr-ai). New repos go here.
> - **Canonical product doc**: [dolr-ai/yral/blob/main/context-for-agents.md](https://github.com/dolr-ai/yral/blob/main/context-for-agents.md) — the single source of truth for YRAL features, screens, flows, glossary. **Everything we build aligns with this doc.**
> - **Servers** (IPs shown here for reference; they must NOT appear in code/templates/scripts — see `feedback_no_hardcoded_ips.md`. In the plan doc itself, IPs are fine.):
>   - **Legacy production cluster** (DO NOT TOUCH — read-only access only when needed): `rishi-1` → 138.201.137.181, `rishi-2` → 136.243.150.84, `rishi-3` → 136.243.147.225. Three live production projects run here; any change can take prod down.
>   - **V2 cluster** (allocated 2026-04-23 by Saikat, dedicated for yral-rishi-agent): `rishi-4` → 138.201.128.108 (Swarm manager, edge + state-primary), `rishi-5` → 88.99.160.251 (Swarm manager, edge + observability), `rishi-6` → 162.55.88.112 (Swarm manager, compute + Langfuse). All in Hetzner (rishi-1/2/3/4 confirmed in Falkenstein FSN1; rishi-5 ambiguous; rishi-6 likely Nuremberg NBG1 — confirm via `hcloud server describe` or ask Saikat; if cross-DC, keep Patroni sync replica within FSN1).
>   - **Hardware**: Intel Core i7-6700 @ 3.4 GHz (4C/8T), 62.6 GB RAM, 2× 512 GB NVMe (RAID TBC), Ubuntu 24.04.4 LTS.
>   - **Access**: `~/.ssh/rishi-hetzner-ci-key` for all 6 nodes. `rishi-deploy` user on rishi-4/5/6 (matches legacy convention). Saikat grants time-limited root (~1 week) for day-0 bootstrap; after that, scoped sudoers.
> - **Sentry decision (locked 2026-04-23)**: v2 services emit errors/traces/perf to `sentry.rishi.yral.com` (Rishi's self-hosted Sentry on rishi-3). **We explicitly do NOT use `apm.yral.com`** (the team-shared Sentry Saikat provisioned earlier). Reason: Rishi owns `sentry.rishi.yral.com` and can tune it; `apm.yral.com` is shared team infra we don't control.
> - **Wildcard DNS**: `*.rishi.yral.com` → rishi-1, rishi-2 (load balancer nodes). All new services get subdomains under this wildcard.
> - **Current proxy**: Caddy (on rishi-1, rishi-2) — routing to `rishi-hetzner-infra-template`, Sentry, `yral-chat-ai` (Python). **Free to switch** from Caddy if justified; Saikat is neutral.
> - **Current orchestrator**: Docker Swarm. Saikat is happy with the choice but **free to switch** (Kubernetes, Nomad, K3s) if justified.
> - **More servers available**: ✅ **DONE 2026-04-23** — Saikat has provisioned rishi-4/5/6 (IPs above). More can be requested if scale demands.
> - **NO-DELETE covenant** 🚨 (hard rule from Rishi, non-negotiable): Until Rishi gives explicit per-item approval — and only after the new service is live in production — we cannot delete anything from existing infrastructure: not the Python `yral-chat-ai`, not the Hetzner template repo, not the Sentry instance, not any config on rishi-1/2/3. This extends to DNS, Caddy routes, GitHub repos, GitHub secrets, Swarm stacks. **If in doubt, ask. Never delete.**
> - **Mobile client constraint** 🚨: The mobile app (owned by Sarvesh + Shivam) currently calls `yral-chat-ai`. The hardest constraint: **at most ONE change in the mobile codebase** when we cut over. Ideally zero — via DNS flip of a preserved URL like `chat.yral.com`. Any required mobile-side routing change needs a strong justification that Sarvesh/Shivam can defend.
> - **Data preservation rules** (REVERSED & UPGRADED 2026-04-24): ALL DATA MUST PORT — for LOCAL TESTING on Rishi's Motorola AND for eventual PRODUCTION CUTOVER (whenever Rishi says). AI influencers (Soul Files, avatars, bios, earnings, follower counts) + full user chat history (conversations, messages, read states, unread counts, typing state) all carry forward. Reversed from earlier "chat history discardable" position per Rishi 2026-04-24: *"earlier we were discussing that we are fine losing chat data — but now I am sure that would be the wrong approach since it will hamper user experience for current users."* V2 schema is fully greenfield (design from first principles for the 50%-faster + 1000×-better goal); ETL transforms chat-ai rows into whatever shape v2 wants. Pulling data from live yral-chat-ai during dev requires explicit Rishi approval per-operation (CONSTRAINTS A14).
> - **Feature parity required from day 1**: the new service must support (a) Human ↔ AI influencer chat, (b) Human ↔ Human chat, and (c) "Chat as Human" creator-takeover. The current schema already supports `conversation_type = 'human_chat'` via `participant_b_id` — carry that forward.
> - **Explicit naming** 🚨: per Rishi's request — every service, table, column, function, variable must be named so an English reader can infer its purpose without comments. No cryptic abbreviations. Prefer `conversation-turn-orchestrator` over `orch-svc`, `user-message-memory-extractor` over `mem-ext`. Verbose and obvious beats terse and clever.
> - **Existing observability** (reuse, don't rebuild): Sentry at `sentry.rishi.yral.com` (rishi-3), Beszel at `beszel.yral.com`, Uptime Kuma at `status.yral.com`, Vault at `vault.yral.com`. Reuse all of these for the new service.
> - **Existing services we do NOT own** (integrate, don't replace): Ravi owns chat/metadata/auth/storage; Ansuman owns recommendation; Naitik owns infra (Coolify + Vault); Sreyas owns LTX video model. Our scope is chat. Other services are upstream/downstream integrations.
> - **Team**: Rishi (ADHD, 2-3h/day deep work, non-programmer) + Yoa + whoever Saikat allocates. Roadmap assumes effectively 1-1.5 engineers.
>
> **Top-line product insight (still holds):** the ceiling is **base response quality** — robotic tone, essay-length replies, one-word-auto-generated Soul Files, no creator editing, no global config dial. Only Tara works because you hand-crafted her Soul File. The new service's #1 design priority is a **layered Soul File system + creator Soul File coach**, followed by memory, proactivity, archetype specialization, private content, and meta-AI advisor.
>
> **Metrics baseline (from DOLR context doc, Mar 2026):** 4,000 daily downloads · 10K DAU · D1 ≈ 10% · D7 ≈ 1-2% · D30 ≈ 0% · 18-20 daily paying subs at ₹9. The D30≈0% is the single most damning number; memory + proactivity + quality should all be measured against closing it.

---

## 1. Context (what I learned before designing)

**What YRAL is:** TikTok-like short-form video app where every influencer is an AI agent users can chat with. 10K DAU (per your ChatGPT convo; earlier memory said ~1K — you've grown). Revenue = ₹9/24hr chat unlock. Most users chat with "companions" (emotional-connection bots, not specialists).

**What `yral-chat-ai` currently does (from exploring the repo):**

- Python / FastAPI / asyncpg / PostgreSQL (HA Patroni) — NOT Rust; your new services should also be Python/FastAPI to match template
- 7 REST endpoints (create conv, list, send msg, list msgs, mark read, delete, image-gen) + 1 WebSocket for inbox
- Every message: takes last 10 messages → ships to Gemini 2.5 (or OpenRouter for NSFW) → returns reply
- Existing **hook points** (ways other services can tap in WITHOUT modifying chat-ai):
  - `messages` table (read-only tail)
  - `conversations` table (read-only tail)
  - Existing `asyncio.create_task()` background memory-extraction already runs per message (`app/routes/chat_v1.py:535-555`)
  - WebSocket broadcaster, push-notification service, S3 uploads, Google Chat webhook pattern
- **Key shallowness bottleneck:** 10-message sliding window. No long-term memory, no user profile, no RAG, no cross-conversation learning, no proactive outreach.

**What the infra template gives you:**

- `bash scripts/new-service.sh --name foo` → 5-minute service spin-up, 1 command, ~100 lines config
- Python/FastAPI stateless HTTP service on Docker Swarm across rishi-1/2/3
- Optional HA Postgres, Sentry, S3 backups, Caddy TLS, Uptime Kuma health checks
- **Limitations to design around:**
  - No baked-in Redis/Kafka/NATS/RabbitMQ — you'd add one as its own service
  - No background workers — workers are separate HTTP services that poll or get pinged
  - Shared 3-node VPS — colocated services compete for CPU/RAM
  - Per-service Patroni = heavy; stateless services should use shared Postgres via schema

**What's different now (greenfield):** we don't have to work around chat-ai anymore. We get to design the turn lifecycle, memory system, Soul File system, and tool runtime as first-class citizens of a new platform — using chat-ai's data and lessons as input, but not its code as a constraint. The old service stays running as a safety net; we build beside it, on NEW servers, with zero impact on users until cutover.

---

## 1.5 Saikat's Infrastructure Covenant & the No-Delete Rule

**Where the new chat service runs (proposed):** on **dedicated new servers** `rishi-4`, `rishi-5`, `rishi-6` (Saikat to allocate). **Not** on the existing rishi-1/2/3. This is a critical decision. Reasons:

1. **Zero risk to production.** The existing rishi-1/2/3 keep serving the current Python chat-ai, Sentry, template. We can break anything on rishi-4/5/6 during development and not affect users.
2. **Clean cutover path.** When new service is production-ready, we point `chat.yral.com` DNS from rishi-1/2 to rishi-4/5. One DNS change; zero mobile-client change. Rollback = one DNS revert.
3. **No colocation contention.** Running 12 new services on rishi-1/2/3 alongside existing services would cause CPU/RAM fights and make both worse.
4. **Honors the no-delete rule.** Because new service is on separate hardware, there's no need to delete or repurpose anything on rishi-1/2/3 during or after the build.
5. **Post-cutover, we keep rishi-1/2/3 alive 90+ days** as a read-only fallback. Only after Rishi explicitly approves decommissioning — per item — do we release them back to Saikat.

**Reused existing team infrastructure (integrate, never replace, never delete):**

All team infrastructure is indexed at **`dashboard.yral.com`** — this is the canonical team dashboard where every tool below is linked. When setting up the new service, start by visiting dashboard.yral.com to confirm access to all of these. If missing access, ask Saikat on the team chat channel.

| Asset | URL | How the new service uses it |
|---|---|---|
| **Team Dashboard** | `dashboard.yral.com` | Master index of all team infra. Open this first when onboarding a new engineer. |
| **Vault (Hashicorp)** | `vault.yral.com` (owned by Naitik) | **Canonical secret store.** Every secret — DB passwords, `GEMINI_API_KEY`, Claude API keys, S3 creds, Sentry DSN, Stripe keys, webhook secrets — lives here. Never in code. Never in Docker images. Never in environment files checked into git. Services fetch at runtime via env vars injected from Vault. |
| **Sentry (self-hosted)** | `sentry.rishi.yral.com` (on rishi-3) | All new services emit errors, traces, performance data, exceptions to this Sentry. Tagged with `service=<service-name>`. **No new Sentry instance** unless Rishi explicitly approves (see No-Delete covenant). |
| **Beszel** | `beszel.yral.com` | Install Beszel agents on rishi-4/5/6 when provisioned. One pane of glass for CPU/RAM/disk across all servers including the new ones. |
| **Uptime Kuma** | `status.yral.com` | Register every new service's `/health` endpoint as a monitor. Page-worthy alerts route to the team chat. |
| **Wildcard DNS** | `*.rishi.yral.com` | Each new service gets its own subdomain. For production cutover, `chat.yral.com` DNS-flips from rishi-1/2 → rishi-4/5. |
| **Hetzner S3** | `hel1.your-objectstorage.com/rishi-yral` | Reuse existing bucket for media (images, voice notes). Creds via Vault. |

### 1.5.1 Secrets management — mirror the existing template's pattern

🚨 **Correction from earlier draft:** we do **NOT** push everything into Vault. We **mirror the pattern already proven by `yral-rishi-hetzner-infra-template`** — which Rishi and I built together — where most secrets live in **GitHub Secrets** and only shared team secrets (like a notification key used by many services) live in Vault. Vault is a **read-only lookup** for things already there; we don't store new things there.

**The rules:**

1. **GitHub Secrets is the primary secret store per-service.** Each service's secrets (DB passwords, LLM API keys, S3 creds, third-party API keys) are set via `gh secret set` during the `new-service.sh` spawn script — same pattern as the existing template.
2. **Vault (`vault.yral.com`) is the team's shared-secrets lookup** — used only for secrets that already live there and are shared across multiple services. Example from our chat-ai Python build: the notification API key came from Vault (because the metadata service owns it and multiple services consume it). We fetch these at runtime via `infra.get_secret("path/key")` (the pattern baked into the existing template).
3. **Do NOT push new secrets into Vault "just to be safe."** If it's a secret only this service uses, GitHub Secret. If it's a team-shared secret already in Vault, read from Vault. This keeps Vault from becoming a junk drawer and respects Naitik's ownership.
4. **Secrets are fetched at runtime via environment variables.** GitHub Actions CI writes them as Swarm secrets during deploy; containers read env vars at startup. Restart container = picks up rotated secret on next start. This is what the existing template already does — we just inherit it.
5. **Nothing secret goes into Docker images.** Images built in CI without secrets, pushed to GHCR, run on servers where env vars are injected at container start. Swarm `docker service update` rotates secrets without rebuilding images.
6. **Nothing secret goes into git.** `.env.example` with placeholders ✔. Real `.env` git-ignored ✔. CI uses `${{ secrets.* }}` references; the actual values live in GitHub repo/org secret store. (Same as existing template — no change.)
7. **Rotation:** when we need to rotate a secret, we `gh secret set SECRET_NAME` → redeploy → new containers pick up new value. Old containers die. Zero downtime.
8. **Local development:** `cp .env.example .env`, fill in dev values you generated locally (not prod secrets). Prod secrets NEVER on laptop.
9. **What lives where, concretely:**
   - **GitHub Secrets (per-repo):** `DATABASE_URL_SERVER_1`, `DATABASE_URL_SERVER_2`, `POSTGRES_PASSWORD`, `REPLICATION_PASSWORD`, `GEMINI_API_KEY`, `CLAUDE_API_KEY`, `OPENROUTER_API_KEY`, `AWS_ACCESS_KEY_ID`/`SECRET_ACCESS_KEY` (Hetzner S3), `SENTRY_DSN`, `SSH_PRIVATE_KEY` (for CI → server), `GHCR_TOKEN`
   - **Vault (`vault.yral.com`, shared across services):** things the team already keeps there — e.g., `YRAL_METADATA_NOTIFICATION_API_KEY`, anything Naitik designates as team-canonical. We read, we don't write.
   - **GitHub Org secrets (shared across all our repos):** things identical for every repo — e.g., a common container-registry push token.
10. **Audit:** GitHub logs secret reads per workflow run (visible in Actions log); Vault logs its own access. Monthly, we skim both for anomalies.

**Source code to inherit from** (the existing `yral-rishi-hetzner-infra-template`):
- `scripts/new-service.sh` — the `gh secret set` block that populates secrets from `~/.ssh/`, macOS Keychain (S3 creds), and openssl-generated DB passwords
- `app/infra.py` (if that's where `infra.get_secret("path/key")` lives) — the Vault read helper
- The Swarm `docker-compose.yml` pattern where secrets are mounted as Docker Swarm secrets, exposed to containers as env vars or /run/secrets/ files

**What's new in v2:** nothing structural. We just add service-specific GitHub Secrets per new service (LLM keys, Langfuse token, etc.) and keep using Vault for whatever was already there. The template does the heavy lifting — we don't re-invent.

### 1.5.1.5 Naming conventions — explicit English, with ownership + version stamp

🚨 **Rishi's hard naming rule:** every service, repo, Docker image, Swarm stack, database, subdomain, and top-level identifier must:

1. **Include `rishi`** — so it's unambiguous which cluster/owner this belongs to (distinct from Ravi's services, shared team services, etc.).
2. **Include `chat-ai-v2`** — so it's unambiguous this is Version 2.0 of the chat-ai stack (distinct from the v1 Python `yral-rishi-chat-ai` still running).
3. **End with an explicit English purpose** — anyone reading the service name in a log line should know what it does.

**Naming template:** `yral-rishi-agent-<explicit-purpose-in-english>`

**Examples of correct names:**

| Good ✅ | Bad ❌ | Why |
|---|---|---|
| `yral-rishi-agent-conversation-turn-orchestrator` | `chat-orch` | Only the good one tells you the owner, version, and purpose at a glance. |
| `yral-rishi-agent-soul-file-library` | `prompts` | "Soul File" matches DOLR product vocab; "library" signals it's a versioned store, not a runtime thing. |
| `yral-rishi-agent-user-memory-service` | `mem-svc` | English reader gets it instantly. |
| `yral-rishi-agent-new-service-template` | `template-v2` | Explicit about which template's v2. |

**Naming applies to:**
- GitHub repo names: `dolr-ai/yral-rishi-agent-<purpose>`
- Docker image tags: `ghcr.io/dolr-ai/yral-rishi-agent-<purpose>:<sha>`
- Docker Swarm stack names: `yral-rishi-agent-<purpose>` (short enough for Swarm's 63-char limit — we must verify each name before accepting it)
- Postgres schemas inside the new DB: `agent_<purpose>` (underscores because Postgres prefers them in identifiers)
- Subdomains: `<purpose>.rishi.yral.com` (these are under the existing wildcard; don't repeat `rishi` in the subdomain since the wildcard already encodes it) — e.g., `agent.rishi.yral.com`, `creator-studio.rishi.yral.com`, `soul-file-library.rishi.yral.com`
- Environment variables inside services: `UPPER_SNAKE_CASE_EXPLICIT_ENGLISH` — e.g., `GEMINI_API_KEY`, `SOUL_FILE_LIBRARY_DATABASE_URL`
- Function names in code: `verb_object_qualifier` in English — e.g., `fetch_soul_file_for_influencer`, not `get_sf`
- Database table and column names: English words, no abbreviations — `ai_influencers` ✅, `conversation_turns` ✅, not `convos` or `ai_inf`

**Swarm 63-character limit check:** Docker Swarm stack names and service names have a 63-character limit. Template names like `yral-rishi-agent-conversation-turn-orchestrator` are 53 chars — fits. But `yral-rishi-agent-<very-long-explicit-purpose>` could overflow. Every name added to the plan must be verified under 63 chars, otherwise shortened to meaningful English that still includes the `yral-rishi-agent` prefix. The `new-service.sh` spawner should refuse names over 39 chars after `yral-rishi-agent-` to leave room for secret suffixes.

---

### 1.5.2 Database backup & recovery — three-layered, never-lose-data strategy

Postgres data for the new service is irreplaceable (Soul Files, user memories, influencer catalog, payments). Losing it would be catastrophic. The strategy below is designed so **no single failure can destroy data**:

**Layer 1 — Real-time replication (HA within cluster)**
- Patroni runs a leader + 2 synchronous replicas across rishi-4/5/6
- Writes commit only after being acknowledged by at least 1 replica (sync quorum)
- If the leader crashes, a replica is promoted within 30 seconds (Patroni auto-failover via etcd)
- RPO (data loss window): 0 seconds. RTO (time to restore service): <1 minute

**Layer 2 — Continuous WAL archiving + PITR to Hetzner S3**
- Every Postgres WAL segment is streamed to Hetzner S3 bucket `rishi-yral-wal-archive` via pgBackRest or WAL-G
- Allows **point-in-time recovery** to any moment in the last 7 days
- Also enables **database branching**: spin up a staging DB restored to any past timestamp for testing (without touching prod)
- RPO: ~1 minute. Used for: oops-I-dropped-a-table, data corruption, debugging historical state

**Layer 3 — Off-site logical backups (disaster recovery)**
- Daily `pg_dump` → Hetzner S3 bucket `rishi-yral-daily-backup` (30-day retention)
- Weekly `pg_dump` → **different provider** S3 (Backblaze B2 or AWS S3, "offsite") in a different geographic region (3-month retention)
- Monthly encrypted `pg_dump` → cold long-term storage (1-year retention, compliance-grade)
- RPO: 24 hours (worst case). Used for: Hetzner datacenter loss, account compromise of primary S3, compliance audit

**Verification (this is the part nobody does — we will):**
- **Weekly automated restore drill** — CI job restores yesterday's backup into a throwaway Postgres, runs sanity queries, destroys it. If restore fails, page the team.
- **Quarterly disaster-recovery simulation** — Rishi (or whoever) restores from the off-site backup to a fresh server, verifies data, documents any issues. If DR takes more than 4 hours, that's a bug we fix.

**Database branching (for dev/test/staging):**
- Branching = creating a cloned DB at a specific point-in-time from Layer 2 WAL archive
- Use case: "I want to test a schema migration against last-Tuesday's data" → restore from WAL archive into a dev Patroni instance → test migration → destroy
- Tooling: pgBackRest or WAL-G's restore commands, scripted
- This means **dev/staging always has real-shape data** (with sensitive fields redacted for safety) and we never test migrations blind

**Cost estimate for backups at 10K DAU:**
- Postgres working set: ~10-50 GB first year
- WAL archive to Hetzner S3: ~30 GB/month = €0.60/month
- Daily backups (30 days): ~1.5 TB total = €30/month
- Off-site weekly backups (3 months, Backblaze B2): ~500 GB = ~$3/month
- Monthly cold archive (1 year): ~50 GB/year = negligible
- **Total backup cost: <€50/month.** Insurance against catastrophic data loss. Obvious ROI.

---

## 1.6 The V2 Template — the beating heart of the whole build

🚨 **Rishi's preference (strong):** build a NEW template first (sibling to the existing `yral-rishi-hetzner-infra-template`, NOT a replacement — **the old one stays untouched per the No-Delete covenant**). All 13 new services are spawned from this new template. After each service is built, review what we learned and fold useful learnings back into the template so future services benefit.

**Name of the new template:** `yral-rishi-agent-new-service-template` (lives at `github.com/dolr-ai/yral-rishi-agent-new-service-template`)

### 1.6.1 Why a template-first approach is the right call for this plan

1. **ADHD-friendly mental model.** You read ONE template thoroughly, understand how a service is built, and then every other service is just variations of that. You never have to re-understand "how do I deploy something" — you understand it once, then 13 services are built by copying.
2. **1-command service spawn.** `bash scripts/new-service.sh --name yral-rishi-agent-user-memory-service` → 5 minutes later, service is live, wired to Sentry, registered in Uptime Kuma, secrets set, CI green, health endpoint responding.
3. **One place to improve.** You fix something once in the template (e.g., add structured logging, improve health-check format, add a retry policy). Next service spawned has it automatically. No per-service fix-ups.
4. **A learning curriculum.** The template IS the curriculum for a new engineer (or Yoa, or you re-learning in 3 months). Docs + code + Dockerfile + CI yaml + secret list all in one repo.
5. **Consistent patterns → consistent observability.** Every service exports Sentry, Langfuse traces, `/health` endpoint, structured logs, Prometheus metrics in the exact same shape. Dashboards work uniformly. Debugging any service uses the same mental model.

### 1.6.2 What to inherit from the existing `yral-rishi-hetzner-infra-template`

The new template is NOT built from scratch. We start by copying the existing one and evolving it. Concrete inheritances:

- `scripts/new-service.sh` (the 457-line spawner) — inherit wholesale, then extend. Already handles: GitHub repo creation, secret population from `~/.ssh/` and Keychain, DB password generation via `openssl`, `gh secret set` for each secret, CI-watch to verify first green run, health-check verification.
- `project.config` (single source of truth for project name, domain, DB) — inherit the pattern; add new fields (e.g., `LLM_PROVIDER_DEFAULT`, `SOUL_FILE_LAYER_PARTICIPATION`).
- `servers.config` — inherit. New template points at rishi-4/5/6 (new cluster) instead of rishi-1/2/3. Same structure.
- Swarm deploy CI workflow — inherit. Canary deploy pattern: rishi-4 first, verify health, then rishi-5, rollback on failure.
- Caddy reverse proxy snippets — inherit. Per-project decoupled snippets in `/etc/caddy/conf.d/`.
- Patroni HA Postgres setup — inherit. New cluster on rishi-4/5/6.
- `strip-database.sh` (removes DB layer for stateless services) — inherit. Many new services are stateless and don't need a Patroni cluster.
- Per-project Docker overlay network — inherit.
- Backup workflow (daily pg_dump to S3 via GitHub Actions) — inherit and **extend** with the 3-layered strategy (Section 1.5.2).
- Documentation standards (DEEP-DIVE, READING-ORDER, CLAUDE, RUNBOOK, SECURITY) — inherit, mandatory, as your memory already dictates.

### 1.6.3 What the NEW template adds (vs. the old one)

Things we didn't have in the old template that we need for every v2 service:

- **Sentry DSN wiring pre-configured.** Every new service emits to `sentry.rishi.yral.com` out of the box.
- **Langfuse client middleware.** Every new service that calls an LLM auto-traces every call to self-hosted Langfuse on rishi-4. Zero per-service setup.
- **Redis client baked in.** Every new service gets a Redis client for session cache, rate limiting, and Redis Streams event emit/consume. One line of config.
- **Event-stream helpers.** `emit_event("message.sent", {...})` and consumer-group subscription helpers — so every service talks via Redis Streams uniformly.
- **Feature-flag client.** Postgres-table-based feature flags, consistent API across services, ready to use.
- **Health endpoint format.** Uniform `/health` returning `{status, version, uptime_seconds, dependencies: [...]}` — Uptime Kuma auto-understands it.
- **Structured JSON logs.** Out of the box. Can be piped to any log aggregator later.
- **Tracing headers propagation.** Request IDs flow through all services so a single user turn is traceable end-to-end.
- **Latency baseline integration.** Every request's p50/p95 is recorded and comparable against the baseline doc (Section 2.8).
- **LLM client abstraction.** `llm_client.chat(messages, model=...)` wraps Gemini / Claude / OpenAI / OpenRouter / self-hosted behind one interface. Per Section 2.5's LLM-agnostic principle.
- **MCP tool-runtime helpers.** Easy to wire a service up as an MCP tool consumer.
- **Postgres schema-per-service bootstrapping.** Each service has its own schema; the template handles DB bootstrap + migration.
- **Soul File Coach hooks.** If the service needs to read Soul Files, the template provides the client; no re-implementing.
- **Safety filter middleware.** Pre/post request filters for moderation, rate limits, etc. — on/off via config.
- **Pre-flight check before first deploy.** Template runs a checklist: all secrets set? Sentry DSN valid? Postgres reachable? Swarm manifest lints? Prevents broken first deploys.

### 1.6.4 Template evolution workflow — learn once, reuse forever

After each new service is built and running, Rishi + I do a 15-minute retrospective with three questions:

1. **Did we copy-paste code while building this service?** If yes, that code belongs in the template.
2. **Did we have to fix a deployment issue more than once?** If yes, the fix goes in the template.
3. **Is there a documentation gap we hit?** If yes, update the template's `CLAUDE.md` / `TEMPLATE.md`.

Each update to the template ships as a version bump (`v1.0`, `v1.1`, ...). Existing services keep their original template version (no forced upgrades). New services always spawn from the latest.

**Example expected evolutions:**
- After `yral-rishi-agent-user-memory-service`: add pgvector setup to the DB bootstrap layer of the template.
- After `yral-rishi-agent-content-safety-and-moderation`: add a "safety classifier client" to the template for future services that need it.
- After `yral-rishi-agent-creator-studio`: add an "LLM conversation session manager" helper (for multi-turn LLM interactions) to the template.
- After `yral-rishi-agent-proactive-message-scheduler`: add a cron-job primitive to the template (GitHub Actions cron → template endpoint).

### 1.6.5 Template is NEVER copied; services FORK from template

When a new service is spawned via `new-service.sh`, it doesn't copy-paste files — it **pulls from the template's latest tagged release** and customizes per `project.config`. Updates to the template propagate to new services by default. This prevents drift.

Existing services are NOT auto-updated when the template evolves. If we want an older service to adopt a new template improvement (e.g., a new logging format), we do it explicitly — a PR that cherry-picks the template's new pattern.

---

**The No-Delete Covenant** — I, Claude, agree to:

- **Never** `rm`, `git push --force`, `docker stack rm`, `helm uninstall`, DNS delete, Caddy route remove, GitHub repo archive, or any other destructive operation on any existing service, repo, config file, DNS record, server, Docker stack, Swarm secret, database, or GitHub secret without Rishi's **explicit per-item typed approval**.
- **Never** assume "we built something better, old one can go" — no, it stays until Rishi says otherwise, per item, in writing.
- **Always** prefer additive changes: new repos, new DNS records, new Swarm stacks, new secrets, new domains. The old ones sit undisturbed.
- **When unsure**, ASK. Cost of asking = ~20 seconds. Cost of deleting wrong thing = incident + lost trust.
- **When the plan needs to remove something** (e.g., during cutover, we'd stop routing traffic to old service), I describe the change, explain why, and wait for explicit approval before executing.

**What "delete" covers** (non-exhaustive — when in doubt, ask):
- Removing a git repo / archiving a repo / deleting branches
- Removing a Caddy route / DNS record / subdomain
- Removing a Docker Swarm stack or service
- Removing a Swarm secret or GitHub secret
- Dropping a Postgres database, schema, table, or column
- Removing a file on any server (even "just a log" — ask)
- Removing a Sentry project or Beszel monitor
- Removing a Hetzner server
- Removing any GitHub Actions workflow or CI/CD config
- Removing a memory file in `~/.claude/projects/`

---

---

## 2. The Greenfield Architecture (the big pivot)

This section is the NEW center of the document. Everything downstream (Plans A-H, catalogue, roadmap) still describes the capabilities you want — but this section defines the architectural vehicle that will deliver them.

### 2.1 What "1000× better" actually means (vision, concrete)

**User experience:**
1. **Streaming-first.** First token to user in <200ms. Response streams as it's generated — user sees thinking, not "…typing" for 3 seconds then a wall of text.
2. **Remembers forever.** Bot recalls the right fact at the right moment. Tiered memory (session / episodic / semantic / profile / vector) — no more 10-message window.
3. **Sounds human.** Short, punchy, asks questions back, stays in character. Global guardrails + per-archetype voice + per-bot personality + few-shot examples.
4. **Has initiative.** Bot texts first. Morning check-ins, evening reflections, reactions to skipped workouts. Tunable frequency per user.
5. **Multi-modal.** Bot sends voice notes, images, even short videos — generated in a consistent style for that bot.
6. **Actually does things.** Bot logs meals, sets reminders, tracks goals, fetches weather/news/ephemeris. Tool/skill system.
7. **Different bots feel different.** Not just different system prompts on one model — different models + different tool sets + different memory depths per archetype.
8. **Safe at the edges.** Crisis detection, moderation, age gating, consent flows. Non-negotiable.

**Creator experience:**
1. **5-minute bot creation** that produces a rich bot, not a one-word slop. Structured intake, reference upload, instant preview.
2. **Prompt Coach** — creator chats with an LLM that progressively tunes their bot. See old vs. new side-by-side, deploy with one tap.
3. **Creator analytics** — how is my bot doing? who loves it? what should I change? AI-generated recommendations.
4. **Monetization** — approve private image/voice requests, tip jar, earn per unlock. Real-time earnings.
5. **Skill marketplace** (Phase 3) — install plug-ins for your bot (astrology, fitness lookups, etc.). Eventually: write your own.

**Platform experience:**
1. **Eval-driven.** Every change tested offline against held-out prompts before shipping. No more "tweak and pray."
2. **Shadow traffic.** New service runs in shadow next to old — same requests, compare outputs, catch regressions.
3. **Meta-AI advisor.** Daily LLM-generated "top 3 things to do today" based on metrics + advisor dashboards.
4. **Full observability.** Every turn traced (input, memory fetched, model called, tokens, latency, cost, output). Searchable.
5. **Per-turn cost visibility.** Know what each user costs per day. Unit-economics controls baked in.

### 2.2 Orchestration decision

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Docker Swarm** (current) | You know it. Your template uses it. Simple HA. No new learning cost. | Minimal ecosystem. Weaker at 20+ services. No autoscaling. | ✅ **Stay on Swarm.** |
| **K3s** (lightweight k8s) | k8s ecosystem, Helm, operators, autoscaling. | Learning curve. More moving parts. New ops playbook. | Defer. Revisit at 100K DAU or service count >20. |
| **Nomad** (HashiCorp) | Simpler than k8s; fits Vault ecosystem. | Smaller community. Less AI-ecosystem tooling. | Defer. Not obviously better than Swarm for our scale. |
| **Full K8s** | Industry standard. Infinite ecosystem. | Overkill for 10K-100K DAU and 13 services. | ❌ Not now. |

**My strong recommendation: stay on Docker Swarm** — for the new servers rishi-4/5/6 too. Reasons:
1. You already know it. Learning another orchestrator costs months.
2. Your template and `new-service.sh` already target Swarm. Zero template changes needed.
3. 13 services × 3 replicas × 3 nodes fits comfortably in Swarm without autoscaling.
4. If we hit a limit, migration to K3s is feasible because Dockerfiles and images stay the same.

**Justification for Saikat:** same stack keeps ops simple; new cluster is operationally identical to existing; future migration path to k8s is preserved via Docker images.

**Proxy decision:** **continue with Caddy** on the new cluster (rishi-4/5). Same reasoning — proven, simple TLS, your template integrates. Alternative considered: Traefik (more features, more complexity); HAProxy (for raw perf; we don't need it at this scale).

### 2.3 Service granularity decision

The old plan had 75 services. For a solo-ish team, **that's too many**. Greenfield approach: **~13 purposeful services** with **explicit English names** (per your constraint). Split further only when a service has >1 team.

**The 13 core services of the new platform** — all named `yral-rishi-agent-<explicit-english-purpose>` per Section 1.5.1.5. Names are long; that's the point. Every name fits under Swarm's 63-char limit (verified below).

| # | Service name (explicit, long, unambiguous) | Chars | Role | Stateful? | Subdomain |
|---|---|---|---|---|---|
| 1 | `yral-rishi-agent-public-api` | 32 | Public HTTPS API + WebSocket/SSE streaming. Auth. Rate limit. Routes to orchestrator or creator-studio. | Stateless | `agent.rishi.yral.com` (until cutover; then DNS-flip to `chat.yral.com`) |
| 2 | `yral-rishi-agent-conversation-turn-orchestrator` | 53 | Runs a single chat turn end-to-end: fetch context → compose Soul File → call LLM → stream → update state. THE brain. | Stateless | internal |
| 3 | `yral-rishi-agent-soul-file-library` | 40 | Layered Soul File registry (Global / Archetype / Per-Influencer / Per-User-Segment), versioned, composer, Tara Template engine. | Postgres | internal |
| 4 | `yral-rishi-agent-user-memory-service` | 42 | Tiered memory: session (Redis), episodic events, semantic facts, user profile, embeddings (pgvector). | Postgres + Redis | internal |
| 5 | `yral-rishi-agent-skill-runtime` | 42 | Tool/skill registry + sandboxed execution. Native MCP protocol from day 1. | Postgres | internal |
| 6 | `yral-rishi-agent-proactive-message-scheduler` | 50 | Scheduler + triggers + planner + dispatcher + throttler. Authors bot-initiated pings. | Postgres | internal |
| 7 | `yral-rishi-agent-media-generation-and-vault` | 49 | Voice synthesis, image generation (creator-styled), content vault, consent/safety gates. | Postgres + S3 | internal |
| 8 | `yral-rishi-agent-creator-studio` | 37 | Soul File Coach, bot editor, creator analytics, earnings dashboard, bot quality scorer. | Postgres | `creator-studio.rishi.yral.com` |
| 9 | `yral-rishi-agent-content-safety-and-moderation` | 53 | Moderation (pre + post), crisis detection, NSFW classification, age gate, CSAM detection. Mandatory. | Stateless (models loaded) | internal |
| 10 | `yral-rishi-agent-events-and-analytics` | 43 | Event pipeline (Redis Streams), metrics warehouse, dashboards, anomaly detection, cohort analysis. | Postgres | `metrics.rishi.yral.com` |
| 11 | `yral-rishi-agent-meta-improvement-advisor` | 47 | Meta-AI: daily improvement recommendations, hypothesis generator, auto-experimenter. | Postgres | `advisor.rishi.yral.com` (private) |
| 12 | `yral-rishi-agent-payments-and-creator-earnings` | 53 | Subscriptions, micropayments, creator payouts, tip jar, 70/30 revenue split logic. | Postgres (audit-grade, immutable append) | internal |
| 13 | `yral-rishi-agent-influencer-and-profile-directory` | 55 | AI influencer catalog, profile metadata (Human + AI), follow/unfollow, "Switch Profiles", "Chat as Human" toggle. Preserves ALL existing influencers. | Postgres | internal |

**Plus the template repo:** `yral-rishi-agent-new-service-template` (43 chars) — the paved road. See Section 1.6.

**GitHub org convention:** all repos live at `github.com/dolr-ai/<service-name>`. Example: `github.com/dolr-ai/yral-rishi-agent-soul-file-library`.

**Container registry:** `ghcr.io/dolr-ai/<service-name>:<git-sha>`.

**Postgres schemas (underscores, since Postgres identifiers prefer them):** `agent_soul_file`, `agent_user_memory`, `agent_conversation`, etc. Each service owns one schema inside the shared Patroni cluster on rishi-4/5/6.

**Why Service #13 is new vs. prior plans:** your context doc makes clear that Human↔AI and Human↔Human chat are the same system from day 1, and that AI influencers must persist. The influencer/profile directory needs to be an explicit service, not an implicit table in chat-api.

**Plus 3 infrastructure pieces (first-class, installed before services):**
- **Redis Cluster** — session cache, pub/sub, rate limiter, Redis Streams for events (new dependency; install on rishi-4/5/6 as a Swarm service)
- **Postgres Patroni HA** — one cluster on rishi-4/5/6, schema per service (same architecture as existing template, fresh cluster)
- **Hetzner S3** — reuse existing bucket `rishi-yral`

### 2.4 Target architecture diagram

```
                            ┌──────────────────────────────┐
                            │      Mobile App (YRAL)       │
                            └──────────────┬───────────────┘
                                           │ HTTPS + WSS
                                           ▼
 ┌───────────────────────────────────────────────────────────────────┐
 │                         yral-chat-api                              │
 │   Auth · Rate limit · WebSocket/SSE streaming · Request routing    │
 └──────────────────┬────────────────────────┬───────────────────────┘
                    │                        │
         chat turn  │                        │ creator/media/billing
                    ▼                        ▼
 ┌──────────────────────────────┐  ┌──────────────────────────────┐
 │      yral-orchestrator       │  │    yral-creator-studio       │
 │  (the brain - runs a turn)   │  │  Prompt Coach · Bot Editor   │
 └─┬──────┬──────┬──────┬──────┬┘  └─────┬───────────┬────────────┘
   │      │      │      │      │         │           │
   ▼      ▼      ▼      ▼      ▼         ▼           ▼
┌──────┐┌────┐┌────┐┌──────┐┌──────┐┌────────┐┌───────────┐
│prompt││mem ││tool││safety││media ││billing ││ analytics │
│system││ ory││runt││      ││      ││        ││           │
└──┬───┘└─┬──┘└─┬──┘└──┬───┘└───┬──┘└───┬────┘└─────┬─────┘
   │      │    │      │        │        │           │
   │      │    │      ▼        │        │           │
   │      │    │  ┌─────────────────┐   │           │
   │      │    │  │   LLM Routing   │   │           │
   │      │    │  │  Gemini/Claude/ │   │           │
   │      │    │  │  GPT/self-host  │   │           │
   │      │    │  └─────────────────┘   │           │
   │      │    │                        │           │
   └──────┴────┴────────────────────────┴───────────┘
                          │
                          ▼
          ┌───────────────────────────────┐
          │ Postgres HA (Patroni, schema/ │
          │ service) + Redis + S3 + pg    │
          │ vector + Redis Streams (evts) │
          └───────────────────────────────┘
                          │
                          ▼
                ┌────────────────────┐
                │  yral-advisor      │
                │ (Meta-AI that tells│
                │  Rishi what to do) │
                └────────────────────┘

                 ┌────────────────────────┐
                 │   yral-proactive       │
                 │   (fires bot-initiated │
                 │    messages via api)   │
                 └────────────────────────┘
```

### 2.5 Technology stack decisions (recommendations)

| Layer | Choice | Why |
|---|---|---|
| **Language** | Python 3.12 + FastAPI + asyncio (continue current). Sprinkle Go or Rust ONLY for hot paths that prove slow (e.g., streaming proxy, vector search) | Continuity; massive AI ecosystem; you already know it; 90% of your workload is I/O-bound (LLM calls) where Python's async is fine |
| **LLM providers** — *LLM-agnostic by design* | **Providers are pluggable.** Day 1: Gemini 2.5 (primary, cheap, baseline match for current service). Day 30+: add Claude (deep reasoning, safety-critical turns), GPT-5 (creative), OpenRouter (NSFW). Day 90+: self-hosted option (see below). | Being LLM-agnostic is a **first-class architectural goal.** The orchestrator talks to an internal `llm-client` abstraction; switching providers is a config change, not a rewrite. Avoids vendor lock-in, enables cost optimization, enables self-hosting ramp. |
| **Self-hosted LLM (target state, Month 6+)** | Self-host best open-weight models — Llama 3.3 70B, Qwen 2.5 72B, Mistral Large, DeepSeek V3, etc. — via vLLM or TGI on dedicated GPU servers (Hetzner GPU or other cloud). Possibly fine-tuned on YRAL conversation data. | **This is Rishi's explicit long-term goal.** Self-hosting gives: (a) full privacy for user chats (no third-party sees messages), (b) cost control at 100K+ DAU, (c) fine-tunability for YRAL-specific response style (Soul File baked into weights), (d) zero rate-limits. Requires GPU allocation from Saikat (H100 or A100 class, or Hetzner's new GPU offerings). Adds ops complexity (GPU failover, model updates, capacity planning). Treat as a strategic milestone: ship it when ready, not earlier. |
| **Primary DB** | Postgres 16 + Patroni HA (you already have this) + pgvector extension | One cluster, many schemas; proven pattern. pgvector is enough up to ~50M vectors; if you outgrow, add Qdrant |
| **Cache / Pub-Sub / Queue** | Redis Cluster (add this, you don't have it yet) — serves as session cache + rate limiter + Redis Streams for events + pub/sub for real-time | Adds one dependency but replaces need for Kafka/NATS/Celery at your scale. Single pane of glass |
| **Real-time to client** | Server-Sent Events (SSE) for streaming LLM output; WebSocket for presence/typing | SSE is simpler than WS for unidirectional streams; use WS only where bidirectional is needed |
| **Object storage** | Hetzner S3 (you have) | No change |
| **Search / Analytics store** | Postgres + ClickHouse (add later if needed for analytics scale) | Postgres handles everything ≤100K DAU. Add ClickHouse if analytics queries slow down Postgres |
| **Observability** | Sentry (errors — you have), Grafana + Prometheus (metrics — you have Beszel, upgrade this), Langfuse or self-hosted equivalent (LLM traces — NEW) | **Langfuse is critical for LLM observability** — per-turn traces, prompt/response inspection, cost tracking, eval runs. Can self-host on your servers. |
| **Eval harness** | Langfuse + custom eval runners, or Promptfoo | Run 200-prompt eval suite on every prompt-registry change. No prompt change ships without it. |
| **Deployment** | GitHub Actions → Docker Swarm blue-green per service (continue template pattern) | Same as today. Per-service rollbacks via Swarm `service update --rollback`. |
| **Feature flags** | Unleash (self-host) or custom Postgres-table-based flags | Every new feature ships behind a flag; progressive rollout 1% → 10% → 100%. |

### 2.6 Data architecture

**Schema-per-service within one Patroni cluster on rishi-4/5/6** (explicit schema names):

- `soul_file.*` — Soul File layers (global, archetype, influencer, user_segment), versions, active_flags, audit_history
- `user_memory.*` — session_cache_index (data in Redis), episodic_events, semantic_facts, user_profiles, embeddings (pgvector)
- `conversation.*` — conversations, messages (supports ai_chat, human_chat, chat_as_human modes), unread_counts, typing_state
- `influencer.*` — ai_influencers (migrated from existing `ai_influencers` table; all data preserved), personality, model_selection, follower_count, creator_user_id
- `human_profile.*` — human_user_profiles (bio, avatar, followers, following), subscribe_enabled, talk_to_me_enabled
- `skill.*` — skill_registry, skill_installs_per_influencer, skill_execution_logs, mcp_endpoints
- `proactive.*` — scheduled_sends, triggers, dormancy_flags, streaks, throttle_state
- `media.*` — content_requests, content_vault, consent_log, generation_jobs, creator_reference_images
- `creator_studio.*` — soul_file_coach_sessions, bot_quality_scores, creator_analytics_rollups
- `safety.*` — moderation_events, crisis_flags, age_verifications, consents, banned_words_per_locale
- `analytics.*` — events (Redis Streams primary; batched into Postgres daily), cohort_rollups, kpi_snapshots
- `billing.*` — chat_subscriptions (the ₹9/24hr unlock), per_influencer_paywall_counters (the 50-message limit), transactions, creator_payouts (audit-immutable, append-only)
- `admin.*` — feature_flags, experiments, advisor_recommendations

**Why schema-per-service (not DB-per-service):** operational simplicity. One Patroni cluster, one backup, one connection pool. Services read their own schemas + have **explicit read-only views** into other schemas (e.g., orchestrator reads `user_memory.*` and `soul_file.*`). Writes go through service APIs, not cross-schema writes.

**Critical preservation rule:** the `influencer.ai_influencers` table gets **complete data migration** from the existing `ai_influencers` table (ID preservation, creator_user_id preserved, Soul File content preserved as Layer 3). Existing mobile deep-links to influencer IDs must still work post-cutover.

**All chat data MUST migrate (locked 2026-04-24, reversed from earlier "discardable" position):** `conversation.conversations` + `conversation.messages` + read states + unread counts ALL port from chat-ai to v2. Rishi: *"losing chat data would be the wrong approach — it will hamper user experience for current users."* V2 schema is fully greenfield (not constrained to match chat-ai); ETL transforms old rows into new shape during migration. No row dropped. Applies to LOCAL TESTING (immediate, against a snapshot of chat-ai data pulled per CONSTRAINTS A14) AND PRODUCTION CUTOVER (when Rishi says).

**Event flow via Redis Streams:** Every significant event (message.sent, message.received, payment.completed, influencer.created, crisis.flagged, chat_as_human.toggled) → one Redis Stream entry. Downstream services consume via consumer groups. No polling, no N² service mesh.

### 2.7 The single most important design choice: the Orchestrator Turn Lifecycle

This is the hot path. Every user message goes through this. It's the piece to get right.

```
User sends message (HTTPS POST /v2/chat/conversations/{id}/messages)
          │
          ▼
   yral-chat-api receives, auth, rate-limit, opens SSE stream for response
          │
          ▼
   yral-orchestrator takes over:
   ┌─────────────────────────────────────────────────┐
   │ Turn Lifecycle (target: first token <200ms)      │
   │                                                  │
   │ 1. Persist user message (async fire-and-forget)  │
   │ 2. IN PARALLEL:                                  │
   │    ├─ fetch session memory (Redis, <5ms)         │
   │    ├─ fetch semantic facts (Postgres, <20ms)     │
   │    ├─ vector search recent topics (pgvector <50ms)│
   │    ├─ fetch bot definition + prompt layers       │
   │    └─ safety pre-filter on user msg              │
   │ 3. Compose final prompt (layered, cached)        │
   │ 4. Route to model (archetype + turn-type aware)  │
   │ 5. Stream tokens FROM LLM → SSE to user          │
   │    In parallel: safety post-filter on stream     │
   │ 6. Persist response + metadata + trace           │
   │ 7. Emit events (Redis Streams):                  │
   │    ├─ message.created (for analytics/memory-ext) │
   │    ├─ turn.completed (for cost/latency tracking) │
   │    └─ memory.candidate (for async memory ext)    │
   └─────────────────────────────────────────────────┘

Async workers triggered by events:
   ├─ memory-extractor: parses for facts → memory.semantic_facts
   ├─ memory-consolidator: nightly cleanup/dedupe
   ├─ analytics-rollup: updates KPIs
   ├─ advisor: flags anomalies if this turn was weird
   └─ bot-quality-scorer: grades the response
```

**Why this matters:** streaming + parallelism + async side effects means the user experience is fast and the platform is deeply intelligent at the same time. The current service does none of this — it's serial and blocking.

### 2.7.5 Scale projections & capacity plan — 25K → 1M+ messages/day

🚨 **Rishi's scale constraint** (new): today ≈ 25,000 messages/day; expected in 4-6 months = hundreds of thousands/day, plausibly 1M+/day by Month 12. Every architectural choice in this plan must hold at that scale without re-architecture. Here's the capacity plan:

**Today's baseline:**
- 10K DAU · 25K messages/day · 2.5 messages per active user
- Avg request rate: ~0.3 msg/sec. Peak (evening India hours, say 3× avg): ~1 msg/sec.
- Each message = 2 LLM calls (extract memory async + respond sync) + 5-10 DB writes + 1 Redis event

**Month 6 projection (10× growth):**
- 30-50K DAU · 250K-500K messages/day
- Avg: ~3-6 msg/sec. Peak: ~15-30 msg/sec.
- Postgres writes: ~150 writes/sec peak (trivial for Patroni).
- LLM calls/sec peak: ~30-60. This starts approaching Gemini free-tier rate limits — need paid tier or multi-provider routing.
- Redis Streams: trivial.
- WebSocket concurrent: ~1-3K. Fine on Swarm-managed WebSocket service with sticky sessions.

**Month 12 projection (40× growth):**
- 100K DAU · 1M+ messages/day
- Avg: ~12-15 msg/sec. Peak: ~60-100 msg/sec.
- LLM calls/sec peak: ~120-200. **Need multi-provider routing by this point — no single provider guarantees this without contract.**
- Postgres writes/sec peak: ~600-1000. Patroni on rishi-4/5/6 can handle with read replicas added.
- pgvector embeddings stored: ~10M cumulative. Still works natively; index tuning needed.
- Redis Streams: still fine.

**Capacity-plan triggers — when to scale what:**

| Metric | Threshold | Action |
|---|---|---|
| Orchestrator CPU > 70% sustained | Anytime | Add Swarm replicas (horizontal scale — stateless service; one Swarm command). |
| Postgres write IOPS > 60% of disk | Month 6-9 expected | Add read replica on additional server OR upgrade disk on rishi-4/5/6. |
| Postgres table > 100GB for hot tables | Month 12+ | Introduce table partitioning by month on `messages` and `events` tables. |
| LLM provider rate-limit errors > 1% | Month 6+ expected | Add second provider in rotation (Claude Haiku / OpenRouter) behind the LLM-agnostic abstraction. |
| pgvector search p95 > 100ms | Month 12+ expected | Move vectors to dedicated Qdrant cluster (still behind same interface). |
| Analytics Postgres query slow | Month 9+ expected | Add ClickHouse for events warehouse; keep Postgres for transactional data. |
| Swarm node CPU >70% sustained on any node | Month 6+ expected | Ask Saikat for rishi-7 (scale-out the cluster). |
| Sentry event volume > free tier limit | Month 6+ | Upgrade Sentry tier OR sample errors. |

**Headroom & reservation strategy:**
- Design every service to horizontally scale to 3 replicas minimum (already required by Swarm HA).
- Postgres connection pool per-service: start at max=10; at 100K DAU, bump to max=50 per service (need pgBouncer to front Postgres by Month 6).
- Redis memory: start at 4GB per node; monitor growth; pre-order bigger RAM nodes if needed.

**Cost model (ballpark, LLM only):**
- Today at 25K msgs/day with Gemini Flash (~$0.30/1M tokens output): ~$5/day = $150/month for LLM. Well within ₹9×20 paying users = ₹180/day = ~$2/day revenue. **Unit economics already tight.**
- Month 6 at 500K msgs/day: ~$100/day LLM = $3000/month. Revenue likely $50-150/day at current conversion. **Must improve conversion OR drop to cheaper model OR self-host for anything that's not conversion-critical.**
- Month 12 at 1M msgs/day: ~$200/day LLM. Self-hosting becomes obvious win for routine turns; reserve paid LLMs for "deep think" moments.
- This is why **LLM-agnostic routing (Section 2.5) is a scale requirement, not a preference.** You cannot run this platform at 1M msgs/day on one paid provider without going bankrupt.

**Backup sizes at scale:**
- 1M msgs/day ≈ 30M rows/month in messages table ≈ ~5GB/month of hot data
- WAL archive at 1M msgs/day: ~10GB/day = 300GB/month — still trivial for Hetzner S3
- Daily pg_dump at 12 months: ~60GB → 30-day retention = 1.8TB Hetzner S3 = €35/month. Still cheap.

**What this projection does NOT require us to change today:**
- Orchestration stays on Docker Swarm (works fine up to ~50 nodes; we'll have 3-6).
- Postgres stays on Patroni (sharding not needed until tens of TB).
- Service count stays at 13 (not 30; horizontal scaling, not decomposition).
- Backups stay on Hetzner S3 primary + Backblaze B2 off-site.

**What we DO change in the plan, given the scale foresight:**
- Build every service stateless-by-default so horizontal scaling is a Swarm CLI command.
- Build the LLM client to support multi-provider routing from day 1 (not as a future feature).
- Build pgBouncer into the template from day 1 (even though not needed at 25K msgs/day; it's trivial to add, impossible to remove gracefully later).
- Design Postgres schemas with partitioning-friendly columns (every big table has `created_at` indexed, ready to partition on).
- Design event-stream consumers to be replicable (one consumer group, many members — horizontal scale-out).

---

### 2.8 Latency Service Level Objective (SLO) — non-negotiable

🚨 **Rishi's hard constraint (UPGRADED 2026-04-24):** v2 MUST be **at least 50% FASTER than Python yral-chat-ai** (which went live 2026-04-23 and is drawing user complaints about slow latency) on user-interactive endpoints. Stretch goal: match world-class AI companion apps (Character.ai, Replika, Talkie — typical TTFT 200-400ms, full message 1-3s). This is the single constraint every architectural decision flows from — if a design choice doesn't help v2 be 50% faster, it doesn't ship.

**What "user-interactive" means (the 50%-faster scope):**
- Chat screen load (conversation list + metadata) — user taps chat tab, sees everything instantly
- Conversation open (load messages + typing state + unread counts) — user taps a bot, history is there
- Send message → first token of streamed response (TTFT) — the hot path
- Send message → full response complete — the full turn
- WebSocket inbox push (new-message delivery) — instant, no polling delay
- Send image / media — within 2× of text-message TTFT

Admin / list-all / ban / search / rarely-used endpoints hold to the softer "≤ baseline" rule (CONSTRAINTS E1) — they aren't worth architecting around.

**Step 1 — Baseline measurement (CONTINUOUS, not one-time; starts Day 1):**
- **Sentry API (pre-authorized 2026-04-24, key at `~/.config/dolr-ai/sentry-api-key` TBC)** pulls `sentry.rishi.yral.com` perf data daily for yral-chat-ai.
- Metrics captured per user-interactive endpoint: p50, p95, p99 for (a) full request completion and (b) first-token latency where applicable.
- For Ravi's Rust `yral-ai-chat`: captured if still serving traffic; if all traffic has moved to Python chat-ai already (confirm via Saikat), the Python baseline alone is the binding number.
- Output: `latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/daily-baseline.csv` — appends one row per day; plotted in `baseline-over-time.md`.
- **World-class reference numbers** (for stretch goal, NOT for hard-target compliance): pulled from published benchmarks / product demos / user reports of Character.ai, Replika, Talkie. Recorded in `world-class-companion-apps-latency-reference.md`.

**Step 2 — Latency budgets (designed against the 50%-faster target):**
- HARD target per endpoint: `v2_p95 ≤ 0.5 × python_chat_ai_p95`
- STRETCH target: within 1.2× of world-class companion app latencies
- Turn-lifecycle budget breakdown (hot-path TTFT, assuming Python chat-ai p95 TTFT ~1500ms → v2 hard target ~750ms p95, stretch ~400ms):
  - Auth + JWT validation (from JWKS cache): <10ms
  - Billing pre-check (Redis cache hit): <5ms
  - Redis session-memory fetch: <5ms
  - Postgres semantic-facts fetch: <20ms (**in parallel with LLM init**)
  - pgvector semantic search: <50ms (**in parallel**; short-circuit if it overruns)
  - Soul File composition from warm Redis cache (composed-string cache): <5ms
  - Anthropic / provider prompt-cache hit on the static prefix (Layers 1-4 of the Soul File): saves ~80-90% of the prefix-tokens contribution to TTFT after the first turn (see Step 4 architectural rule "Stable prompt prefix for provider-side caching")
  - Safety pre-filter on user message: <30ms (**in parallel with prompt build**)
  - LLM first-token time (Gemini Flash): ~200-400ms typical — this is the wall clock; reason Tara's OpenRouter route may need special care
  - Network + framing back to client (SSE): <20ms
- **Non-negotiable architectural rule:** every memory/retrieval/safety step runs IN PARALLEL with LLM-call initialization, never serially before it. If any step is slow, short-circuit it and ship the turn without that enrichment rather than waiting.

**Step 3 — Enforcement (continuous):**
- Every v2 service emits per-request latency to Langfuse + Sentry. Per-turn traces link end-to-end.
- **CI latency gate:** every PR merged to main runs a synthetic load test against a staging instance; fails if any user-interactive endpoint p95 > `0.5 × current_python_chat_ai_p95` for that endpoint.
- Automated hourly comparison in prod: v2 p95 vs yral-chat-ai p95 (via Sentry API). If v2 is slower than `0.5×` target on ANY user-interactive endpoint for 30 consecutive minutes, auto-rollback halves traffic percentage. Rishi's manual approval required to resume.
- Load test before each phase transition: synthetic burst at 100× current QPS, verify p99 still beats `0.5×` target.
- Langfuse per-turn breakdown view shows where time was spent — easy regression diagnosis.

**Step 4 — Architectural safeguards (fail open, fail fast, fail invisible):**
- **Fallback short-circuit:** memory service slow/down → ship turn with no memory enrichment, log warning, baseline latency preserved.
- **LLM provider failover:** Gemini slow (>500ms TTF for 5 turns) → auto-switch to fallback provider (Claude Haiku / OpenRouter) for next N turns; alert Rishi.
- **Streaming makes latency invisible after first token:** user perceives time to first token, not full response time. Every architectural choice should preserve first-token latency over everything else.
- **Pre-warm during user idle:** between user turns, background jobs warm memory caches for likely next user. Next turn starts hot.
- **Never block on a cold dependency.** Every non-critical call has a timeout + graceful fallback path.
- **Stable prompt prefix for provider-side caching (locked 2026-04-27):** the composed Soul File prefix sent to the LLM provider MUST be **byte-identical across turns** for the same `(influencer_id, user_segment)` pair within the provider's cache TTL (Anthropic ephemeral = 5 minutes; Gemini context-cache = configurable; OpenRouter passes through to upstream). This unlocks Anthropic `cache_control: {type: "ephemeral"}` breakpoints (and equivalent on Gemini / OpenAI), buying ~80-90% TTFT reduction on the prefix contribution and ~90% input-token cost reduction on cache hits. Forbidden inside the cached prefix: timestamps, request IDs, UUIDs, random bullet ordering, current-date strings, model-temperature suffixes, anything that mutates per turn. The cache-control breakpoint sits at the END of Layer 4 (Per-User-Segment) and BEFORE per-turn user message, fresh memory facts, and recent-message context — those go in the uncached suffix. The `yral-rishi-agent-soul-file-library` composer owns this contract; the `yral-rishi-agent-conversation-turn-orchestrator` consumes the composed prefix as opaque bytes. CI gate: a test composes the prefix twice for the same `(influencer_id, user_segment)` 100ms apart and asserts byte-identity. For providers that don't support cache_control (some OpenRouter upstream models), document the latency delta but still emit the stable prefix — switching providers later is then a config change, not a refactor. See FUTURE_PICKUPS.md for related deferred patterns from OpenClaw R&D.

**Step 5 — Design consequences (things this SLO forbids):**
- Memory retrieval can be ADDED (parallel + short-circuit), does NOT break SLO.
- RAG can be ADDED (parallel retrieval, graceful fallback).
- Self-hosted LLM CANNOT replace Gemini if it's slower at TTF. **Benchmark before allocating GPU.** A 70B self-hosted model that does 800ms TTF when Gemini does 300ms is a step backwards — unless self-hosted is warranted for privacy/cost reasons and latency can be brought back through quantization or smaller model selection.
- Multi-hop tool calls (e.g., bot calls food-db then calls user-profile before responding) must render "thinking..." UI so perceived latency is acceptance, not completion.
- Adding Langfuse/observability adds zero user latency (async submission, no blocking on I/O).

---

## 3. Capability Blueprints (Plans A-H) — what goes INTO the new service

Each of the following plans is a **capability group** that the greenfield service must implement. In the old "don't touch chat-ai" framing these were separate microservices; in the new framing they're **features of the 12 core services** (Section 2.3). The content is preserved because it's the best way to think about WHAT you're building. The WHERE has changed: most of these live inside `yral-orchestrator`, `yral-prompt-system`, `yral-memory`, `yral-creator-studio`, `yral-media`, `yral-safety`, `yral-analytics`, `yral-advisor`.

> **How to read this:** for every capability below, ask "which of the 12 core services owns this?" — I've mapped a few explicitly, but most map obviously. Some old plans (like "LLM Gateway") dissolve into the orchestrator and become just a function call, not a whole service.

---

### PLAN A — Memory & Depth (fix shallowness)
*Bet: D30 retention ≈ 0% because bots forget. Fix memory → fix retention → fix revenue.*

🚨 **Architectural decision (locked 2026-04-27): memory layer is greenfield, NOT a mem0 dependency.** mem0 (mem0ai/mem0, Apache 2.0, ~54K stars) was evaluated as a candidate dependency for A2/A3/A4 and rejected for these specific reasons:

1. **Convention mismatch with YRAL standards.** YRAL's `feedback_explicit_naming` rule (every identifier reads as English) and `feedback_documentation_standards` rule (line-by-line comments + WHY blocks + 5 required docs per service) would force renaming/recommenting of mem0's 26,814 LOC — work comparable to rewriting the 1,300 LOC YRAL actually needs.
2. **Surface-area mismatch.** mem0 ships 28 vector-store backends, 12 LLM provider adapters, procedural-memory mode, embedchain legacy, server, dashboard, TS port. YRAL would import ~26K LOC to use ~1.7K. The relevant subset (extraction prompt, pgvector wrapper, multi-signal search, entity linking) totals ~1,700 LOC and is well within scope to reimplement.
3. **Resilience gap.** mem0's `Memory.add()` is soft-fail throughout: no retry on LLM rate-limits, no transactional atomicity across the 7-phase write (extract → embed → vector-insert → history-insert → entity-link), no row-level locks for concurrent writes to the same `(user_id, agent_id)`. Any production deployment needs a hardened wrapper anyway — at which point reimplementation is comparable effort to wrapping.
4. **Soul File integration is native here, bolted-on there.** YRAL's per-influencer extraction (different prompts for Tara vs. a coach bot vs. a companion bot) is naturally expressed in custom code that consumes the Soul File composer; in mem0 it sits behind a generic `custom_instructions` string parameter.
5. **Frontier-AI calculus shift.** With current LLM coding assistance, the "we figured out the architecture so you don't have to" value of mem0 has dropped: reading and understanding their extraction prompt is fast, and YRAL-domain-specific prompts are easier to write fresh than to adapt.

**What we DO lift from mem0 (architectural reference, no code dependency):**
- The **ADD-only extraction philosophy** (no LLM-driven UPDATE/DELETE on existing memories — append, dedup by content hash, let retrieval-time scoring resolve contradictions). mem0 v3 (April 2026) made this their core design choice; it's correct and we adopt it.
- The **7-section extraction prompt structure**: profile summary, last-k messages (pronoun resolution), recently-extracted-this-session (in-session dedup), top-N similar existing memories (cross-session dedup), new messages, observation date (temporal grounding), current date.
- The **multi-signal retrieval pattern**: semantic distance + BM25 keyword score + entity-match boost, fused before ranking. We tune the fusion weights for chat-companion use.
- The **entity-linking-as-second-collection pattern**: extract named entities from each memory, embed and store separately with `linked_memory_ids` back-references, dedup at score ≥ ~0.95.
- The **hash-based content dedup** (MD5 of memory text as content address; skip insert on hash match) — cheap, no LLM call, prevents exact duplicates.

**What we deliberately do NOT copy from mem0:**
- 28-backend abstraction layer (we use pgvector only; one wrapper, no plugin system).
- Procedural-memory mode (different problem; not relevant to chat companions).
- PostHog telemetry (privacy + compliance; we use Langfuse + Sentry already).
- Soft-fail-everywhere resilience pattern (we add retry + idempotency + transactional writes from day 1).

**Reference reading for the team building Plan A:** `/tmp/mem0-research/mem0/mem0/configs/prompts.py` (extraction prompt), `mem0/memory/main.py:573-971` (`Memory.add()` flow), `mem0/memory/main.py:1126-1410` (`Memory.search()` flow), `mem0/vector_stores/pgvector.py` (pgvector wrapper). Read these to understand the WHY behind the patterns above before writing YRAL's versions.

**Trade-off accepted:** we lose ~6 months of edge-case discovery that mem0's 90,000 developers have already done in production. We mitigate via aggressive Sentry + Langfuse instrumentation from day 1, the eval suite from Plan F (`yral-bot-quality-scorer`), and a feature flag to disable memory writes if extraction-quality eval drops below threshold.

**Time-cost comparison (informing the decision):**
- mem0-as-dependency path: ~4-6 weeks (write hardened wrapper service + influencer-specific extraction extension + telemetry-disable + retry/idempotency layer).
- Greenfield-with-mem0-as-reference path: ~4-6 weeks (write extraction prompt + pgvector wrapper + multi-signal search + entity linking + retry/idempotency).
- Roughly equal effort. Reversibility weighs in favour of greenfield: switching to mem0 later if rebuild stalls is easier than removing mem0 once embedded.

---

**Core services (in build order):**

| # | Service | What it does | Why it matters |
|---|---|---|---|
| A1 | `yral-llm-gateway` | Gemini-protocol proxy that intercepts every chat-ai → LLM call | The seam. Nothing else in this plan works without it. |
| A2 | `yral-memory-store` | Stores extracted facts per (user_id, influencer_id): "user's cat is named Momo", "user's goal = lose 5kg by June". **Greenfield Postgres+pgvector implementation; design lifts mem0's ADD-only philosophy + content-hash dedup + JSONB payload shape — see decision block above.** | Survives past 10-message window |
| A3 | `yral-memory-extractor` | Async worker that tails `messages` table, calls small LLM to extract facts, writes to memory-store. **Extraction prompt is YRAL-native, structured per mem0's 7-section pattern (profile / last-k / recently-extracted / similar-existing / new-messages / observation-date / current-date) but worded for YRAL multi-influencer chat with Soul File context per influencer.** Runs strictly async (after-turn, behind Redis Stream — never blocks the user-visible response). | Runs behind the scenes, no latency added |
| A4 | `yral-embedding-index` | Vector DB (pgvector inside shared Postgres — don't bring a new DB) of every message chunk. **Multi-signal retrieval: cosine distance + PostgreSQL full-text BM25 + named-entity-match boost, fused at query time. Entity links live in a sibling pgvector collection per the mem0 reference design.** | Semantic retrieval: "what did we talk about last Tuesday?" |
| A5 | `yral-user-profile` | Stable profile per user: name, tone preferences, location, goals, relationship stage with each bot | Different from memory; this is the stable identity. **Not present in mem0 reference; YRAL-original.** |
| A6 | `yral-conversation-summarizer` | Hourly job: summarizes old conversation chunks into short narratives, frees up context budget | "Compression" layer; enables effectively infinite history. **Not present in mem0 reference; YRAL-original.** |
| A7 | `yral-memory-consolidator` | Daily "sleep" job that merges duplicate memories, resolves contradictions, promotes frequent facts to profile | Imitates human memory consolidation; keeps store clean. **Not present in mem0 reference; YRAL-original.** |

**What chat feels like after Plan A:** bot remembers your birthday, your cat's name, what you told it last Tuesday, your goals, and writes responses in YOUR preferred tone. Feels like a friend, not a lookup.

**Template fit:** all 7 are Python/FastAPI; share one Postgres with pgvector extension; A3 + A6 + A7 are HTTP services invoked by cron (GitHub Actions cron-pings the endpoint — works inside your template without adding workers).

**Timeline realistic for you (2-3h/day, ADHD):** 6-8 weeks for A1-A4 (the "minimum viable memory"). A5-A7 over months 3-4. The greenfield-not-mem0 decision does NOT change this estimate — see the time-cost comparison in the decision block above.

**Day-1 instrumentation (non-negotiable for the rebuild):**
- Sentry transaction per `.add()` and `.search()` call, tagged with `(user_id, agent_id)` and the phase that errored if any.
- Langfuse trace per extraction LLM call, capturing input messages + extraction prompt + model output + parse outcome (succeeded / repaired / failed).
- Per-phase latency emitted to Langfuse (so we can see if extraction is slow, embedding is slow, or pgvector insert is slow without code-spelunking).
- Daily eval run against a held-out 200-conversation set; auto-flag if extraction recall drops >10% vs the prior week's baseline.
- Feature flag `memory_extraction_enabled` (default on) that, when flipped off, skips all `.add()` writes but lets `.search()` continue against historical data — kill-switch if extraction quality regresses without warning.

---

### PLAN B — Proactivity & Habit (make users come back)
*Bet: 100% reactive chat = no habit. A bot that texts first creates a habit. Habit = DAU → paid conversion.*

#### B.0 First-turn re-engagement nudge (within-session, Rishi-requested feature)

**What it is:** When a user opens a chat with an AI influencer:

1. **t = 0**: Bot immediately sends a greeting message (warm, in-character, generated from the influencer's Soul File + the user's known profile/memory if any).
2. Alongside the greeting, the client shows **3-4 starter option chips** (same behavior as the current service's "Default Prompts" per DOLR context doc §7.3).
3. An inactivity timer starts (default 20-30 seconds, per-bot tunable, A/B-testable globally).
4. State machine from here:
   - **User taps an option chip** → option becomes a user message → normal turn flow. Chips disappear on the client.
   - **User types a message** → normal turn flow. Chips disappear.
   - **User navigates away from the chat screen** → client sends a presence signal; timer pauses. If they come back, timer resumes.
   - **User stays on the screen, silent, timer fires** → **chips disappear AND bot sends a second follow-up message** — feels proactive, invites a response. Follow-up is LLM-generated, references the greeting naturally, asks an open question the user is most likely to engage with (uses the user's memory + Soul File for contextual hook).
5. After the follow-up, a second inactivity timer starts (default 45-60 seconds, longer than the first). If user still silent when THIS fires → **stop.** Do NOT send a third auto-message. Spammy behavior kills retention faster than it drives engagement.

**Why this matters (Rishi's stated bet):** the first 30 seconds in a chat decide whether a user engages at all. Today, if a user opens chat and doesn't click an option, nothing happens. They close the app. With this feature, the bot "feels alive" — it notices the pause, gently prompts again — and conversion from "app opened" to "first user reply" should rise meaningfully.

**Where it lives in the architecture:**
- **Client (mobile):** renders greeting + chips; tracks presence (is this chat screen in foreground?); handles option-tap, typing, timer-fire UI state. Sends presence heartbeat to `yral-rishi-agent-public-api` every 10 seconds while the chat screen is active. Removes chips when (a) user acts or (b) a new bot message arrives (covers both user-typed and auto-fired cases).
- **`yral-rishi-agent-conversation-turn-orchestrator`:** generates the greeting at conversation open (first turn of a new conversation OR when a returning user re-opens an existing conversation after >N hours idle). Also generates the follow-up message when the scheduler triggers it. Uses Soul File + user memory to make both messages feel personal.
- **`yral-rishi-agent-proactive-message-scheduler`:** owns the inactivity timer. When a user opens a chat, scheduler registers `(user_id, conversation_id, fires_at = now + 25s)`. On presence heartbeat, scheduler can extend/reset. If timer fires without a user message being posted in between, scheduler calls orchestrator to generate the follow-up → orchestrator posts the message → push notification to mobile if app is backgrounded.
- **Config lives in `yral-rishi-agent-soul-file-library`:** per-bot overrides (e.g., a "laid-back therapist" bot might wait 60 seconds; a "hyperactive companion" bot might wait 15 seconds). Global defaults in Layer 1 of the Soul File.

**What the follow-up message actually says:**
- Not "Hi, are you there?" (robotic)
- Instead: LLM generates it with full context. Examples:
  - Nutritionist bot: *"No rush — just curious, what did you eat last night? I can help you plan today around that."*
  - Astrologer bot: *"By the way, I was looking at your chart — there's something interesting about your Mars placement I'd love to share when you're ready."*
  - Companion bot: *"Kya sochh rahe ho? 😊 I'm just here whenever."* (tone/language matched to user's prior interactions if we have memory; generic warmth if we don't.)
- The LLM prompt for follow-up generation is itself part of the Soul File's prompt layers — a "re-engagement nudge" sub-prompt that combines: greeting + 3-4 options (what was shown) + user's memory/profile + Soul File + "generate a gentle, character-appropriate follow-up that invites response without being pushy; 1-2 sentences max."

**Paywall interaction (decision needed):** the follow-up message counts as 1 of the 50 free messages (because it's a bot-authored message in the conversation). This could annoy users who pay for ₹9 access then feel they "wasted" 2 messages on greeting + follow-up. **Recommend: DON'T count greeting OR auto-fired follow-up against the 50-msg paywall.** Only count messages that are a response to a user action. Encode this as a `count_toward_paywall` flag on each message; yral-billing's paywall check honors the flag.

**A/B testable variables (via `yral-rishi-agent-events-and-analytics`):**
- Inactivity timer duration (15s / 25s / 45s / 60s)
- Whether to show chips at all (some creators may prefer only a greeting)
- Follow-up message style (short/long, question/statement, emoji/plain)
- Per-bot defaults vs. global defaults
- Paywall-counting behavior (count / don't count)

**Success metrics:**
- Primary: **opened-chat → first-user-reply conversion rate**. Baseline (today): needs measurement during Phase 0 baseline capture. Target: +20% lift with this feature.
- Secondary: D1 retention, session length, paid-conversion rate per chat.
- Guardrail: **uninstall rate**. If nudging feels spammy, users uninstall. Kill the feature immediately if this metric moves >+2%.

**When to ship:** Phase 2 (Week 7-12) alongside the Soul File Library MVP. This is a high-visibility, low-complexity win that showcases v2's "the chat feels alive" promise. It ALSO validates the proactive-message-scheduler infrastructure at a small scale before we use it for cross-session proactive pings (Phase 4).

**Mobile-change implications** (cross-reference Section 7 Step 3.5 audit): adds minor client work — presence heartbeat emission + chip-dismissal-on-new-bot-message. Both are trivial (<1 day of mobile work each). Adds as item **M13** in the audit: *"Client-side presence heartbeat + chip dismissal on auto-fired bot message — <1 day mobile work, no protocol change, can ship with v2.0."* Still well within the "one mobile change" spirit if bundled with whatever other mobile change Saikat approves.

---

**Core services:**

| # | Service | What it does | Why it matters |
|---|---|---|---|
| B1 | `yral-scheduler` | Cron-like service. Stores "send X to user Y at time Z in timezone T". Fires webhooks. | Foundation for everything proactive |
| B2 | `yral-timezone-resolver` | Infers user timezone from activity patterns; stores in profile | Can't text at 3am; table stakes for proactivity |
| B3 | `yral-proactive-planner` | Nightly job: decides which users each bot should text tomorrow and what about | The brain. Uses memory + profile to pick topics |
| B4 | `yral-message-sender` | Posts INTO `conversations` table as if the bot sent a message (bypass chat-ai API — direct DB write) + triggers push | The mouth. Makes the proactive message appear |
| B5 | `yral-dormancy-detector` | Flags users who haven't opened app in 24h/72h/7d | Targets re-engagement with tiered urgency |
| B6 | `yral-streak-tracker` | Tracks daily chat streaks per (user, bot); writes streak counter into profile | Duolingo-style habit hook |
| B7 | `yral-event-triggers` | Fires on external events: user's birthday, workout missed, goal deadline | "Your bot noticed you didn't log breakfast" |
| B8 | `yral-notification-throttler` | Cross-service rate limiter so a user doesn't get 5 bots pinging at once | Prevents notification spam catastrophe |

**What chat feels like:** bot texts you at 7am "morning Rishi, how's the marathon prep?"; later at 8pm "you said you'd try that pasta — did you?". Bot has initiative.

**Risk:** badly tuned proactivity = spam = uninstalls. B8 is mandatory before shipping B1-B4.

**Timeline:** 4-6 weeks for B1+B4+B8 (minimum shippable proactive loop). B3 is the hard one — it's really a mini ML problem.

---

### PLAN C — Archetype Specialization (nutritionist/coach/etc actually good at their job)
*Bet: Generic Gemini is shallow for specialists. A nutritionist bot needs nutrition tools. A coach needs workout frameworks. Give each archetype superpowers.*

**Core services (per archetype, but modular — share infrastructure):**

| # | Service | What it does |
|---|---|---|
| C1 | `yral-tool-registry` | Registry of "tools" (functions) bots can call. e.g., `lookup_food_macros`, `plan_workout`, `log_mood`, `set_reminder` |
| C2 | `yral-tool-executor` | Sandboxed runner that the gateway invokes when the LLM requests a tool |
| C3 | `yral-food-db` | Nutrition database (can seed from USDA + Indian food DB); exposes `GET /foods?query=paneer` |
| C4 | `yral-workout-planner` | Generates structured workout plans, tracks progression |
| C5 | `yral-meal-planner` | Generates structured meal plans with grocery lists |
| C6 | `yral-mood-journal` | Stores mood logs; powers "how have you been feeling this week?" |
| C7 | `yral-goal-tracker` | Stores user goals + progress; bot sees progress in context |
| C8 | `yral-habit-tracker` | Binary daily habits (meditated? drank water? walked?) |
| C9 | `yral-archetype-router` | Gateway sub-component: looks at influencer type, picks which tools + which system prompt template |
| C10 | `yral-knowledge-rag` | Per-archetype knowledge base (nutrition research, coaching frameworks, CBT techniques). RAG in the gateway. |
| C11 | `yral-calendar-integrator` | (Later) Optional Google/Apple calendar read for context |

**What chat feels like:** you tell the nutritionist "I had paneer rice for lunch" — it logs the macros, checks your weekly target, congratulates you for staying under 1800 cal, and tonight reminds you you're 5g short on protein. It's not advice — it's **ongoing tracking**.

**Key insight:** specialists need state, not just smarter prompts. C6-C8 (journal + goal + habit) are the "stateful substrate" that makes specialists feel real. Build these once, share across archetypes.

**Timeline:** C1-C3 + one archetype (nutrition) end-to-end = 6-8 weeks for proof. Then additional archetypes = 1-2 weeks each because infrastructure is reused.

---

### PLAN D — Multi-Model Intelligence (raise the IQ ceiling)
*Bet: Gemini 2.5 is fine for chit-chat but limits reasoning. Route hard queries to smarter models per-task.*

**Core services:**

| # | Service | What it does |
|---|---|---|
| D1 | `yral-model-router` | Part of the gateway. Classifies incoming message (chitchat / reasoning / creative / sensitive / code / specialist) and picks model |
| D2 | `yral-reasoner` | Calls Claude Opus / GPT-5 for "deep think" turns (mental health, life advice, planning). Higher cost, higher quality. |
| D3 | `yral-creative-model` | Separate model pool for roleplay / creative writing quality |
| D4 | `yral-response-scorer` | Async: grades each response 1-5 on helpfulness + persona-fit. Feeds D1's routing |
| D5 | `yral-persona-consistency-checker` | Flags responses that break character. Signals D1 to re-generate with stricter system prompt |
| D6 | `yral-cost-controller` | Tracks per-user LLM $$$ cost; downshifts models if free-tier user exceeding threshold |
| D7 | `yral-ab-framework` | Microservice for A/B testing model choices, prompt variants. Chat-ai oblivious. |
| D8 | `yral-eval-harness` | Nightly: runs ~200 held-out prompts against current config; regression-detects quality drops |

**What chat feels like:** for simple "hi!" you get Gemini Flash (cheap, fast). For "I'm having a panic attack" you get Claude Opus (careful, deep). For creative "tell me a story" you get a model tuned for prose. User never knows — they just feel the bot "got it" every time.

**Timeline:** D1+D2 = 4 weeks. D4-D8 = 8+ weeks. Quality gains may be large or small — MUST ship eval harness (D8) FIRST to measure, otherwise you're guessing.

---

### PLAN E — Creator Ecosystem (the "creators write code" future)
*Bet: 100 in-house features < 10,000 creator features. Open a safe plugin system; let creators extend their bot. Long-horizon, highest leverage.*

**Core services:**

| # | Service | What it does |
|---|---|---|
| E1 | `yral-skill-registry` | Registry of "skills" (sandboxed code + metadata). Creators submit, users install per bot |
| E2 | `yral-skill-sandbox` | Sandboxed execution (likely: WASM or Firecracker microVMs). Skills can call HTTP + take LLM in/out |
| E3 | `yral-skill-marketplace` | Browse / install / rate skills. Revenue share per install |
| E4 | `yral-skill-permissions` | Users grant/deny skill access to memory, profile, external APIs |
| E5 | `yral-mcp-bridge` | Support Anthropic's Model Context Protocol — lets any MCP server become a YRAL skill |
| E6 | `yral-creator-analytics` | Per-skill usage, retention lift, revenue. Helps creators iterate |
| E7 | `yral-skill-moderation` | Review pipeline for malicious skills (prompt injection, data exfil, spam) |
| E8 | `yral-skill-llm-generator` | Creator says "I want a skill that logs periods" in English → LLM scaffolds skill code → deploys to sandbox. This IS the "creators write code intelligently" vision. |

**What chat feels like:** a creator builds an "astrology skill" that pulls real ephemeris data; 500 users install it on their companion; creator earns revenue per install and per chat. Your companion now has astrology superpowers without you writing a line of code.

**Timeline:** realistically 6-12 months. Don't start until A+B+C are in production. But START DESIGNING now so later plans don't conflict. E1-E2 alone is a genuinely hard engineering project.

---

### PLAN F — Response Quality Foundation ⭐⭐ NEW, NOW THE #1 PRIORITY
*Bet: **Memory, proactivity, and specialization are worthless if the bot's baseline response is a robotic 400-word essay.** The "Tara" problem: you have ONE bot that works because you hand-tuned its system prompt. Every other bot was auto-generated from a single word the creator typed and talks like a machine. Fix the prompt quality layer globally, give creators tools to tune their own bots, and give yourselves a global config dial. This runs BEFORE Plan A-E.*

**Why this gets promoted above Plan A:** in the first 3 messages, a user decides to keep chatting or leave. If the bot opens with a 5-paragraph essay, they're gone before memory can help. Plan F fixes that ceiling.

**Core services:**

| # | Service | What it does | Why it matters |
|---|---|---|---|
| F1 | `yral-prompt-registry` | Versioned store of system prompts organized in **layers**: (1) global YRAL layer, (2) archetype layer (companion/nutritionist/coach), (3) per-bot layer (creator-editable), (4) per-user-segment layer (new users get extra warmth, paying users get deeper content) | The "config file" you said you don't have. One source of truth, versioned, rollback-able. |
| F2 | `yral-prompt-composer` | Gateway sub-component: merges the 4 layers into final system prompt at request time. Feature-flag per layer. **Composes the prefix in deterministic byte-identical order (Layer 1 → 2 → 3 → 4) for provider-side prompt caching — see Section 2.8 Step 4 "Stable prompt prefix" rule.** | You can tweak GLOBAL layer once → every bot on YRAL improves overnight. Byte-stable composition unlocks Anthropic/Gemini prompt cache → ~80-90% TTFT reduction on the prefix after turn 1. |
| F3 | `yral-creator-prompt-coach` ⭐ | LLM chat service the CREATOR talks to: *"make my bot warmer", "make it funnier", "stop giving essays"*. The coach writes prompt edits, shows preview conversations, lets creator approve. This is literally "users can chat with an LLM to improve their bot's system config." | Turns every creator into a prompt engineer without them knowing it. Highest-leverage idea in this whole doc. |
| F4 | `yral-bot-bootstrap-v2` | Replaces the "user types one word → bad auto-prompt" flow. Instead: LLM runs a 4-question structured intake (voice? quirks? values? what-it-WON'T-do?) then synthesizes a rich prompt from a proven template. Can also ingest creator reference material (text, existing chat samples). | Fixes quality at the source — the moment bots are created. |
| F5 | `yral-few-shot-bank` | Curated library of 5-10 "example conversation turns" per archetype. Injected into the prompt so the bot learns style by example, not just instruction. | Few-shot > instruction for response style. Industry standard. |
| F6 | `yral-tone-normalizer` | Post-processing check: flags responses that sound "AI-ish" (phrases like "As an AI...", "I'm happy to help you with..."). Re-rolls or strips. Runs in gateway as sync post-filter (~20ms). | Brute-force anti-robot shield while prompts are still being tuned. |
| F7 | `yral-response-length-guardrail` | Enforces max length per turn (e.g., 2 sentences for casual chat, longer only when user explicitly asks). Clips or re-rolls. Model-aware (some models verbose by default). | Direct fix for the "long essay" complaint. Ship this WEEK 1. |
| F8 | `yral-opening-line-optimizer` | The bot's FIRST message to a new user is the single highest-leverage message. A/B test opening lines per archetype. Track "did user reply?" as the metric. | The thing that decides retention at turn 1. Disproportionate ROI. |
| F9 | `yral-ask-back-ratio` | Guardrail: ensures a meaningful % of bot responses end with a question back to the user. Conversations die when bots stop asking. | Directly fights the "conversation dies in 3 turns" problem. |
| F10 | `yral-tara-distillation` | Reverse-engineer what makes Tara's prompt good. Extract into a **"Tara Template"** — structural blueprint (opening style, tone register, signature phrases, hard constraints) that other prompts inherit. | You already HAVE the best prompt on your platform; productize it. |
| F11 | `yral-bot-quality-scorer` | Async: nightly job scores every bot on platform on response quality (length, tone, question-back ratio, user-reply-rate). Dashboard shows bottom 20% of bots. | Shows you which bots drag the platform down. Targeted intervention. |
| F12 | `yral-auto-prompt-improver` | For bottom-scoring bots: yral-creator-prompt-coach generates suggested prompt improvements and DMs the creator "hey, here's how to level up your bot — one tap to try". | Automated quality improvement; creator-in-the-loop, not auto-deployed. |
| F13 | `yral-prompt-ab-engine` | Runs 2 prompt variants per bot per user, picks the one with higher user engagement per-user. Fine-grained, not global. | Continuous improvement without team involvement. |
| F14 | `yral-persona-reference-injector` | Lets creators upload "reference material" (photos captions, sample voice notes, example chats they wrote) → service embeds these into prompt as persona evidence. | Creators with strong identities (influencers, characters) get richer bots. |
| F15 | `yral-model-per-archetype` | Different archetypes use different base models (creative roleplay → one model; specialist tool-use → another; NSFW → another). Already discussed in Plan D but critical for quality too. | One-size-fits-all Gemini isn't optimal for every bot type. |

**What chat feels like after Plan F:** every bot feels more like Tara. Responses are tight (1-3 sentences typical), human-sounding, end with a question that keeps you engaged. Creators who care can dial their bot up via a conversational editor; creators who don't care still benefit from the global layer getting better. You have a morning dashboard showing which bots improved and which got worse.

**UI dependency to flag:** F3, F4, F12, F14 need front-end surfaces in the main YRAL app (iOS/Android), not just chat-ai. The microservices are the backend; someone needs to add "Prompt Coach" and "Edit Bot Personality" screens in the main app. Call this out to whoever owns the main app.

**Timeline realistic for you (ADHD, 2-3h/day):** F1 + F2 + F6 + F7 + F10 = weeks 1-3 (minimum quality lift — ships improvement to every bot on platform). F3 + F4 = weeks 4-6 (creator tools). F8, F9, F11-F14 = weeks 7-12 (measured iteration).

---

### PLAN A2 — Authentication & Billing Integration (from Ravi's Rust chat + yral-billing)
*Bet: do NOT reinvent auth or billing. Integrate with what already works. Fix the gaps we inherited. Preserve the Google Play payment flow so mobile doesn't have to change.*

#### Current-state findings (from exploring `dolr-ai/yral-ai-chat` and `dolr-ai/yral-billing`)

**Authentication — how it works today:**
- Auth service: `yral-auth-v2` (Leptos/Rust) at `auth.yral.com` (also accepts `auth.dolr.ai` as alt issuer)
- Flow: OAuth 2.0 Authorization Code + PKCE → returns `access_token` + `id_token` + `refresh_token`
- JWT claims (id_token): `sub` (user_id), `exp`, `iss`, `ext_delegated_identity` (user's ICP identity), `ext_ai_account_delegated_identities[]` (up to 3 bot ICP identities)
- Bot identities expire after 7 days; refresh token rotation required
- Chat service auth middleware: parses `Authorization: Bearer <jwt>`, decodes via `decode_jwt()`, validates issuer + required spec claims (exp, sub, iss)
- 🚨 **Gap discovered**: Ravi's chat uses `validation.insecure_disable_signature_validation()` — **JWT signatures are NOT validated**. This is an inherited security bug. In v2 we MUST fetch auth.yral.com's JWKS and validate RS256 signatures properly.
- CallerType resolution: chat queries ICP's `USER_INFO_SERVICE` canister via `get_user_profile_details_v_7(principal)` to distinguish `BotAccount` vs `MainAccount`. This is what powers "Chat as Human" — when a creator's bot identity sends a message, service knows it's a human takeover.

**Billing — how it works today:**
- Billing service: `yral-billing` (Rust/Axum/Diesel) at its own endpoint, uses SQLite (local) + Postgres (prod)
- **Payment rail: Google Play In-App Purchase** (Android). iOS Apple IAP likely in the pipeline but not visible in the repo yet.
- Price: **₹9 = 900 paise** per 24-hour bot-chat subscription, hardcoded as `BOT_SUBSCRIPTION_REWARD_PAISE = 900` in `src/consts.rs`
- Purchase flow:
  1. Mobile: user hits paywall → triggers Google Play IAP sheet
  2. User pays → Google Play returns `purchase_token`
  3. Mobile POSTs `{package_name, product_id, purchase_token, bot_id}` to `/google/chat-access/grant` on yral-billing
  4. yral-billing calls Google Play Developer API to verify purchase + consume it
  5. yral-billing inserts row in `bot_chat_access` table: `(id, purchase_token, user_id, bot_id, status=ConsumePending, granted_at, expires_at = now + 24hr)`
  6. After Google Play confirms consume, status flips to `Active`
  7. Also inserts a `Transaction` row (type = `BotSubscriptionReward`, amount = 900 paise, recipient = bot_id) — this feeds the creator's wallet
- State machine for access: `ConsumePending` → `Active` (with retry on crash/partial-failure)
- Real-Time Developer Notifications (RTDN) from Google Play: webhook for refunds, chargebacks, voids — handled by `rtdn.rs`
- Access table schema: `bot_chat_access(id, purchase_token, user_id, bot_id, status, granted_at, updated_at, expires_at)` — keyed on purchase_token; per-user-per-bot lookups via `(user_id, bot_id)` index
- Re-use of same purchase_token for different bot = rejected (`TokenAlreadyUsed`)
- YRAL Pro: `YRAL_PRO_CREDIT_ALLOTMENT = 30` credits/month (future monthly subscription tier)

**The 50-message free tier logic** (inferred from repo structure + DOLR context doc):
- Each conversation's message count is tracked in `conversation_turns` (or equivalent)
- Before each new turn, chat service checks: `SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND (sender_id = user_id OR sender_id = bot_id)` → if >= 50 AND no active `bot_chat_access` row, reject with paywall
- Paywall is per-bot per-user, not global — user gets 50 free messages with EACH new bot they chat with
- When mobile gets the paywall response, it triggers the IAP sheet

#### How v2 integrates (zero reinvention of auth or billing)

**Principle:** `yral-auth-v2` and `yral-billing` are owned by Ravi / the team, NOT by us. We consume them as-is. We add only chat-side logic around them.

**Auth integration in v2:**
- New service `yral-rishi-agent-public-api` uses an auth middleware identical in contract to Ravi's (Authorization header → JWT → sub + ext identities)
- **Fix the signature-validation gap**: fetch `auth.yral.com/.well-known/jwks.json` → cache keys in Redis with 1-hour TTL → validate RS256 signatures properly. Fall-back to `auth.dolr.ai` issuer if needed.
- Implement `resolve_caller_type()` the same way Ravi does — via USER_INFO_SERVICE canister — so "Chat as Human" still works.
- Implement refresh-token rotation on the client-side pattern (mobile already does this; we just honor new tokens).
- JWT validation happens at the public-api edge; downstream services trust the `user_id` passed in request headers (via mTLS or Swarm overlay network — no re-validation).

**Billing integration in v2:**
- v2 never implements payment logic itself. Every access check is a gRPC/HTTP call to yral-billing.
- **Add a caching layer in the orchestrator**: before each turn, check `yral-billing` for `(user_id, bot_id)` active access. Cache in Redis with 60-second TTL to avoid hitting yral-billing on every message. (If billing says "expires at T", cache until T.)
- The 50-message free counter is enforced at the orchestrator before the LLM call. Query `conversation_turns` count; if >=50 and no `bot_chat_access` row, return access-denied via standard `ApiResponse` envelope (NOT 402 — see CONSTRAINTS E7 + Section 11.8.2 below for the corrected paywall contract). Mobile reads `hasAccess: false` and triggers Google Play IAP sheet client-side.
- Google Play + Apple IAP flow completely unchanged; yral-billing handles both. Mobile triggers IAP sheet; yral-billing verifies; inserts row; orchestrator reads row on next turn.
- YRAL Pro (future tier): orchestrator reads credit balance from yral-billing; deducts per turn per tier rules.
- Creator earnings: when yral-billing inserts a Transaction row (`BotSubscriptionReward`, bot_id, 900 paise), the 70/30 split is handled by yral-billing. Our `yral-rishi-agent-payments-and-creator-earnings` service reads from yral-billing's transaction stream to feed wallet dashboards. We do NOT duplicate yral-billing's ledger.

**Integration surface (new services ↔ existing team services):**

| From | To | Purpose | Protocol |
|---|---|---|---|
| `yral-rishi-agent-public-api` | `auth.yral.com` (yral-auth-v2) | Fetch JWKS for signature validation | HTTPS GET `/.well-known/jwks.json`, cached |
| `yral-rishi-agent-public-api` | USER_INFO_SERVICE canister (ICP) | Resolve CallerType (bot vs user) for "Chat as Human" | ic-agent via yral-canisters-client Rust crate (or equivalent Python binding) |
| `yral-rishi-agent-conversation-turn-orchestrator` | `yral-billing` | Check chat access, read credit balance, decrement credits | HTTP GET/POST — 60s cache in Redis |
| `yral-rishi-agent-payments-and-creator-earnings` | `yral-billing` transactions feed | Read creator-wallet transactions for dashboards | Polling or event-stream subscription (whichever yral-billing supports) |
| mobile | `yral-billing` directly (as today) | Initiate IAP, submit purchase_token | HTTPS POST (unchanged from today) |

**Gaps / improvements we must address in v2:**
1. **JWT signature validation** (fix Ravi's inherited gap) — proper RS256 verification against JWKS.
2. **Mutual TLS between internal services** (or Swarm overlay network isolation) so downstream services can trust `user_id` without re-validating JWT per hop.
3. **Centralized access-check caching** so yral-billing isn't hit per turn — 60s Redis cache keyed on `(user_id, bot_id)`.
4. **Paywall response envelope match** — paywall is NOT a 402 (corrected 2026-04-23 evening + reaffirmed by Codex audit 2026-04-27; see Section 11.8.2 + CONSTRAINTS E7). v2 returns `ApiResponse<ChatAccessDataDto{hasAccess, expiresAt}>` for the pre-chat access check; mobile triggers Google Play IAP client-side when `hasAccess=false`. v2 must match the exact shape so mobile code doesn't change.
5. **Support both Google Play and iOS Apple IAP** — even if Apple flow isn't live today, design yral-billing integration to be rail-agnostic from v2's perspective.
6. **Paywall A/B testing hooks** — future experimentation (e.g., 30 msgs vs 50 msgs free, ₹9 vs ₹19, monthly vs daily) is routed through yral-billing, but v2's orchestrator must treat the paywall threshold as a config value, not a constant.

**Non-goals for v2's auth/billing integration:**
- Building a new OAuth server. `auth.yral.com` is it.
- Building a new payment rail. yral-billing owns Google Play + iOS + Pro tier logic.
- Changing the ₹9/24hr economic model (that's a product decision, not ours).
- Replacing the `bot_chat_access` table schema (yral-billing's schema is canonical).

---

### PLAN G — Creator Monetization & Private Content ⭐ NEW
*Bet: Users ask creators for private images/audio and today there's no path. Creators lose revenue, users lose value. Build the rails.*

**IMPORTANT:** this plan has strong legal/safety implications (age verification, consent, CSAM prevention, NSFW). Treat G6 (consent) and G7 (safety) as non-negotiable preconditions for G1-G5.

**Core services:**

| # | Service | What it does |
|---|---|---|
| G1 | `yral-content-request` | User says "send me a selfie" or explicit ask. Service detects the ask (classifier), opens a request ticket. |
| G2 | `yral-creator-inbox` | Creators see pending requests. Approve (→ auto-fulfillment) / custom-fulfill / deny. Push notifications. |
| G3 | `yral-ai-creator-image-gen` | With creator's prior consent, generates AI images in creator's visual style (requires creator to upload reference images; train or fine-tune). Strictly SFW unless creator opts-in to NSFW and user has verified age + consented. |
| G4 | `yral-content-vault` | User's unlocked private content library per-creator. Persistent, re-viewable, receipt history. |
| G5 | `yral-micropayments` | ₹ per content unlock. Settlement to creator wallet. Extends existing ₹9/24hr economy. |
| G6 | `yral-consent-manager` | Creator's explicit consent per content type (selfies / NSFW / voice / video). Revocable. Logged immutably. |
| G7 | `yral-safety-gate` | Age verification for user, CSAM detection on all AI-generated content, automated and human review for flagged content. **Non-negotiable.** |
| G8 | `yral-voice-message-gen` | AI-generated voice messages in creator's voice (requires creator's voice sample + consent). |
| G9 | `yral-tip-jar` | Users tip bots / creators without a content request. Simple micropayment with a thank-you reply. |
| G10 | `yral-custom-video-requests` | Higher tier — creator records a personalized video (like Cameo). Not AI; human. |
| G11 | `yral-creator-earnings-live` | Real-time earnings dashboard for creators; pushes motivating notifications ("you earned ₹80 today!"). |

**What it feels like:** user asks "can you send a pic of you at the beach?" → bot replies "sure, ₹49 — want me to send?" → user pays → AI-generated image (in creator's approved style) arrives. User feels they got something special; creator earns; platform takes cut. Revenue per chatting user goes up without new user acquisition.

**Timeline:** G6+G7 FIRST (4 weeks; legal work too), then G1+G2+G3 (4 weeks). Revenue features G4-G11 over 2-3 months after. Don't skip safety; it's ruinous if you do.

---

### PLAN H — Meta-AI Advisor & Analytics (self-improving platform)
*Bet: You can't be improving daily if you don't SEE daily what's changing. You're one person — an AI that reads your metrics and tells you what to focus on is higher leverage than any single feature.*

**Core services:**

| # | Service | What it does |
|---|---|---|
| H1 | `yral-event-pipeline` | Every meaningful action (message sent, message received, bot created, payment, app open, session length) → event. Simple Postgres event table or Redis stream; start cheap. |
| H2 | `yral-metrics-warehouse` | Nightly job rolls events into per-day per-cohort per-bot-archetype rollups. Your "warehouse" — doesn't need to be Snowflake; a Postgres schema is fine at 10K DAU. |
| H3 | `yral-daily-dashboard` | Static page served at metrics.rishi.yral.com. Big numbers. Deltas. Sparkline per KPI. Open it every morning. |
| H4 | `yral-weekly-narrator` | LLM-powered: "this week retention went up 8%, driven by new nutritionist archetype; chat length dropped 4% for companions — worth investigating." Lands in your inbox Monday 8am. |
| H5 | `yral-improvement-advisor` ⭐ | **The big one.** LLM reads last 7-28 days of metrics + product context + current backlog → emits a ranked list: "TOP 3 things to do this week." Refreshes daily. You open, decide, act. Effectively a Chief of Staff AI. |
| H6 | `yral-hypothesis-generator` | Given an observation ("nutritionist D7 retention = 2x companion"), proposes 3 testable hypotheses ("is it tool-use? is it structured goals? is it length of responses?"). |
| H7 | `yral-auto-experimenter` | You approve a hypothesis → this service spins up an A/B test using the prompt-ab-engine (F13), monitors, reports statistical significance, kills the test when conclusive. |
| H8 | `yral-anomaly-detector` | Daily: "DAU dropped 15% — flag." "Average session length -20% in companion archetype — flag." Sentry-like but for business metrics. |
| H9 | `yral-user-feedback-synthesizer` | If you run user interviews or surveys, dumps transcripts → LLM distills themes. Amplifies small amounts of qualitative data. |
| H10 | `yral-competitor-watcher` | Periodic web/LLM analysis of competitor AI companion apps (Character.ai, Replika, Talkie). "They just launched X — here's what it implies for us." Optional but powerful. |

**What it feels like:** every morning, 7 minutes with coffee: dashboard → weekly narrator → improvement advisor's top-3. You pick one, ship, next day the advisor tells you if it worked. You stop guessing.

**Timeline:** H1 + H2 + H3 = weeks 1-3 (this is FOUNDATIONAL — without data, you can't measure any other plan's impact). H4 + H5 = weeks 4-6. H6-H10 later.

---

## 4. Full Capability Catalogue (75 features across the 12 core services)

> These were "75 microservices" in the old plan. In greenfield they become **features within the 12 core services** (Section 2.3). Group maps: Quality → `prompt-system` + `orchestrator`; Memory → `memory`; Proactivity → `proactive-engine`; Archetype/Tools → `tool-runtime` + `prompt-system`; Intelligence → `orchestrator` routing; Media → `media`; Safety → `safety`; Creator → `creator-studio`; Analytics → `analytics` + `advisor`; Ecosystem → `tool-runtime` (future).

If you mixed-and-matched the plans above, this is your full menu. Bold = I think must-haves. *Italic = defer.*

**Response Quality — Plan F (15, TOP PRIORITY):**
- **yral-prompt-registry**, **yral-prompt-composer**, **yral-creator-prompt-coach**, **yral-bot-bootstrap-v2**, **yral-few-shot-bank**, **yral-tone-normalizer**, **yral-response-length-guardrail**, **yral-opening-line-optimizer**, **yral-ask-back-ratio**, **yral-tara-distillation**, yral-bot-quality-scorer, yral-auto-prompt-improver, yral-prompt-ab-engine, yral-persona-reference-injector, yral-model-per-archetype

**Infrastructure / Seam (4):**
- **yral-llm-gateway** — the seam
- **yral-model-router** — picks model per turn
- **yral-event-bus** — simple Redis/NATS pub-sub service; all "new message" events flow through here
- yral-feature-flags — toggle microservices on/off per user for safe rollout

**Memory Layer (7):**
- **yral-memory-store**, **yral-memory-extractor**, **yral-embedding-index**, **yral-user-profile**, yral-conversation-summarizer, yral-memory-consolidator, yral-shared-memory-graph *(cross-bot memory with permission)*

**Proactivity Layer (8):**
- **yral-scheduler**, **yral-message-sender**, **yral-notification-throttler**, yral-timezone-resolver, yral-proactive-planner, yral-dormancy-detector, yral-streak-tracker, yral-event-triggers

**Archetype / Tool Layer (11):**
- **yral-tool-registry**, **yral-tool-executor**, yral-food-db, yral-workout-planner, yral-meal-planner, yral-mood-journal, yral-goal-tracker, yral-habit-tracker, yral-archetype-router, yral-knowledge-rag, yral-calendar-integrator

**Intelligence Layer (5):**
- yral-reasoner, yral-creative-model, yral-response-scorer, yral-persona-consistency-checker, **yral-eval-harness**

**Content & Media (5):**
- yral-image-gen-gateway *(existing call already async; wrap it)*, yral-voice-synthesis *(bot sends voice notes)*, yral-meme-generator, yral-shared-album *(photos "you and your bot")*, yral-video-recommender *(YRAL videos based on chat)*

**Safety & Moderation (4):**
- **yral-moderation**, **yral-crisis-detector** *(mental health red flags — non-negotiable)*, yral-nsfw-classifier, yral-age-gate

**Creator Monetization / Private Content — Plan G (11):**
- **yral-consent-manager**, **yral-safety-gate**, **yral-content-request**, **yral-creator-inbox**, yral-ai-creator-image-gen, yral-content-vault, yral-micropayments, yral-voice-message-gen, yral-tip-jar, yral-custom-video-requests, yral-creator-earnings-live

**Analytics + Meta-AI Advisor — Plan H (10):**
- **yral-event-pipeline**, **yral-metrics-warehouse**, **yral-daily-dashboard**, **yral-improvement-advisor**, yral-weekly-narrator, yral-hypothesis-generator, yral-auto-experimenter, yral-anomaly-detector, yral-user-feedback-synthesizer, yral-competitor-watcher

**Growth & Retention (5):**
- yral-push-scheduler, yral-daily-digest, yral-weekly-recap, yral-shareable-moment-generator, yral-referral-tracker

**Creator Ecosystem (8):**
- yral-skill-registry, yral-skill-sandbox, yral-skill-marketplace, yral-skill-permissions, yral-mcp-bridge, yral-creator-analytics, yral-skill-moderation, yral-skill-llm-generator

---

## 5. Greenfield Build Roadmap — 11 Phases

**Authoritative timeline:** see `TIMELINE.md` (sibling doc) for day-by-day detail with Rishi-on-Motorola checkpoints. This section is the high-level summary. The two MUST stay aligned — TIMELINE wins on conflicts.

**Sequencing principle (Rishi 2026-04-24):** template first → hello-world from template → feature-parity services → 1000× services. Local testing uses FULL data port from chat-ai (CONSTRAINTS A13). Cutover has no timeline (CONSTRAINTS A6).

| Phase | Days | What ships | 🤳 Motorola checkpoint |
|---|---|---|---|
| **0 — V2 template + hello-world** | 1-5 | Local v2 template at `yral-rishi-agent-new-service-template/` proven via throwaway hello-world service | #0: `docker compose up` passes locally; observability all green |
| **1 — Feature parity services + full data port** | 6-22 | All 21 chat-ai endpoints + influencer CRUD + billing pre-check + H2H chat. Full ETL of chat-ai data into local v2 Postgres | #1-#4: Motorola exercises every v1 feature against ported-prod data |
| **2 — Memory + Depth (Priority #1)** | 23-28 | Tiered memory (pgvector) — bot remembers across sessions | #5: bot recalls something said in earlier session |
| **3 — Soul File + SSE Streaming (Priority #2)** | 29-36 | 4-layer Soul File composer + streaming responses (first token <200 ms); Tara stays on OpenRouter | #6: streaming feels fast; quality feels better |
| **4 — Proactivity + First-turn Nudge (Priorities #4 + #7)** | 37-42 | Scheduler + presence heartbeat + bot follows up after 25s silence | #7: bot nudges if you sit idle |
| **5 — Safety + Moderation (mandatory before any canary)** | 43-46 | Crisis detector + prompt injection defense + NSFW + age gate | #8: safety filter active |
| **6 — Programmatic AI Influencer Creation via MCP (Priority #5)** | 47-52 | Open API + MCP server wrapping influencer endpoints | #9: Claude Desktop creates an influencer |
| **7 — Creator Tools + Analytics Backend (Priority #8)** | 53-60 | Soul File Coach + creator analytics (backend) | Backend demo |
| **8 — Meta-AI Advisor (Priority #10)** | 61-65 | Daily LLM-generated top-3 actions for Rishi | Rishi-facing dashboard |
| **9 — Creator Monetization + Private Content (Priority #9)** | 66-80 | Tip jar, content vault, consent + safety gates | Backend first |
| **10 — Deploy to rishi-4/5/6 cluster** | Separate approval gate | Production cluster provisioning, chaos tests, latency baselines | Rishi's explicit gate |
| **11 — Cutover to V2** | Separate approval gate | Caddy on rishi-1/2 starts routing percentage of `chat-ai.rishi.yral.com` traffic to v2 | Rishi's explicit gate |

**Total to Phase 1 parity on Motorola:** ~22 days at reasonable pace.
**Total through Phase 4 (memory + streaming + proactivity = bulk of 1000× UX):** ~42 days (~6 weeks).
**Total through Phase 9 (full backend):** ~80 days (~11-12 weeks).

Pace flexes with available hours. Every phase ends with a Motorola checkpoint; we pause, test, iterate, then move on. Cutover (Phase 11) is NOT tied to any phase — happens only when Rishi explicitly says "cut over now" (CONSTRAINTS A6).

**Open separately and IN parallel** (no specific phase): mobile-side bundled change (SSE parser + `sendMessageStream` + presence heartbeat + chip dismissal + Firebase flag) needs Sarvesh + Shivam capacity. Estimated 2-3 sprints. Documented in `running-coordination-asks-plus-mobile-team-memo-and-change-log/`.

### Parallel-forever tracks (always running)

- **Eval harness** (Langfuse built-in) — runs on every PR touching LLM-facing code; posts diff
- **Shadow traffic middleware** — every orchestrator change runs shadow before promote
- **Meta-AI advisor** (post Phase 8) — daily top-3 actions in your inbox
- **Security review** — every 90 days, rotating safety audits
- **Latency baseline check** — automated comparison vs `latency-baselines.md` (CONSTRAINTS E1)
- **Synthetic user heartbeat** — canary bot every 5 min

---

## 6. Architectural Principles (the rules that keep this sane)

### 6.A The Safety Covenant (Rishi's hard rules — non-negotiable)

**A1. No deletions, ever, without explicit per-item approval from Rishi.** (See Section 1.5 No-Delete Covenant for full list of what this covers.)
**A2. One mobile-client change maximum.** The cutover target is literally one DNS flip with zero mobile-code change. If we must change mobile code, it's one env-var / config flip, and Sarvesh/Shivam must agree the change is worth it.
**A3. Preserve all AI influencers.** Every influencer ever created, with its ID, Soul File, creator, earnings history, follower count, intact.
**A4. All secrets in Vault, fetched at runtime via env vars.** Not in code, images, git, CI yaml. See Section 1.5.1.
**A5. Three-layered backups, verified weekly.** No single failure can destroy data. See Section 1.5.2.
**A6. Explicit English naming.** Any engineer (or ADHD Rishi reading code at 2am) must understand what a service/table/function does from its name alone.
**A7. Never regress latency.** New service p50/p95/p99/p99.9 must be ≤ current service's at every rollout step. Latency regression = automatic rollback. See Section 2.8.
**A8. LLM-agnostic by design.** Orchestrator talks to `llm-client` abstraction; switching providers is a config change, not a rewrite. Long-term goal: self-host best open-weight models when latency + quality permit.
**A9. Maximum dynamism / no hardcoded values in code.** Rishi's philosophical rule for v2: **nothing that changes should be hardcoded in code or templates**. IPs, hostnames, ports, version strings, timeouts, tunable thresholds, feature flags, model choices, LLM provider — everything that might change per environment or per-service lives in a **single config file that all services read from**. CI lint rejects literal IPs. Template has one `shared-config.yaml` (or similar) that every spawned service consumes. Changing a tunable = edit one file, redeploy. The plan doc itself is exempt (IPs in docs are fine for human reference); this rule is about **runtime code, templates, scripts, and CI workflows**. Why: Rishi wants to add/remove/move servers or bump values without grep-and-replace across 13 services. **"Since we are creating the V2 version of the template I would love if everything is dynamic and fluid and have separate configs for things that will be used across almost all new services being created by the template itself."**

### 6.B The Build Principles

1. **Build on new cluster (rishi-4/5/6), not on existing cluster.** Zero impact on rishi-1/2/3 until cutover.
2. **Shadow → canary → cutover.** New code proves itself in shadow before touching any user, then 1% canary, then ramp.
3. **Old services are safety nets, not maintenance burdens.** Freeze features in old service; only security patches. Energy goes to v2.
4. **One DB, many schemas.** One Patroni cluster on new servers. Schema per service. Read-only views when crossing schemas. Writes only through service APIs.
5. **Template-driven service spin-up.** Build a new template (`yral-chat-v2-service-template`) — DO NOT delete the old `yral-rishi-hetzner-infra-template`. New template bakes in: Swarm deploy, Caddy route, Sentry wiring, Vault secret fetch, Beszel+Uptime registration, Postgres schema creation, Redis client, Langfuse tracing, health endpoint.
6. **Redis Streams for events; no polling.** Services emit events to streams, consume via consumer groups.
7. **Feature flags on everything.** Postgres-table-based flags (Unleash or homegrown). Instant rollback.
8. **Soul File layers are the product, not a config.** Versioned, audit-logged, never edited in-place, eval-tested before activation.
9. **Quality ceiling first, depth second.** Soul File Coach + guardrails (Plan F) ship before memory (Plan A).
10. **Streaming is non-negotiable.** First token <200ms. Never buffer.
11. **Tool runtime uses MCP from day one.** Anthropic's Model Context Protocol as the native interface.
12. **Documentation per service** (DEEP-DIVE / READING-ORDER / CLAUDE / RUNBOOK / SECURITY). Non-negotiable per your memory.
13. **Safety before real users.** Crisis detector + moderation live before canary. Age gate + consent live before private content.
14. **Measure, don't guess.** Langfuse day 1. Eval harness before first prompt change. Analytics dashboard in Phase 2. Every change A/B'd.
15. **Meta-AI Advisor gives opinions, not dashboards.** If it's just graphs, we built it wrong.
16. **Soul File Coach is a structured product, not free-form chat.** State machine with guardrails against jailbreaks.
17. **Plan for 10× not 1000× on day 1.** Pick primitives that scale (Postgres + Redis + Swarm → 100K DAU without rearchitecting).
18. **Cost observability per turn.** Every turn trace includes ₹ cost. If a user costs ₹100/day while paying ₹9, we must know.
19. **Human↔Human + Chat-as-Human + Human↔AI are the SAME system.** Unified `conversation_type` column from day 1. No bifurcation.
20. **Profile indistinguishability is a platform invariant** (per DOLR context doc). The chat system must NOT leak whether the other side is AI or human, except to the creator of an AI influencer.

---

## 7. Migration Strategy (Old → New) — Detailed, honoring all constraints

This is the highest-risk part. 10K DAU on the existing stack; we can't break them. **Zero mobile-client code changes** is the hard target. **No deletions** is the absolute rule. **All AI influencers preserved** is the data rule. Here's how:

### Step 1 — Provision New Cluster (Week 1-2)
- Ask Saikat to allocate `rishi-4`, `rishi-5`, `rishi-6` (3 new servers). Install Beszel agents (monitor via `beszel.yral.com`). Register in Uptime Kuma. Point `dashboard.yral.com` references to include them.
- Install Docker Swarm on new servers (separate cluster from rishi-1/2/3 — independent Swarm manager token)
- Install Patroni Postgres cluster across rishi-4/5/6 (empty database)
- Install Redis cluster across rishi-4/5/6
- Install Caddy on rishi-4/5 as new cluster's edge proxy (separate from the Caddy on rishi-1/2)
- Add DNS A records `chat-v2.rishi.yral.com` → rishi-4, rishi-5 (new cluster's external entrypoint)
- Wire Vault access so new services can fetch secrets at runtime
- Wire Sentry DSN so new services report errors to the existing rishi-3 Sentry

### Step 2 — Zero-Risk Data Migration (Week 2-3)
- **AI influencers — MUST preserve** (per Rishi's data rule):
  - One-time ETL: `SELECT * FROM old_db.ai_influencers` → `INSERT INTO new_db.influencer.ai_influencers`. Preserve `id` column so mobile deep-links keep working.
  - Set up continuous CDC (logical replication from old Postgres to new Postgres on just this table) so any new influencer created via old service also appears in new database. No drift possible.
- **User chats — UPDATED 2026-04-24**: For LOCAL TESTING (the immediate concern), Rishi confirmed we port the FULL chat-ai dataset including user chats. ETL one-time + CDC continuous so local v2 mirrors prod state. For eventual production CUTOVER (no timeline; Rishi's discretion): keeping vs discarding old chats is a separate decision deferred until cutover is on the table. Older Option A vs Option B framing is superseded by Rishi's "full port for local testing" directive.
- **Human profiles — pass-through**: profile data is owned by Ravi's metadata service upstream. New chat service just calls metadata service like old chat service did.

### Step 3 — Build & Shadow on Private Subdomain (Weeks 3-12)
- New service runs on `chat-v2.rishi.yral.com` — private subdomain, zero production traffic yet
- **No mobile-client change yet. Zero user impact.**
- Internal shadow: we manually send real conversation examples through both old and new API, compare responses in Langfuse, measure quality delta
- Creator-side ramp: invite 5 trusted creators to use new Creator Studio (coach, editor) while their bots still serve via old service. Studio writes to NEW Soul File library; cutover will activate those new Soul Files.
- Eval harness runs nightly against both services on a held-out prompt set
- Success criterion before moving to Step 4: new service scores >20% higher on eval, matches old on latency, zero Sentry errors >P2 severity for 2 consecutive weeks

### Step 3.5 — Mobile-Client Change Audit 🚨 (DO THIS BEFORE BUILDING)

Rishi's constraint is **ideally zero mobile-client changes, absolute max ONE**. Before Phase 0 starts we must enumerate every change the new service might need on the mobile side and get Saikat's sign-off on which are acceptable. Sarvesh + Shivam own mobile; they need to agree on scope.

**The audit (draft — this list needs Saikat approval before we proceed):**

| # | Potential mobile change | Why it might be needed | Can we avoid it? | Approval status |
|---|---|---|---|---|
| M1 | Change base URL for chat API | If we can't do a clean DNS flip of `chat.yral.com` from rishi-1/2 → rishi-4/5 | ✅ **YES** via DNS flip at Caddy (Section 7 Step 4 Option A). **Cost of avoidance: zero.** | PENDING — confirm with Saikat that DNS-flip at Caddy is feasible given current routing setup |
| M2 | Switch from POST-response to SSE streaming | Streaming is a 1000× UX improvement (first token <200ms). But mobile currently expects POST → single JSON response. | ❌ NO if we want streaming. Mobile must be upgraded to consume SSE or WebSocket tokens. | **NEEDS APPROVAL** from Sarvesh/Shivam/Saikat — this is THE big mobile change. Impact: weeks of mobile work. Alternative: ship v2 without streaming first, then add streaming in v2.1. |
| M3 | Add new screens: Soul File Editor + Prompt Coach (creator side) | Plan F creator-side tooling needs UI. Backend can be ready; UI is absent without mobile work. | ❌ NO — UI is mobile-only. But can ship v2 WITHOUT these features first (backend ready, mobile adds UI later). | NEEDS APPROVAL — can be phased; not a blocker for v2 backend cutover. |
| M4 | Proactive message push notifications | New in v2 — bot texts user first (Plan B). Existing push notification infra via metadata service can be reused. | ✅ PARTIAL — reuse existing FCM/APNS pipeline; only the notification payload template changes (copy/image). Mobile tap-handling already opens chat. | Minor change — confirm notification payload schema is compatible. |
| M5 | Tip jar / private content UI (Plan G) | Micropayment + content unlock flows need new screens. | Not needed for v2 MVP — defer to Plan G launch (Month 4+). | Defer. |
| M6 | Paywall response schema (CORRECTED 2026-04-23 + Codex audit 2026-04-27) | Paywall is NOT a 402. Mobile does pre-chat IAP check via yral-billing returning `ApiResponse<ChatAccessDataDto{hasAccess, expiresAt}>`. If `hasAccess=false`, mobile triggers Google Play IAP sheet client-side. v2 must match this exact envelope. | ✅ YES — v2 returns identical envelope. No 402 anywhere. | No mobile change needed IF we match schema. Auto-verify during shadow phase. See CONSTRAINTS E7 + Section 11.8.2 for authoritative contract. |
| M7 | JWT auth header handling | v2 uses same Authorization: Bearer JWT. | ✅ YES — no change. | No change. |
| M8 | Bot creation flow (3-step: describe → expand → profile) | v2's `yral-rishi-agent-soul-file-library` improves the expansion step. Mobile flow itself unchanged. | ✅ YES — backend change is invisible to mobile. Mobile keeps calling existing `create-bot` endpoint; the expanded description comes back richer. | No change. |
| M9 | "Chat as Human" toggle | Already exists in mobile; uses ICP bot identity delegation. v2 preserves. | ✅ YES — no change. | No change. |
| M10 | Message Inbox rendering | v2 may send richer message types (voice notes, images, reactions). If mobile hardcodes text-only, richer types won't render. | ❌ NO if we ship richer media in v2. | **NEEDS APPROVAL** — can be phased (text-only v2 first, richer media in v2.1). |
| M11 | WebSocket inbox updates | Already exists — `WS /api/v1/chat/ws/inbox/{user_id}` in current Python service. v2 preserves protocol. | ✅ YES — preserve WebSocket contract. | No change. |
| M12 | Analytics events list | v2 may fire new event types. Mobile's analytics list (mobile-app-events-list.md per DOLR context doc) may need update. | ✅ PARTIAL — server-side events only, no mobile change needed unless we want mobile-side events. | Minor — update mobile-app-events-list.md, low effort. |
| M13 | Presence heartbeat + chip-dismissal-on-auto-message (for Plan B.0 re-engagement nudge) | First-turn inactivity nudge needs client to emit presence pings every 10s while chat screen is open, and to dismiss option chips both on user action AND when a new bot message arrives. | ⚠️ MINOR — small client work (<1 day total), no protocol change required; can reuse existing WebSocket channel for presence. | NEEDS APPROVAL — bundle with whatever other mobile change Saikat approves. |

**The headline decision for Saikat + Sarvesh + Shivam:**

**Q: Do we ship v2 with streaming (M2) or without?**
- **With streaming:** biggest UX win, but mobile work is substantial (SSE parsing, incremental UI updates, error handling mid-stream). Blocks v2 cutover until mobile is updated.
- **Without streaming:** v2 backend is ready without mobile change; cutover is pure DNS flip. Streaming added in v2.1 when mobile catches up.

**My recommendation:** ship v2 WITHOUT streaming first (M2 deferred to v2.1), get the backend cutover done with zero mobile change, then prioritize streaming as v2.1's headline feature. This lets us validate all the OTHER improvements (memory, proactivity, Soul File coach) without tangling with mobile release cycles.

**Action before Phase 0:** Rishi writes a one-page memo to Saikat + Sarvesh + Shivam asking for sign-off on:
1. DNS-flip feasibility for `chat.yral.com` (critical — M1)
2. Paywall response envelope match — `ApiResponse<ChatAccessDataDto>`, NOT 402 (M6, corrected per Codex audit 2026-04-27)
3. Whether streaming (M2) ships in v2 or v2.1
4. Whether creator-side UI (M3) is in-scope for v2 or later

**No code writing until Saikat signs off on this.**

---

### Step 4 — The Mobile-Client Change (Week 12-13) ⚠️ CRITICAL
- This is the ONE mobile-client change. We want it to be **literally one line**.
- **Option A (preferred, zero-change):** Don't change the mobile client AT ALL. Instead, change `chat.yral.com` DNS to split-route by percentage at the Caddy layer on rishi-1/2 (Caddy can load-balance between old-backend and new-backend). Start at 1% to v2, ramp up. **Mobile client sees nothing.**
- **Option B (one env-var change):** If split routing at Caddy isn't feasible, coordinate with Sarvesh/Shivam: mobile client reads a config endpoint at startup that tells it which chat-base-URL to use. Flip endpoint to `chat-v2.rishi.yral.com` for canary cohort. Mobile code = 1 config read (may already exist).
- **Option C (avoid):** mobile client refactors to call new API. Only if new API is protocol-incompatible. We design new API to be a superset of old API precisely to avoid this.
- **API contract:** new `yral-chat-public-api` MUST be a **strict superset** of old `yral-chat-ai`'s API for all endpoints mobile currently calls. Existing request/response shapes preserved. New endpoints added; old endpoints preserved.

### Step 5 — Canary Ramp (Weeks 13-18)
- At Caddy layer (Option A) OR via mobile config (Option B), ramp traffic to new service: 1% → 5% → 10% → 25% → 50%.
- Metrics watched: D1/D7 retention, session length, payment conversion, crash rate, Sentry errors, eval score, LLM cost per turn, uninstall rate, support ticket volume.
- **Regression alarm:** any metric drops >5% vs. control group → halt ramp, diagnose, fix, resume.
- Daily review meeting (5 min): look at Langfuse for bad conversations, triage.

### Step 6 — Full Cutover (Weeks 19-24)
- N = 100% when metrics hold for 2 consecutive weeks at 50%.
- Old Python `yral-chat-ai` keeps running on rishi-1/2 in **read-only fallback mode** for 90 days. Traffic no longer routes to it, but it's alive, data is intact, rollback remains possible.
- **NO DELETIONS.** Rishi decides deletion per-item, per Section 1.5 covenant.

### Step 7 — Eventual Retirement (Month 4+, ONLY after Rishi's per-item approval)
- After 90 days of production stability, Rishi reviews and decides, per item:
  - Old Python `yral-chat-ai` — retire? Archive repo? Shut down Swarm stack? Each needs explicit approval.
  - Old `rishi-hetzner-infra-template` — keep as template, or retire? (My vote: keep; it's still useful.)
  - Old Caddy routes pointing at retired service — remove? (Approval needed.)
  - DNS records for retired-service subdomains — remove? (Approval needed.)
  - Old GitHub secrets — rotate out? (Approval needed.)
- Rishi-1/2/3 remain allocated indefinitely unless Rishi decides to release. My recommendation: keep them, they're cheap, they're cushion.

### Rollback plan (at every step)
- **Steps 1-3:** no user impact, no rollback needed (everything on private subdomain)
- **Step 4-5 (canary):** at Caddy → flip traffic-percentage back to 0% → users on old service in <10 seconds. No code change. No data loss (chat history ported to v2 via ETL AND still present in chat-ai — both systems hold the same data during coexistence; influencer data replicated via CDC).
- **Step 6 (cutover):** flip Caddy split back to any percentage; old service still alive on rishi-1/2. Full rollback <5 minutes.
- **Catastrophic rollback (even months post-cutover):** old service is still deployed, just not receiving traffic. Flip DNS. Back in business. This is the reason we don't delete.

---

## 8. Critical Files & Docs To Read Before Phase 0

**YRAL product source of truth (read this FIRST):**
- `github.com/dolr-ai/yral/blob/main/context-for-agents.md` — the canonical product doc (product vision, user flows, every screen, service ownership, glossary including "Soul File", "Chat as Human", etc.). Every technical decision must align with this doc.

For **yral-chat-ai** (input to v2 design — data model + lessons):
- `/Users/rishichadha/Claude Projects/yral-chat-ai/app/routes/chat_v1.py:535-573` — existing memory-extraction background task; the turn-lifecycle in v2 must do this and much more
- `/Users/rishichadha/Claude Projects/yral-chat-ai/app/services/` — Gemini call wrapper; study for data that flows today and informs v2 API shape
- `/Users/rishichadha/Claude Projects/yral-chat-ai/app/config.py` — env var conventions to inherit and improve
- `/Users/rishichadha/Claude Projects/yral-chat-ai/migrations/002_chat_schema.sql` — the tables we're migrating FROM (and the `conversation_type`/`participant_b_id` scaffolding for h2h chat that's already there)
- `/Users/rishichadha/Claude Projects/yral-chat-ai/migrations/001_initial.sql:79-100` — the h2h chat unique index; data model already supports this, carry it forward
- `/Users/rishichadha/Claude Projects/yral-chat-ai/app/models.py` — Pydantic schemas; v2 API must be a superset so mobile client keeps working

For **infra template** (the paved road for new v2 services — DO NOT MODIFY, fork into a new template):
- `/Users/rishichadha/Claude Projects/yral-rishi-hetzner-infra-template/scripts/new-service.sh` — the 1-command spawner; will be copied/adapted for the v2 template
- `/Users/rishichadha/Claude Projects/yral-rishi-hetzner-infra-template/TEMPLATE.md` — usage guide
- `/Users/rishichadha/Claude Projects/yral-rishi-hetzner-infra-template/CLAUDE.md` — deep-dive

**Team-maintained observability & infra (bookmark, open before doing work):**
- `dashboard.yral.com` — index of all team infra
- `vault.yral.com` — secrets (get access from Saikat/Naitik)
- `sentry.rishi.yral.com` — Sentry (already rishi-3)
- `beszel.yral.com` — server monitoring
- `status.yral.com` — uptime monitoring

---

## 9. Verification (how we'll know it's working — per capability)

- **Quality (Plan F) — the north star test:** pick 10 conversations from your worst-performing bots today. Screenshot them. Six weeks after shipping F1-F10, re-pull 10 conversations from the same bots. Blind-rate them yourself 1-5 on quality. Target: median ≥+2 points. Also, average response length should drop measurably (probably 40-60% shorter).
- **Gateway (A1):** synthetic load test — 99p latency overhead < 20ms vs direct Gemini. Sentry error rate ≈ 0. Chat-ai Sentry shows no regressions.
- **Memory (A2-A4):** manual red-team — tell bot a fact today, ask about it 2 days later. Measure recall rate on held-out 50-conversation set. Baseline 0% → target 70%+.
- **Proactivity (B1-B8):** A/B test — cohort with proactive pings vs. control. Measure D7 retention delta (target +10pp) and uninstall-rate delta (target ≤+1pp — if spammy, kill it).
- **Creator Prompt Coach (F3):** 20 creators, have them run their worst bot through it. Before vs. after blind quality rating. Target: median creator reports "my bot got much better" (≥4/5). Secondary: user-side D1 engagement with coached bots vs non-coached.
- **Private content (Plan G):** revenue per paying user delta. Target: ₹9/user/day → ₹20+/user/day on users who unlock at least one piece of content. Also: zero CSAM incidents (non-negotiable).
- **Meta-AI Advisor (H5):** after 4 weeks of use, Rishi rates the weekly advisor output 1-5 on "was this actionable and correct." Target: median ≥4. If ≤3, rebuild it.
- **Archetype (C1-C3 nutrition):** 20-user pilot. Survey: "does the nutritionist bot feel like a real coach?" (1-5). Target median ≥ 4.
- **Always:** your eval harness runs nightly. Quality must not regress on baseline 200-prompt set.

---

## 10. Special Section — The "Bot Quality" Problem In Depth

Because you said quality is upstream of everything, let me lay out EXACTLY how the prompt-layer system works, because this is the single most important piece.

### How system prompts get composed at request time (via gateway)

```
┌────────────────────────────────────────────────────────────┐
│ Final System Prompt = concat(                              │
│   [Layer 1] Global YRAL Layer (you + team control)         │
│      "You are a YRAL AI. Keep responses under 2 sentences  │
│       unless asked. Never say 'As an AI'. Always end with  │
│       a question."                                         │
│                                                             │
│   [Layer 2] Archetype Layer (companion vs nutritionist)    │
│      "You are a companion — emotional, warm, curious."     │
│      OR "You are a certified-style nutritionist — use the  │
│       lookup_food_macros tool before claiming any numbers."│
│                                                             │
│   [Layer 3] Per-Bot Layer (creator-editable via F3 Coach)  │
│      "Your name is Tara. You love chai. You grew up in     │
│       Bangalore. You're slightly sarcastic."               │
│                                                             │
│   [Layer 4] Per-User Segment (new user / paying / dormant) │
│      "This user is on message 1-3 — give them a hook fast."│
│ )                                                           │
└────────────────────────────────────────────────────────────┘
```

**Who can edit what:**
- Layer 1: You + Yoa (platform-wide lever — tweak once, every bot improves)
- Layer 2: You + Yoa + trusted specialists
- Layer 3: Creator (via the Prompt Coach F3; guardrails prevent harmful edits)
- Layer 4: Algorithm (based on user state)

**Why this matters:**
- You asked for a config file to tweak — **Layer 1 IS that config file.**
- You asked for creators to edit their bots — **Layer 3 + the Coach are that.**
- Both exist in one system, versioned, rollback-able, eval-tested before deploy.

### How the Creator Prompt Coach (F3) works (non-programmer's view)

Creator opens "Tune my bot" in YRAL main app →

1. Coach: "Hey! I'm here to help make your bot feel more alive. What's the ONE thing you wish your bot did better?"
2. Creator: "It gives really long boring answers."
3. Coach shows 2 example conversations (current bot vs. proposed bot) side-by-side.
4. Creator: "Yeah, the second one is way better."
5. Coach: "Great — I'll update Layer 3 for your bot. You'll start seeing shorter replies in 1 minute. Tap here to try it now." → runs the updated prompt through eval → deploys → user can test immediately.

The coach is itself an LLM with a VERY tight system prompt of its own, a fixed 5-step workflow, and access to a library of prompt-improvement patterns. It's NOT free-form. It's a structured product with guardrails.

### How the "bad bot → DM creator with suggestion" loop works (F11 + F12)

- F11 runs nightly, scores every bot on platform.
- Bottom 20% bots: F12 generates a prompt improvement suggestion + preview of what it would change.
- Creator gets a push notification: "Your bot Sam got a 2/5 quality rating this week. Tap to improve in 2 minutes — preview attached."
- One tap opens the Coach with the suggestion pre-loaded. Creator approves or declines.
- This is **passive improvement at scale** — most creators won't initiate, but will accept a good suggestion.

---

## 11. Hard Questions to Answer Before Phase 0

1. **Who's in the build team beyond you + Yoa?** Saikat's 3 servers suggest platform support — is he allocating engineering time, or just infra? Solo Rishi + Yoa at 2-3h/day = ambitious timeline; the roadmap doubles if it's just you two.
2. **Can we self-host Langfuse on one of the 3 servers, or do we use their cloud?** Self-host is free + private but adds ops. Recommend: self-host, you have the skills.
3. **Does Saikat greenlight adding Redis to the stack?** Your template doesn't have it. Strong recommendation: yes. 1 extra Docker Swarm service, huge capability unlock.
4. **MCP as the tool-call standard — OK?** This is a major future-facing bet. If creator ecosystem is in Plan E, starting with MCP saves a rewrite later. Alternative: proprietary tool format, faster short-term, reshape-able later.
5. **Model strategy lock:** Gemini primary forever? Or are we designing for multi-provider from day 1? Designing multi-provider costs ~10% more upfront but saves a major refactor in Phase 4/5.
6. **Legal review timing:** private content (Plan G) has CSAM / deepfake / Indian IT rules implications. Need legal in the loop during Phase 4 design, not Phase 4 launch.
7. **What happens to the old service's Postgres data long-term?** Archive forever? GDPR / India-privacy requirements? Decide retention policy before cutover.
8. **Cost ceiling per user per day?** Current ~pennies. New stack with memory + RAG + multi-model + streaming will be 5-20×. Set a unit-economics red line now so features get built with cost awareness.
9. **Do we build the mobile-app side too, or does main YRAL team?** Creator Prompt Coach, Bot Editor, Content Request UI — all need mobile work. Capacity question.
10. **Naming:** yral-chat-v2? yral-chat-new? yral-chat? Decide before Phase 0 so repo/service/domain names are stable.

---

## 11.5 Memories to save to my auto-memory system (upon plan approval)

When you approve this plan, I will save the following as new or updated memory entries under `~/.claude/projects/-Users-rishichadha/memory/`. These are the constraints I must never forget across future sessions:

1. **`feedback_no_delete_covenant.md`** (UPDATE) — Expand existing rule to explicitly include: never delete any old chat services, templates, Sentry instances, Docker stacks, DNS records, Caddy routes, GitHub repos/secrets, database tables, etc. Per-item approval required. Cost of asking <<< cost of deleting wrong thing.
2. **`project_yral_chat_v2.md`** (NEW) — The greenfield chat platform plan: scope, 13 services, 6-month roadmap, runs on rishi-4/5/6 (to be provisioned), cutover via DNS flip on `chat.yral.com` preserving mobile client (zero-change goal).
3. **`reference_yral_infrastructure.md`** (NEW) — Canonical team infra: dashboard.yral.com (index), vault.yral.com (secrets), `sentry.rishi.yral.com` (Rishi's self-hosted Sentry on rishi-3 — the one v2 uses), `apm.yral.com` (team-shared Sentry — NOT used by v2), beszel.yral.com, status.yral.com, coolify.yral.com. Reuse, never replace.
4. **`reference_dolr_service_ownership.md`** (NEW) — Service ownership map: Ravi (chat/metadata/auth/storage), Ansuman (recommendation), Naitik (Coolify+Vault), Saikat (servers+monitoring+website), Sreyas (LTX), Sarvesh+Shivam (mobile). Know who to consult before cross-service changes.
5. **`reference_saikat_server_allocation.md`** (NEW) — rishi-1/2/3 legacy production servers (hands-off). rishi-4/5/6 provisioned 2026-04-23 for v2 cluster. Hardware spec + role assignments recorded. IPs in memory only, never in committed code (per no-hardcoded-IPs rule).
6. **`feedback_explicit_naming.md`** (NEW) — Code naming must be explicit English. Any English reader should infer purpose from name. Prefer verbose obvious over terse clever. Applies to services, tables, columns, functions, variables.
7. **`feedback_secrets_github_primary_vault_shared.md`** (NEW, **corrected**) — Secret management pattern (from existing `yral-rishi-hetzner-infra-template`): per-service secrets live in **GitHub Secrets** (set via `gh secret set` during `new-service.sh`); only **team-shared secrets that ALREADY exist in Vault** get read from `vault.yral.com` at runtime via `infra.get_secret("path/key")`. Do NOT push new secrets into Vault. Everything fetched at runtime via env vars. Never in code, images, git, CI yaml. Reason: rotation without rebuild; no accidental disclosure; respects Naitik's Vault ownership.
8. **`feedback_three_layer_backup.md`** (NEW) — 3-layer backup strategy: Patroni HA (Layer 1) + WAL archive to Hetzner S3 for PITR (Layer 2) + off-site weekly to Backblaze B2 (Layer 3). Weekly automated restore drill. Quarterly disaster recovery simulation.
9. **`feedback_mobile_one_change_rule.md`** (NEW) — Any change to yral mobile app costs dearly (other team, other codebase, release cadence). The NEW chat service must require at most ONE mobile change, ideally ZERO via DNS flip. API shape must be superset of old.
10. **`reference_yral_soul_file_terminology.md`** (NEW) — In YRAL product language, "Soul File" = structured personality definition of AI influencer (goals/traits/knowledge/style/behaviors). Use this term in code/docs. Do NOT invent "system prompt"/"personality config" — the DOLR vocabulary is "Soul File".
11. **`feedback_latency_never_regresses.md`** (NEW) — New chat service must meet or beat latency of current Rust chat service AND Python `yral-chat-ai` at every percentile, at every rollout step. Automated rollback on regression. See plan Section 2.8 for enforcement details.
12. **`feedback_llm_agnostic_design.md`** (NEW) — Chat must be LLM-agnostic: orchestrator calls through `llm-client` abstraction; swap providers via config. Long-term goal is self-hosting best open-weight models (requires Saikat GPU allocation and latency benchmarks before deployment).
13. **`feedback_template_first_build.md`** (NEW) — Rishi strongly prefers a template-first workflow: build a new template (`yral-rishi-agent-new-service-template`) BEFORE building any service from it; spawn all services via 1-command `new-service.sh`; fold learnings from each new service back into the template so it gets stronger over time; NEVER modify or delete the existing `yral-rishi-hetzner-infra-template` (it's the predecessor and stays as a reference). Template is the ADHD-friendly mental model: read once, understand all 13 services.
14. **`feedback_explicit_service_naming_v2.md`** (NEW, supersedes generic naming rule) — All new v2 services named `yral-rishi-agent-<explicit-english-purpose>`: must include `rishi` (owner), `chat-ai-v2` (version), and English purpose. Names can be long (up to 63 chars for Swarm); long beats cryptic. Applies to: GitHub repos, Docker images, Swarm stacks, Postgres schemas (underscores), subdomains (purpose-only since wildcard encodes rishi).
15. **`project_yral_scale_projection.md`** (NEW) — Scale expectations: today ~25K msgs/day, 4-6 months ~300-500K msgs/day, Month 12 ~1M+ msgs/day. Capacity plan in Section 2.7.5. Implications: horizontal scaling from day 1, multi-LLM-provider routing for rate-limit resilience, pgBouncer baked into template, partition-friendly schema design, self-hosted LLM becomes cost-justified by Month 9-12.
16. **`reference_yral_auth_billing_architecture.md`** (NEW) — Auth = `yral-auth-v2` at `auth.yral.com` (OAuth2/PKCE, RS256 JWT, issuers `auth.yral.com` or `auth.dolr.ai`, JWT contains `sub` + `ext_delegated_identity` + `ext_ai_account_delegated_identities[]` up to 3). Billing = `yral-billing` repo (Rust/Diesel, Google Play IAP, price 900 paise = ₹9 / 24hr per bot via `bot_chat_access` table, State machine ConsumePending→Active, RTDN webhook for refunds). V2 consumes both; never reinvents. Gap inherited: Ravi's chat disables JWT signature validation (`insecure_disable_signature_validation`) — we MUST fix in v2 by fetching JWKS from `auth.yral.com/.well-known/jwks.json`.
17. **`project_v2_mobile_change_audit.md`** (NEW) — 12 potential mobile-side changes enumerated (M1-M12 in plan Section 7 Step 3.5). Hard requirement: DNS flip at `chat.yral.com` (M1) to achieve zero mobile code change at cutover. Streaming (M2) is the biggest decision — recommendation to ship v2 without streaming, add in v2.1 when mobile is ready. Saikat + Sarvesh + Shivam sign-off needed BEFORE Phase 0 starts.

I will not save these until you approve the plan, and each save will be its own file that I can update/delete later as facts change.

---

## 11.8 Decisions Locked on 2026-04-23 (Saikat + Rishi sign-off)

Responses to the mobile-change-audit memo (Section 7 Step 3.5) and the infrastructure questions:

### Decisions locked (no further debate needed)

| # | Decision | Answer | Impact on plan |
|---|---|---|---|
| **Servers (Q1)** | Provision rishi-4/5/6 for v2 | ✅ **DONE 2026-04-23**. rishi-4 = Swarm manager + state-primary, rishi-5 = manager + edge/observability, rishi-6 = manager + compute/Langfuse. Hardware: Intel i7-6700, 62.6 GB RAM, 2× 512 GB NVMe each, Ubuntu 24.04.4. | Phase 0 step 1 unblocked. |
| **Caddy routing (Q2)** | Route `chat.yral.com` (today) / `chat-ai.rishi.yral.com` (after Python go-live) → rishi-4/5/6 backend | ✅ **FEASIBLE**. Caddy on rishi-1/2 can be configured to upstream-proxy the new cluster. Rishi has `rishi-deploy` SSH user access. Config patterns live in the existing infra template's `caddy/snippet.caddy.template` (plus branch `rishi/app-ha-caddy-multi-upstream` explores multi-upstream). **DO NOT flip live routing until v2 is production-ready.** | Cutover via Caddy routing confirmed. Phase 4 (cutover) can proceed via existing Caddy on rishi-1/2 — no DNS change needed, just Caddy config update. Mobile URL unchanged. |
| **Sentry (Q3)** | Reuse existing Sentry for v2 services | ✅ **USE `sentry.rishi.yral.com`** (Rishi's self-hosted on rishi-3), NOT `apm.yral.com` (team-shared). | Locked across plan + memories. |
| **Langfuse (Q4)** | Self-host Langfuse on rishi-4 for LLM tracing | ✅ **APPROVED**. | Phase 0 step 4 confirmed. |
| **Redis (Q5)** | Add Redis Cluster as new dependency on rishi-4/5/6 | ✅ **APPROVED**. | Phase 0 step 3 confirmed. |
| **Streaming (Q9)** | SSE streaming in v2.0 or defer to v2.1? | ✅ **STREAMING IS IN FOR v2.0**. Rishi wants first-token <200ms as a headline feature. | Major — triggers mobile work. Details in Section 11.8.1 below. |
| **JWT security gap (Q12)** | Fix Ravi's `insecure_disable_signature_validation` in v2 | ✅ **TAKE THE SAFER APPROACH** (Ravi unresponsive). v2 validates RS256 signatures via JWKS from `auth.yral.com/.well-known/jwks.json`, cached in Redis 1hr TTL. | Plan A2 confirmed. |
| **Plan B.0 re-engagement mobile bundle (Q10)** | Include presence heartbeat + chip-dismissal-on-auto-message in v2.0 mobile changes | ✅ **BUNDLE IT**. | Small additional client work, wraps with streaming changes. |
| **Cost ceiling (Q14)** | Monthly budget cap for LLM + infra | ✅ **NO COST CONTROLS until product-market fit.** Spend what it takes to make chat best-in-world. | Architecture gets designed for quality, not cost. Multi-provider routing still valuable for latency/availability, not primarily cost. Self-hosted LLM track deprioritized (Q6). |
| **Team (Q15)** | Who builds v2? | Solo Rishi + **Claude Code agents + Codex in parallel**. Additional team allocation available if needed. | Roadmap assumes Rishi-led + AI-pair workflow. Plan explicitly includes agent-delegatable chunks of work. |
| **Self-hosted LLM GPU (Q6)** | When do we get GPU capacity? | 🟡 **Delayed** — not a Phase 5 priority anymore. LLM-agnostic abstraction still built in from day 1; self-host is optional future. | Phase 5+ self-host milestone deprioritized (cost no longer the driver; product-fit is). |
| **Mobile base URL (Q7)** | How does mobile get chat API URL today? | **Hardcoded.** File: `/shared/core/.../AppConfigurations.kt`, `const val CHAT_BASE_URL = "chat-ai.rishi.yral.com"`. Mobile stack is Kotlin Multiplatform (KMP), shared ~90% across iOS + Android; HTTP client is Ktor. Firebase Remote Config exists for feature flags but base URL is NOT remote-configured (opportunity for v2.1+). | Critical correction: **the URL mobile hits is `chat-ai.rishi.yral.com`, not `chat.yral.com`**. Rishi said the new Python prod URL will be `yral-chat-ai.rishi.yral.com` — need to clarify whether mobile will be updated to that or Caddy will keep routing `chat-ai.rishi.yral.com`. |
| **Mobile 402 paywall shape (Q8)** | What JSON does mobile expect on paywall? | **The paywall is NOT a 402 HTTP response.** Billing is a separate Google Play IAP flow — mobile POSTs `purchase_token` to `/google/chat-access/grant`, polls `/google/chat-access/check`. The shared response envelope is `ApiResponse<T> { success, msg, error, data }` with `ChatAccessDataDto { hasAccess, expiresAt }` for access checks. The chat endpoint itself doesn't enforce paywall via HTTP status — mobile checks access BEFORE sending a message and triggers the IAP sheet if needed. | **Major correction to the plan.** V2's paywall logic lives in `yral-rishi-agent-public-api` as a pre-turn gate that returns an access-check response in this exact shape. Any error on the chat message endpoint is a regular error, not a paywall. |
| **Plan approval (Q13)** | Green-light to start Phase 0 building | 🟡 **NOT YET.** Explicit user rule: **plan only with me until I say "build"**. Architect every phase in detail first, freeze, then Rishi approves, then build. | Hard rule — no code gets written until explicit approval. Saved to memory as a new feedback rule. |

### 11.8.1 Streaming implementation plan (Q9 accepted — v2.0 ships with SSE)

Rishi wants streaming in v2.0. This forces mobile work from Sarvesh/Shivam. Here's what needs to happen on both sides, based on research into `github.com/dolr-ai/yral-mobile`.

**Mobile stack facts (research findings):**
- **Kotlin Multiplatform (KMP)**: ~90% shared code; thin SwiftUI wrapper on iOS, thin Compose wrapper on Android. This is a WIN — any streaming work done in shared code covers both platforms simultaneously.
- **HTTP client: Ktor Client** (KMP cross-platform) — config in `/shared/libs/http/src/commonMain/kotlin/com/yral/shared/http/HttpClientFactory.kt`. No built-in SSE parser; will need to add.
- **No existing WebSocket or SSE usage anywhere in app** — net-new pattern for this team.
- **UI is streaming-ready**: `ConversationMessageBubble.kt` already renders Markdown via Compose, already has an `isWaiting: Boolean` loading state. Replacing loading dots with incremental text is trivial — just update `content` field as tokens arrive and Compose recomposes.
- **Base URL hardcoded** in `AppConfigurations.kt`: `const val CHAT_BASE_URL = "chat-ai.rishi.yral.com"`. No remote config driving it.
- **Firebase Remote Config available** but not used for base URL — can be used to feature-flag SSE mode on/off during rollout.
- **Chat send method**: `sendMessageJson()` in `/shared/features/chat/.../ChatRemoteDataSource.kt` line 177-193 — this is what changes from POST-to-JSON to POST-to-SSE-stream.

**What mobile team needs to build** (ask Sarvesh + Shivam):
1. **Add SSE parsing to Ktor client.** Ktor has raw streaming via `HttpClient.request()` returning a `ByteReadChannel`; need a lightweight SSE line parser on top (splits on `\n\n`, reads `data:` prefixed lines). ~100 lines of Kotlin in `/shared/libs/http/`. Reusable if the app ever streams anything else.
2. **New chat-send path** — `sendMessageStream(conversationId, message): Flow<ChatStreamEvent>` returning Kotlin Flow emitting tokens. Replaces `sendMessageJson()` (keep old path as fallback for non-SSE backends / feature flag off).
3. **Event types** — `ChatStreamEvent.TokenDelta(content_fragment)`, `ChatStreamEvent.Complete(full_message)`, `ChatStreamEvent.Error(reason)`. V2 backend emits these shapes; mobile parses + maps to UI.
4. **UI wiring** — in ConversationMessageBubble.kt's parent (likely ConversationViewModel), on send:
   - Insert assistant message with empty content + `isWaiting = true`
   - Collect Flow; on each `TokenDelta`, append fragment to the assistant message content (Compose recomposes automatically); on `Complete`, mark not-waiting and persist via existing DB insert; on `Error`, show error state.
5. **Error handling** — network drop mid-stream → retry from last token? Or restart whole turn? Simplest: restart whole turn, make it idempotent server-side via client_message_id.
6. **Feature flag via Firebase Remote Config** — `enable_chat_streaming: bool`, defaulting off. Turn on per cohort during v2 canary. If streaming path errors, fallback to JSON path (both paths maintained for ~3-6 months post-launch).
7. **Presence heartbeat (Plan B.0 nudge)** — every 10 seconds while chat screen is in foreground, emit a lightweight ping to `/api/v1/chat/conversations/{id}/presence`. Reuse Ktor client. Backend scheduler uses this to reset the inactivity timer. ~50 lines.
8. **Chip dismissal on auto-fired message** — when a bot-authored message arrives (streamed or otherwise) and the current conversation still has Default Prompts visible, dismiss them. Small UI state change.

**What backend needs to build** (v2 responsibility):
1. V2 `yral-rishi-agent-public-api` exposes `POST /api/v2/chat/conversations/{id}/messages` with SSE response (Content-Type: `text/event-stream`). Emits token deltas as `data: {"type":"token","content":"..."}\n\n`, final `data: {"type":"complete","message":{...}}\n\n`, errors as `data: {"type":"error","message":"..."}\n\n`.
2. Orchestrator streams LLM tokens directly to the SSE response (bridge Gemini's streaming API → SSE), bypassing full-message buffering.
3. Parallel to streaming: orchestrator emits Redis Streams events (`message.sent`, `turn.completed`, `memory.candidate`) for async consumers.
4. Legacy `POST /api/v1/chat/conversations/{id}/messages` (non-streaming) stays available throughout v2 as the fallback path mobile hits when feature flag is off.

**Coordination ask for Sarvesh + Shivam:**
- Confirm the above plan makes sense from mobile side
- Estimate mobile-side effort (my guess: 1-2 weeks for KMP-shared SSE client + Compose wiring + feature flag; bundle Plan B.0 presence ping in the same sprint for efficiency)
- Agree on event-shape contract (TokenDelta / Complete / Error JSON) before v2 API is frozen
- Decide on testing approach (mock SSE server during development, integration tests with real v2 backend once Phase 1 is up)

**Success criteria for streaming (v2.0 launch gate):**
- Time-to-first-token <200ms at p95 (critical)
- No increase in chat error rate vs. baseline Python service
- No increase in crash rate on mobile
- User-perceived latency survey (informal): feels faster

**Fallback plan if streaming proves unstable:**
- Firebase Remote Config flips streaming off globally; mobile falls back to JSON path; no user-visible outage.
- Root-cause, fix, re-enable.

### 11.8.2 Other corrections and clarifications

- **Current mobile chat URL is `chat-ai.rishi.yral.com`**, not `chat.yral.com` — the DOLR context doc was outdated on this. Plan's cutover strategy updated: Caddy on rishi-1/2 continues to own `chat-ai.rishi.yral.com` and gradually upstreams to rishi-4/5/6 as v2 goes live. Mobile base URL unchanged.
- **Rishi's statement that prod URL will become `yral-chat-ai.rishi.yral.com`** after Python go-live — need to clarify whether this means (a) mobile code bumps to the new URL in a future release, or (b) Caddy aliases both domains to the Python backend. Either way, v2 must serve whichever URL mobile hits at cutover time.
- **Paywall is NOT a 402 response** — it's a pre-chat access check via yral-billing. Corrects my earlier (incorrect) assumption. V2 orchestrator calls billing to verify `hasAccess` BEFORE generating a response; if no access, returns the standard `ApiResponse` error envelope so mobile triggers the Google Play IAP sheet.
- **Response envelope: `ApiResponse<T> { success, msg, error, data }`** — use this exact shape across all v2 endpoints so mobile's existing parsing works unchanged.

### 11.8.3 The "plan-only until approved" rule (saved to memory as hard feedback)

Rishi explicit rule (2026-04-23): **"Plan only with me. We'll start discussing around planning each and every phase in detail and discussing the architecture. Once we are sure about the architecture, we can freeze the plan and start building (start building only when I give explicit approval to build)."**

**How I apply this from now on:**
- No code gets written for v2 until Rishi says "build".
- Phase 0 doesn't kick off without approval. Right now we're in architecture-conversation mode only.
- I propose plans, open questions, alternatives; Rishi decides.
- If I spot an ambiguity or missing piece, I raise it as a question, not an implementation.
- If Rishi asks exploratory questions, I answer them with options, not defaults.
- Build kickoff = a distinct, explicit user instruction. Not implied by agreement on any sub-topic.

---

## 12. What I need from you next

Pick one or more to lock in:

- **(a) Approve the plan as-is** → I'll save the memories from Section 11.5, then start on Phase 0 detailed specs: new-template repo scaffolding, Ansible for rishi-4/5/6 provisioning, Vault/Sentry wiring, Postgres+Redis+Langfuse install playbooks, the empty skeleton services.
- **(b) Ask Saikat for the new servers first** → before I write any more plan, draft the formal request message for rishi-4/5/6 with justification (cluster isolation, 13 services, HA DB, Redis, GPU capacity for future self-hosted LLM). Need his approval before spending time on Phase 0 designs.
- **(c) Deep-dive a specific service** → which one? Examples: `yral-conversation-turn-orchestrator` (the brain), `yral-soul-file-library` (the Soul File layered system), `yral-creator-studio` (the Soul File Coach), `yral-content-safety-and-moderation` (the non-negotiable safety layer).
- **(d) Baseline measurement first** → before committing to the SLO, capture 1 week of prod latency from Rust chat + Python chat-ai. I'll write the Sentry-export + Langfuse-instrumentation script. This is the number we promise to beat. Without it, the SLO is abstract.
- **(e) Mobile-client coordination with Sarvesh/Shivam** → draft the architecture memo explaining the DNS-flip-only plan and get their review before anything else. If they say the API contract needs to change, we need to know now, not in week 18.
- **(f) LLM provider & GPU roadmap** → research self-hosting options (which open-weight model class, GPU requirements, cost estimate at 10K/100K DAU, latency benchmarks on Hetzner GPU or other), write up a proposal for Saikat.
- **(g) Open questions first** → before executing, answer Section 11 hard questions (cost ceiling per user, moderation liability, etc.).
- **(h) Narrow the scope** → the plan is large by design (you asked for extensive). If you want a focused 4-week "Phase 0 only" plan separate from the 6-month vision, I'll write that.
- **(i) Something else** → tell me.

When you decide, say which (a-i) and I'll save memories + move to execution.
