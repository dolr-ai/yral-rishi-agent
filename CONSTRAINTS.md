# V2 Chat — Hard Constraints (reviewable, one per row)

> The tight, go/no-go list of every hard constraint the v2 build operates under. Nothing on this list is aspirational — each row is non-negotiable unless Rishi explicitly lifts it. Reviewed item-by-item before any code is written.
>
> Status legend: 🔒 locked (cannot change without explicit Rishi reversal) · 🟡 provisional (needs Rishi confirm) · 🟢 resolved (answered, folded into plan)
>
> Related docs:
> - [`README.md`](./README.md) — full v2 plan (1500+ lines): capability blueprints, roadmap, architecture
> - [`V2_TEMPLATE_AND_CLUSTER_PLAN.md`](./V2_TEMPLATE_AND_CLUSTER_PLAN.md) — template + rishi-4/5/6 cluster details

---

## Category A — The Safety Covenant (the "do not break these or everything breaks")

| # | Constraint | Status | Source | How we verify |
|---|---|---|---|---|
| A1 | **No deletions** of ANY existing artifact (repos, stacks, DNS records, Caddy routes, Postgres tables, GitHub secrets, memory files, servers) without explicit per-item Rishi approval | 🔒 | `feedback_no_delete_without_asking.md`; Rishi 2026-04-22 | Every proposed deletion has an explicit YES from Rishi in chat before executing; double-confirmation required |
| A2 | **Rishi-1/2/3 are LIVE production servers — hands off.** Read-only access only when needed (key: `~/.ssh/rishi-hetzner-ci-key`, user `deploy`); NO writes, NO deploys, NO config changes | 🔒 | Rishi 2026-04-23 | Every access to rishi-1/2/3 goes through read-only commands (cat, ls, journalctl) — anything that would write requires typed approval |
| A3 | **One mobile-client change maximum.** Mobile base URL stays at `chat-ai.rishi.yral.com` through cutover. Streaming (v2.0) + Plan B.0 presence heartbeat are the approved mobile changes — bundled | 🔒 | Rishi 2026-04-22/23 | Section 11.8.1 of README enumerates exact mobile asks; anything not listed there = needs Sarvesh/Shivam + Saikat re-approval |
| A4 | **All AI influencers preserved.** User chat history MAY be discarded; influencer ID, Soul File, creator link, earnings, followers all carry forward (one-time ETL + continuous CDC) | 🔒 | Rishi 2026-04-22 | Migration script inventoried in README Section 7 Step 2; influencer count before cutover == influencer count after |
| A5 | **Plan-only mode until Rishi types "build".** No code, no repo creation, no server provisioning, no CI pushes for v2 services until explicit build approval | 🔒 | `feedback_plan_only_until_explicit_build_approval.md`; Rishi 2026-04-23 | No actions taken that materialize the plan into running systems; this doc, README, memories all editable — nothing else |

## Category B — Naming & Readability (the "Rishi must be able to read it")

