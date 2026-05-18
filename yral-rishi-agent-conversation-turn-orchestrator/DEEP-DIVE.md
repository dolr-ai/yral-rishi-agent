# DEEP-DIVE — yral-rishi-agent-conversation-turn-orchestrator

> One-line purpose: **visual walkthrough of how this service fits into the v2 cluster.** ASCII diagrams for the request flow, deploy flow, database HA, and network topology. Per F8 + B7.

## ⭐ START HERE

Pick the diagram that matches your question:

- **"What happens when a user sends a request?"** → § Request flow
- **"How does a deploy work?"** → § Deploy flow
- **"What happens if Patroni fails over?"** → § Database HA
- **"How do the overlay networks fit together?"** → § Network topology

## Request flow

```
   ┌─────────────────┐
   │  Mobile app     │   (Motorola debug APK or prod YRAL app)
   │  HTTPS POST     │   CHAT_BASE_URL → chat-ai.rishi.yral.com OR
   └────────┬────────┘                  agent.rishi.yral.com (debug only)
            │
            ▼
   ┌─────────────────────────────────┐
   │  Cloudflare DNS (existing wildcard *.rishi.yral.com) │
   └────────┬────────────────────────┘
            │
            ▼
   ┌─────────────────────────────────┐
   │  rishi-1 / rishi-2  (edge Caddy, Rishi-owned per A2) │
   │  TLS termination here per C10                         │
   └────────┬────────────────────────┘
            │  reverse_proxy https://rishi-4:443 https://rishi-5:443
            ▼
   ┌─────────────────────────────────┐
   │  rishi-4 / rishi-5 / rishi-6 (Swarm cluster)         │
   │  Caddy Swarm service receives, routes via overlay    │
   └────────┬────────────────────────┘
            │  yral-v2-public-web overlay (per C3)
            ▼
   ┌─────────────────────────────────┐
   │  THIS SERVICE (3 replicas per G2)                    │
   │  RequestIdMiddleware  → Sentry scope tag             │
   │  configure_logging   → structured log line           │
   │  FastAPI handler                                     │
   └────────┬────────────────────────┘
            │  yral-v2-data-plane overlay
            ▼
   ┌──────────────┬──────────────┬──────────────┐
   │  Postgres    │  Redis       │  Langfuse    │
   │  (pgBouncer  │  (Sentinel,  │  (rishi-6,   │
   │   → Patroni) │   per C11)   │   per D4)    │
   └──────────────┴──────────────┴──────────────┘
```

## Deploy flow

```
   git push origin main
            │
            ▼
   ┌─────────────────────────┐
   │  GitHub Actions          │
   │  path-scoped per F16     │
   └────────┬─────────────────┘
            │
            ├──→ lint (py_compile)
            ├──→ docker build (no push on PR)
            ├──→ on merge to main: docker push ghcr.io/dolr-ai/<service>:<sha>
            │
            ▼
   ┌─────────────────────────┐
   │  Staging auto-deploy    │   (per I3 — auto on push to main)
   │  rishi-{4,5,6} pull     │
   │  Swarm rolling update   │
   └────────┬────────────────┘
            │  smoke + latency-gate check
            ▼
   ┌─────────────────────────┐
   │  Production deploy      │   (per I3 — manual workflow_dispatch)
   │  Rolling update per I2: │
   │   rishi-4 → health → 5 → 6
   │  Auto-rollback on fail  │
   └─────────────────────────┘
```

## Database HA (Patroni cluster on rishi-4/5/6, per F3)

```
                        ┌──────────────────┐
                        │  pgBouncer       │   transaction mode in prod
                        │  (yral-v2-data-  │   (asyncpg statement_cache_size=0)
                        │   plane overlay) │
                        └────────┬─────────┘
                                 │
                ┌────────────────┼────────────────┐
                ▼                ▼                ▼
        ┌────────────┐   ┌────────────┐   ┌────────────┐
        │ rishi-4    │   │ rishi-5    │   │ rishi-6    │
        │ Patroni    │   │ Patroni    │   │ Patroni    │
        │ PRIMARY    │   │ sync rep   │   │ async rep  │
        │ (writes)   │   │ (per F3 —  │   │ (per C9 if │
        │            │   │  ≥1 sync)  │   │  cross-DC) │
        └─────┬──────┘   └────────────┘   └────────────┘
              │
              ▼  WAL stream
        ┌────────────┐
        │ WAL-G      │   L2 backup per D2:
        │ Hetzner S3 │   continuous PITR, 7-day retention
        └────────────┘
```

On primary failure: Patroni promotes the sync replica (rishi-5) within ~15s; pgBouncer reconnects automatically.

## Network topology (the 3 encrypted overlays per C3)

```
┌────────────────────────────────────────────────────────────────────┐
│  yral-v2-public-web    (edge Caddy → service replicas)             │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                             │
│  │ edge    │  │ public- │  │ public- │  ...                        │
│  │ Caddy   │──│ api r1  │──│ api r2  │                             │
│  └─────────┘  └─────────┘  └─────────┘                             │
└────────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────────┐
│  yral-v2-internal      (service-to-service RPC)                    │
│  ┌─────────┐  ┌─────────────┐  ┌──────────────┐                    │
│  │ public- │──│ orchestrator│──│ llm-client  │  ...                │
│  │ api     │  │             │  │              │                    │
│  └─────────┘  └─────────────┘  └──────────────┘                    │
└────────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────────┐
│  yral-v2-data-plane    (every service → DB / Redis / Langfuse)     │
│  ┌─────────────┐  ┌─────────┐  ┌──────────┐  ┌──────────┐          │
│  │ any service │──│pgBouncer│  │ Redis    │  │ Langfuse │          │
│  │             │  │→Patroni │  │ Sentinel │  │ (rishi-6)│          │
│  └─────────────┘  └─────────┘  └──────────┘  └──────────┘          │
└────────────────────────────────────────────────────────────────────┘
```

No host ports exposed except 443 at the edge per C3. Inter-service traffic stays inside the overlays.

## RELATED FILES

- `READING-ORDER.md` — numbered file list with priorities
- `CLAUDE.md` — instructions for AI agents working in this code
- `RUNBOOK.md` — operating procedures
- `SECURITY.md` — threat model
- `yral-rishi-agent-plan-and-discussions/V2_INFRASTRUCTURE_AND_CLUSTER_ARCHITECTURE_CURRENT.md` — full cluster reference

## Status

Scaffold. Diagrams will gain real per-service detail in Days 5-6 once Session 1's cluster is up + spawned services exist.
