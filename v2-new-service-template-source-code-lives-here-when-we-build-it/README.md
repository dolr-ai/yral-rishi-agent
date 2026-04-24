# Template and Cluster Bootstrap Repo — when we build it

**Status: empty placeholder.** Per Rishi's plan-only rule, no code is written here until explicit "build" approval.

## What will live here

When Rishi approves Phase 0 build, this folder becomes the working copy of the new template repo `github.com/dolr-ai/yral-rishi-agent-new-service-template`.

## Expected structure (per V2_TEMPLATE_AND_CLUSTER_PLAN.md §7)

```
yral-rishi-agent-new-service-template/
├── project.config                  # Per-project single source of truth
├── shared-config.yaml              # Cross-service shared values (no hardcoded anything)
├── pyproject.toml                  # Python 3.12 + FastAPI + asyncio + asyncpg
├── Dockerfile                      # python:3.12-slim, non-root appuser
├── docker-compose.yml              # App container (rishi-4/5/6, Swarm mode)
├── docker-compose.local.yml        # Local dev stack (for my iteration loop)
├── app/
│   ├── main.py                     # FastAPI routes — replace per project
│   ├── database.py                 # Postgres connection pool (via pgBouncer)
│   ├── redis_client.py             # Redis Sentinel-aware client
│   ├── llm_client.py               # LLM abstraction (Gemini / Claude / OpenRouter / self-hosted)
│   ├── soul_file_client.py         # Soul File 4-layer composer client
│   ├── mcp_client.py               # MCP tool-runtime client
│   ├── langfuse_middleware.py      # Auto-traces LLM calls
│   ├── sentry_middleware.py        # Error + breadcrumb middleware (tags service=...)
│   ├── feature_flag_client.py      # Postgres-table-based flags, 30s polling
│   ├── idempotency_middleware.py   # Default-on for non-GET; Redis 24h TTL
│   ├── rate_limit_middleware.py    # Per-(user, influencer) FastAPI rate limit via Redis
│   ├── event_stream.py             # Redis Streams emit + consumer helpers
│   ├── pii_redaction.py            # Log field allowlist + redaction
│   ├── prompt_injection_defense.py # Pre-orchestrator classifier
│   ├── health.py                   # /health/live + /health/ready + /health/deep
│   └── __init__.py
├── bootstrap/                       # Cluster bootstrap folder (per Rishi Q4 lock)
│   ├── cluster.hosts.yaml           # SHAPE only — IPs come from GitHub Secrets
│   ├── services.yaml                # Registry of all 13 services + auto-regen hooks
│   ├── scripts/
│   │   ├── render-cluster-config.py # Merge shape + secrets into runtime config
│   │   ├── generate-ssh-config.sh
│   │   ├── swarm-init.sh
│   │   ├── apply-node-labels.sh
│   │   ├── generate-caddy-snippets.sh  # Generates rishi-1/2 Caddy upstream snippets
│   │   ├── generate-prometheus-targets.sh
│   │   ├── sync-uptime-kuma-monitors.py
│   │   └── bootstrap-new-node.sh
│   ├── systemd/
│   │   └── yral-v2-swarm-resync.service  # Reboot-resilience oneshot
│   └── ufw-rules.sh                 # Host firewall: only :443 on edge + :22 + Swarm ports
├── caddy/
│   ├── snippet.caddy.template       # Per-project Caddy snippet (evolves from existing template)
│   └── rishi-1-2-upstream.caddy.template  # The upstream snippet we drop on rishi-1/2
├── patroni/                         # Shared Patroni cluster config (one cluster, many schemas)
│   ├── Dockerfile
│   ├── patroni.yml
│   └── bootstrap.sh
├── redis/                           # Sentinel-based HA config
│   ├── redis.conf
│   ├── sentinel.conf
│   └── stack.yml
├── migrations/                      # Per-schema SQL migrations (tenant-isolated)
├── evals/                           # Langfuse eval harness, gold prompt set
│   ├── gold-prompts/
│   ├── runner.py
│   └── ci-diff-poster.py
├── tests/                           # pytest unit + integration
├── docs/                            # 5 required docs (DEEP-DIVE, READING-ORDER, CLAUDE, RUNBOOK, SECURITY)
│   ├── DEEP-DIVE.md
│   ├── READING-ORDER.md
│   ├── CLAUDE.md                    # Opens with explicit-naming rule
│   ├── RUNBOOK.md
│   └── SECURITY.md
├── scripts/
│   ├── new-service.sh               # 1-command spawner (forked from existing template, extended)
│   ├── teardown-service.sh          # Per no-delete covenant, requires explicit approval
│   ├── strip-database.sh            # For stateless services
│   ├── local-smoke-test.sh          # Full turn-lifecycle locally
│   └── ci/
│       ├── deploy-app.sh            # Canary deploy (rishi-4 → rishi-5 → rishi-6 with auto-rollback)
│       ├── run-migrations.sh
│       ├── latency-gate-check.py
│       └── schema-migration-safety-net.sh
├── .github/
│   └── workflows/
│       ├── deploy.yml               # CI/CD pipeline
│       ├── backup.yml               # Scheduled 3-layer backup
│       ├── restore-drill.yml        # Weekly automated restore
│       ├── chaos-drill.yml          # Periodic chaos test (safe mode)
│       ├── eval-diff.yml            # Runs Langfuse evals on PR
│       └── lint-naming.yml          # Enforces explicit-English naming block-list
└── README.md
```

## Template principles (from CONSTRAINTS.md Category F)

- **F1** Template-first build — proven via hello-world before real services
- **F2** Existing `yral-rishi-hetzner-infra-template` NEVER modified
- **F3** Schema-per-service on shared Patroni (not per-service Patroni)
- **F4** Staging via namespace separation (shared infra)
- **F5** arq blessed worker lib
- **F6** MCP SDK for tool-runtime
- **F7** Cluster-bootstrap folder INSIDE template repo (not separate)
- **F8** 5 required docs per service
- **F9** Uniform `/health` three-tier split
- **F10** Idempotency-key default-on
- **F11** Postgres-table feature flags
- **F12** Python + FastAPI everywhere
- **F13** GHCR registry
- **F14** Langfuse eval harness baked in
- **F15** Service build order (see refined-capability-priority-order-and-slicing/)

## When build starts

1. Rishi types "build"
2. I clone existing template + create new repo on GHCR
3. Adapt project.config, shared-config.yaml, CI, Docker stuff
4. Prove with `yral-rishi-agent-hello-world` (throwaway, stays as integration test)
5. Rishi approves template proven → real services spawned one by one