| # | Constraint | Status | Source | How we verify |
|---|---|---|---|---|
| B1 | **Every name reads as English** to a non-programmer. Applies to: service names, DB tables, DB columns, Python functions, Python vars, env vars, Docker stacks/services/secrets/configs/networks/volumes, GitHub repos/branches, Docker image tags, GitHub Actions jobs, Caddy snippets, Prometheus metrics, Grafana panels, Langfuse trace names, Sentry project names, Uptime Kuma monitors, alert rule names, log field names | 🔒 | `feedback_explicit_naming.md`; Rishi 2026-04-22/23 (restated as "very very important") | CI lint block-lists abbreviations; PR template asks reviewer "do all names read as English?"; template CLAUDE.md opens with this rule |
| B2 | **Only allowed abbreviations**: `id`, `url`, `api`, `http`, `json`, `sql`, `utc`, `tls`, `dns`, `ssl`, `css`, `html`, `uuid`, `ip`. Nothing else. `db` → `database`, `cfg` → `config`, `svc` → `service`, etc. | 🔒 | `feedback_explicit_naming.md` | CI lint regex block-list |
| B3 | **V2 service names** follow `yral-rishi-chat-ai-v2-<explicit-english-purpose>` pattern — must include `rishi` (owner), `chat-ai-v2` (version), English purpose | 🔒 | `feedback_explicit_service_naming_v2.md` | `new-service.sh` refuses names that don't match regex or exceed 63-char Swarm limit |
| B4 | **Use DOLR product vocabulary** — "Soul File" (not "system prompt"), "AI Influencer" (not "bot"), "Chat as Human" (exact phrase), "Default Prompts", "Message Inbox", "Switch Profiles" | 🔒 | `reference_yral_soul_file_terminology.md`; `dolr-ai/yral/blob/main/context-for-agents.md` | Code review catches deviations; template docs use these terms exclusively |

## Category C — Infrastructure & Servers

| # | Constraint | Status | Source | How we verify |
|---|---|---|---|---|
| C1 | **V2 cluster lives on rishi-4/5/6.** Legacy rishi-1/2/3 untouched | 🔒 | Rishi 2026-04-22; Saikat allocation 2026-04-23 | Every new deploy targets rishi-4/5/6 only; Swarm manager runs on rishi-4/5/6 with its own manager token |
| C2 | **Docker Swarm** — orchestrator for v2. No Kubernetes unless Rishi explicitly upgrades later | 🔒 | Rishi 2026-04-22 | Swarm manifests; no K8s YAML anywhere |
| C3 | **Swarm-only networking. No host ports exposed except `:443` on edge (via Swarm ingress).** All inter-service traffic on overlay. Three encrypted overlays: `yral-v2-public-web`, `yral-v2-internal`, `yral-v2-data-plane` | 🔒 | Saikat directive 2026-04-23 (captured in V2_TEMPLATE_AND_CLUSTER_PLAN §1.7) | CI lint (`yq`) rejects any stack file publishing a port other than the edge Caddy 443; UFW rules confirm only 443 open on rishi-4/5 |
| C4 | **Sentry for v2 = `sentry.rishi.yral.com`** (Rishi's self-hosted on rishi-3), NOT `apm.yral.com` (team-shared) | 🔒 | Rishi 2026-04-23 | Every service's SENTRY_DSN points at sentry.rishi.yral.com |
| C5 | **Caddy routing via rishi-1/2 as edge** to rishi-4/5. Cloudflare DNS stays as-is (no new records); rishi-1/2 Caddy reverse-proxies specific subdomains to v2 cluster | 🔒 | Saikat confirmed 2026-04-23 (V2_TEMPLATE_AND_CLUSTER_PLAN §4) | Caddy snippets on rishi-1/2 drop in via SSH as `deploy` user; `caddy reload` zero-downtime |
| C6 | **No literal IPs in code/templates/scripts/CI.** IPs live in `cluster.hosts.yaml` (shape only) + GitHub Secrets (`RISHI_N_PUBLIC_IPV4`). Render step merges at deploy time. **IPs in the plan doc ARE fine** — this rule is about dynamism of deployed systems, not documents | 🔒 | `feedback_no_hardcoded_ips.md`; Rishi 2026-04-23 (clarified docs OK) | CI regex blocks literal IPs in committed code; PR template asks "any literal IPs in this PR?" |
| C7 | **Maximum dynamism / no hardcoded values in code.** Anything shared across services lives in one config that all services read (shared-config.yaml); versions, timeouts, thresholds, model names, all configurable | 🔒 | Rishi 2026-04-23 | Template `shared-config.yaml`; every service reads from it; CI lint for new magic numbers in code |
| C8 | **SSH access via `~/.ssh/rishi-hetzner-ci-key`** for all 6 nodes. `rishi-deploy` user on rishi-4/5/6 (matches legacy convention). Narrow sudoers rule (docker + specific systemctl + journalctl + apt); `root` is break-glass only | 🔒 | `reference_saikat_server_allocation.md` | SSH tests; sudoers file content reviewed |
| C9 | **Datacenter placement**: rishi-1/2/3/4 = Falkenstein (FSN1, confirmed by IP range). rishi-5 ambiguous. rishi-6 likely Nuremberg (NBG1). **Verification deferred to day-0 provisioning** (Rishi choice 2026-04-23, option c — check `/etc/hetzner-provision` + `cat /proc/mdstat` on first SSH). If rishi-6 is cross-DC: Patroni sync replica stays in FSN1 (rishi-4+5); rishi-6 runs async-only | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §5 + §10 + Rishi confirm 2026-04-23 | Day-0 checklist item; Patroni topology adjusted based on result before any data is written |
| C10 | **TLS terminates on rishi-1/2 Caddy**, not on v2 cluster. rishi-1/2 already has ACME certs for `*.rishi.yral.com` from existing setup; they forward-proxy HTTPS traffic to rishi-4/5 over the public network (same-datacenter ~1ms). Rishi-4/5 Caddy (Swarm service) receives HTTPS from rishi-1/2 and forwards to overlay. **No ACME on v2 cluster at Phase 0-1.** Future migration path if needed: add DNS-01 via Cloudflare API on v2 cluster | 🔒 | Rishi 2026-04-23 (Q7 option c) | Caddy snippet on rishi-1/2 does `reverse_proxy https://rishi-4:443 https://rishi-5:443 { health_uri /health/ready }`; rishi-4/5 Caddy inside Swarm serves the request |
| C11 | **Redis HA via Sentinel** (not Cluster). Primary on rishi-4, replica on rishi-5, Sentinel quorum on rishi-4/5/6. Single dataset (non-sharded); one primary for all writes. All Python services use `redis.sentinel.Sentinel` client to discover current primary. Sentinel auto-fails-over on primary loss. Fits easily in 8-16GB RAM at Month-12 projection | 🔒 | Rishi 2026-04-23 (Q8 option a) | Redis stack in bootstrap folder; primary+replica+sentinel containers; template ships with Sentinel-aware `redis_client.py`; fail-over verified in Phase 0 chaos tests |

## Category D — Secrets, Backups, Observability

| # | Constraint | Status | Source | How we verify |
|---|---|---|---|---|
| D1 | **GitHub Secrets primary per-service, Vault only for team-shared** (notification key etc.). No new secrets in Vault. Runtime injection via env vars populated from Swarm secrets. Never in images, git, CI yaml body | 🔒 | `feedback_secrets_github_primary_vault_shared.md` | `gh secret set` for per-service; `infra.get_secret()` for shared; CI gitleaks scan |
| D2 | **Three-layer backup strategy.** L1 Patroni HA (sync commit on ≥1 replica), L2 WAL-G continuous PITR to Hetzner S3 (7-day retention), L3 daily pg_dump to Hetzner S3 (30-day) + weekly to Backblaze B2 (3-month offsite) + monthly encrypted cold (1-year). Weekly restore drill + quarterly DR simulation | 🔒 | `feedback_three_layer_backup.md` | Restore drill is a CI job; failure pages team |
| D3 | **All v2 services tagged `service=<name>` in Sentry** and `environment=production` or `environment=staging` | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §5 staging | Sentry project config; every service emits tagged events |
| D4 | **Langfuse self-hosted on rishi-6** traces every LLM call (prompt, response, tokens, latency, cost). Joinable to Sentry + Prometheus via correlation ID | 🔒 | V2 plan §2.5 + V2_TEMPLATE_AND_CLUSTER_PLAN §6.5 | Template middleware auto-traces; absent = CI fail |
| D5 | **Uptime Kuma monitors at `status.yral.com`** hit every service's `/health/ready`; self-service monitor creation needs Saikat access confirm | 🟡 | V2_TEMPLATE_AND_CLUSTER_PLAN §10 Q5 | New service spawn auto-registers via Kuma API |
| D6 | **Alertmanager destination: Google Chat webhook** — same mechanism current chat-ai uses for admin notifications. All critical alerts (replicas down, Patroni lag, latency regression, disk full, LLM provider errors, backup failures) route here. Team-wide visibility for Rishi + Saikat | 🔒 | Rishi 2026-04-23 (Q9 option a) | Alertmanager config in cluster-bootstrap folder uses Google Chat webhook URL from GitHub Secret `GOOGLE_CHAT_WEBHOOK_URL` |

## Category E — Performance & Product

| # | Constraint | Status | Source | How we verify |
|---|---|---|---|---|
| E1 | **Latency never regresses.** V2 p50/p95/p99/p99.9 ≤ current Rust + Python chat baselines at every percentile, every rollout step. Automatic rollback on regression | 🔒 | `feedback_latency_never_regresses.md`; Rishi 2026-04-23 | Baseline captured in Phase 0; CI latency gate on every PR; hourly comparison job pages on regression |
| E2 | **Streaming in v2.0** — SSE first token <200ms at p95. Ktor SSE parser + Firebase Remote Config flag + fallback to JSON path | 🔒 | Rishi 2026-04-23 | Synthetic user heartbeat + Langfuse time-to-first-token metric |
| E3 | **LLM-agnostic by design.** Orchestrator talks to `llm-client` abstraction; switching providers = config change not rewrite | 🔒 | `feedback_llm_agnostic_design.md` | One trait, many impls; feature flags route per archetype |
| E4 | **No unit-economics cost controls until product-market fit.** Spend whatever needed on LLMs and infra. BUT a runaway-protection circuit breaker IS active with a very high per-user daily cap (e.g., ₹500/user/day). Per-turn cost tracked in Langfuse for visibility; runaway breaker only fires on clear abuse/bug cases | 🔒 | `feedback_no_cost_controls_until_product_market_fit.md`; Rishi 2026-04-23 (resolved: option C — track + active-but-high cap as runaway protection only) | Langfuse tracks cost per turn; circuit breaker exists in template but cap is set 10-100× expected normal user cost. Prevents a runaway loop or compromised user from draining $10K before anyone notices |
| E5 | **H2H chat + Chat as Human + H2AI all same system** from day 1. Unified `conversation_type` column; `participant_b_id` scaffolding preserved | 🔒 | DOLR context doc; current schema already supports | Data model in `yral-rishi-chat-ai-v2-conversation-*` schema |
| E6 | **Auth via `auth.yral.com` (yral-auth-v2)** — OAuth2/PKCE + RS256 JWT validated against JWKS (fixing Ravi's `insecure_disable_signature_validation` gap) | 🔒 | `reference_yral_auth_billing_architecture.md`; Rishi 2026-04-23 | Public-api middleware fetches JWKS, caches in Redis 1hr, validates signature |
| E7 | **Billing via `yral-billing` (Google Play IAP)** — pre-chat access check, ₹9/24hr per bot. Paywall is NOT a 402 response; it's `ApiResponse<ChatAccessDataDto{hasAccess,expiresAt}>` returned before the chat call | 🔒 | `reference_yral_auth_billing_architecture.md`; `reference_yral_mobile_architecture.md` | Orchestrator calls yral-billing before each turn; response shape matches exactly |
| E8 | **Soul File (layered)** is the product term; 4 layers: global / archetype / per-influencer / per-user-segment | 🔒 | `reference_yral_soul_file_terminology.md` | Schema uses `soul_file_*` names; service named `yral-rishi-chat-ai-v2-soul-file-library` |

## Category F — Template & Service Workflow

| # | Constraint | Status | Source | How we verify |
|---|---|---|---|---|
| F1 | **Template-first build order.** New v2 template (`yral-rishi-chat-ai-v2-new-service-template`) built and proven via a throwaway hello-world service BEFORE any real v2 service ships | 🔒 | `feedback_template_first_build.md` | Phase 0 exit criterion; hello-world must pass all CI lints + deploy + health + Sentry + Langfuse + Beszel |
| F2 | **Existing `yral-rishi-hetzner-infra-template` is NEVER modified.** V2 template forks and evolves independently | 🔒 | No-delete covenant | Existing repo stays frozen; CI doesn't touch it |
| F3 | **Schema-per-service on ONE shared Patroni cluster** on rishi-4/5/6 (not per-service Patroni). Tenant SQL creates schema + role + GRANTs + connection cap at bootstrap. Correlated-failure risk on cluster outage accepted — mitigated by HA + 3-layer backup + chaos tests | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.2 + Rishi confirm 2026-04-23 (option a) | Service bootstrap script; each service sees only its own schema |
| F4 | **Staging via namespace separation** (shared infra). ONE Patroni + ONE Redis + ONE Langfuse serve both environments. Separation: `staging_chat_ai_v2_*` schemas, `staging:` Redis key prefix, `environment=staging` Sentry/Langfuse tag. 1-replica per service at 50% prod resources. Weekly reseed from redacted prod snapshot via WAL-G restore | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §5 + Rishi confirm 2026-04-23 (option A) | Every service has staging deploy at `<svc>.staging.v2.rishi.yral.com`; manual "promote to prod" button in GitHub Actions |
| F5 | **arq** as blessed worker library (Redis-backed async queue, fits FastAPI/asyncio, ~1500 LOC) | 🟡 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | Template ships arq-ready; all workers use it |
| F6 | **MCP (Anthropic Model Context Protocol)** as the tool-runtime standard from day 1. Official `mcp` Python SDK | 🔒 | V2 plan §2.1 + V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | Template helper wraps MCP SDK with correlation-ID + Langfuse |
| F7 | **Cluster-bootstrap folder INSIDE the template repo**, not a separate repo. Structure: `yral-rishi-chat-ai-v2-new-service-template/bootstrap/` contains node bootstrap scripts, systemd units, UFW rules, `cluster.hosts.yaml`, `services.yaml`. Fewer moving parts — one repo to clone when onboarding | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §12 + Rishi confirm 2026-04-23 (option b — fold into template repo) | Template repo has `bootstrap/` folder alongside `app/`, `scripts/`, etc. CI from template runs both service-spawn + cluster-bootstrap operations |
| F8 | **5 required docs per service** (DEEP-DIVE / READING-ORDER / CLAUDE / RUNBOOK / SECURITY) inherited from existing template standards | 🔒 | `feedback_documentation_standards.md` (existing memory) | Template scaffolds all 5; CI fails if any missing |
| F9 | **Uniform `/health` three-tier split** — `/health/live` (process alive), `/health/ready` (deps healthy, 503 if not), `/health/deep` (real round-trip, expensive) | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | Swarm uses `ready` for health checks; Uptime Kuma uses `ready`; synthetic user uses `deep` |
| F10 | **Idempotency-key default-on** on all non-GET endpoints; dedupes via Redis 24hr TTL. Per-endpoint opt-out for truly stateless | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | Middleware in template; CI test covers dedup |
| F11 | **Feature flags custom Postgres-table**, ~200 LOC, polled every 30s, on/off + % rollout. Upgradeable to Unleash if experimentation grows | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | Every new feature ships behind a flag |
| F12 | **Python 3.12+ + FastAPI + asyncio + asyncpg uniformly across all 13 services.** Same stack as existing template. No Go/Rust unless a specific service later proves CPU-bound under load. One language = one mental model for a non-programmer Rishi + Claude/Codex agents to navigate | 🔒 | Rishi 2026-04-23 (Q6 option a) | Template `pyproject.toml` pins Python version; CI rejects non-Python service additions without explicit exception |

## Category G — Scale & Projections

| # | Constraint | Status | Source | How we verify |
|---|---|---|---|---|
| G1 | **Scale targets**: today ~25K msgs/day; Month 6 ~300-500K; Month 12 ~1M+. Architecture must hold at 40× without re-architecture | 🔒 | `project_yral_scale_projection.md` | Every architectural choice stress-tested against Month 12 projection |
| G2 | **Horizontal scaling from day 1.** Every service stateless-by-default (except Patroni/Redis/Langfuse); Swarm replicas handle scale-out | 🔒 | V2 plan §2.7.5 | Template ships with 3-replica default for hot path |
| G3 | **pgBouncer in front of Patroni from day 1.** Trivial to add now, painful later | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.2 | Deployed in Phase 0 stateful core |
| G4 | **Multi-LLM-provider routing by Month 6-9** (when single-provider rate limits hit). Day-1 template has the abstraction ready | 🔒 | `feedback_llm_agnostic_design.md` | `llm-client` trait with multiple impls shippable via config flip |

## Category H — Reliability & Safety

| # | Constraint | Status | Source | How we verify |
|---|---|---|---|---|
| H1 | **`yral-v2-swarm-resync.service`** systemd oneshot runs after docker.service to idempotently redeploy all stacks (fixes April 19 `restart:always` unreliable-on-reboot pattern) | 🔒 | `reference_docker_restart_policy_edge_case.md`; V2_TEMPLATE_AND_CLUSTER_PLAN §6.1 | Phase 0 chaos test: reboot rishi-6, every stack back within 2 min |
| H2 | **SHA-rotating Swarm configs** — `name: <stack>_<configname>_<sha8>` + prune old (per the April 20 bug fix pattern) | 🔒 | `reference_template_haproxy_cfg_bug.md` | Every config has SHA suffix; old ones pruned post-rollout |
| H3 | **Chaos tests as Phase 0 exit criteria** — kill rishi-6 (drain), kill rishi-4 Patroni container, fill rishi-5 disk 80%, partition rishi-6 from 4/5 for 10 min. All must pass before any real service runs | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §6.7 | Chaos test scripts in cluster-bootstrap repo; Phase 0 gate |
| H4 | **Crisis detector + content safety ship before canary traffic.** Non-negotiable for mental-health-adjacent chat | 🔒 | V2 plan §6 principle 12 | `yral-rishi-chat-ai-v2-content-safety-and-moderation` live + integrated before any real user hits v2 |
| H5 | **Prompt injection defense middleware** pre-orchestration. Blocks extraction attempts, logs to Sentry with `type=prompt_injection`, returns safe fallback | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | Middleware in public-api; tests include known injection payloads |
| H6 | **PII-aware log redaction.** Message bodies, user names, email, phone NEVER in Loki, Sentry breadcrumbs, Langfuse trace payloads. Structured logger with allow-list of safe fields | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | Code review + CI grep for sensitive field names in log statements |
| H7 | **Shadow traffic middleware** for every new orchestrator change — mirrors real requests to candidate, compares responses offline in Langfuse | 🔒 | V2 plan §2.1 + V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | Deploy flow: every orchestrator PR runs shadow for N days before promote |
| H8 | **Eval harness baked into template.** Held-out 200-prompt set + promptfoo-style runner; CI runs on every PR touching LLM paths; diff posted to PR | 🔒 | V2 plan §2.1 + V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | `evals/` folder in template; CI job + PR comment bot |
| H9 | **Synthetic user heartbeat** — one canary bot per env sends real API turn every 5 min via real auth; alerts on failure, latency >2× baseline, or eval-metric degradation | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | Container deployed in Phase 0; Grafana dashboard tracks |
| H10 | **Dead letter queue for workers.** arq retries 3× with jittered backoff, then DLQ stream `worker.dlq` with full context. Alert on depth >100 or age >1h | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | Template ships DLQ; Grafana panel + alert |
| H11 | **Schema migration safety net** — every migration PR auto-runs against WAL-restored yesterday-prod snapshot + full test suite; blocks merge on failure | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §7.3 | CI job; uses L2 backup infra |

## Category I — Process

| # | Constraint | Status | Source | How we verify |
|---|---|---|---|---|
| I1 | **Plan-only until explicit Rishi "build" instruction.** No repo creation, no server bootstrap, no code writing for v2 services | 🔒 | `feedback_plan_only_until_explicit_build_approval.md` | All actions that would materialize plan into running systems require typed approval |
| I2 | **Canary deploy + auto-rollback** per service on every deploy: rishi-4 first → health check → rishi-5 → health check → rishi-6. Failure = rollback to last-good image tag | 🔒 | Existing template pattern | Deploy workflow in template; all services inherit |
| I3 | **Manual "promote to prod"** — staging auto-deploys on push to main; prod requires clicking GitHub Actions button after staging smoke + latency gate green | 🔒 | V2_TEMPLATE_AND_CLUSTER_PLAN §6.6 | Workflow exists; no auto-prod deploys |
| I4 | **15-min retrospective after each new service build** — what did we copy-paste? fold into template. What broke twice? fix in template. What doc gap? update template docs | 🔒 | `feedback_template_first_build.md` | Retrospective is part of "done" for each service |
| I5 | **Test locally before CI** (existing rule) — for fixes to current dolr-ai services, reproduce on local stack before pushing to main (which auto-deploys) | 🔒 | `feedback_test_locally_before_ci.md` (existing memory) | Applies to any hotfix we might do on existing services during v2 build |

---

## Appendix A — Conflicts Resolved 2026-04-23

All four previous conflicts resolved:

1. **Cost-cap vs no-cost-ceiling** → Option C: **runaway-protection cap only**. Track cost per turn in Langfuse (no gating for normal usage). Active circuit breaker with a very high per-user daily cap (e.g., ₹500/user/day) as a safety net against abuse, bugs, or infinite loops — NOT a unit-economics control. Row E4 reflects this.
2. **Shared Patroni vs per-service** → Option A: **shared Patroni, schema per service**. Correlated outage risk accepted — mitigated by HA (3 nodes, sync commit on ≥1 replica), chaos tests (Phase 0 exit criterion), 3-layer backup (L1 HA + L2 WAL PITR + L3 offsite), and schema isolation (per-service role, grants, connection cap). Row F3 🔒.
3. **Staging shared vs isolated** → Option A: **shared infra, namespace separation** (same Patroni / Redis / Langfuse, separated by schema prefix / Redis key prefix / environment tag). Weekly reseed from redacted prod snapshot. Row F4 🔒.
4. **Cluster-bootstrap repo** → Option B: **fold INTO template repo as `bootstrap/` folder**. Fewer moving parts. Row F7 🔒.

All resolutions committed to memory + plan. No outstanding conflicts.

## Appendix B — Open Questions (not yet constraints)

See V2_TEMPLATE_AND_CLUSTER_PLAN §10 for full list. Highlights:

- Disk layout on rishi-4/5/6 (RAID1 preferred; verify day-0 via `/proc/mdstat`)
- Rishi-1/2 Caddy SLA (our v2 availability is coupled; worth asking Saikat)
- Sentry self-service project creation permission on `sentry.rishi.yral.com`
- Rishi-6 datacenter verification (Falkenstein vs Nuremberg) — affects Patroni topology
- Backup off-site pair (Backblaze B2 vs alternatives)

---

*Last updated: 2026-04-23. Review cadence: before any phase transition. Any row's status change requires a git commit with explanation.*
