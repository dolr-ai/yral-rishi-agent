# DEEP-DIVE — yral-rishi-agent-soul-file-library

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

## Day-4 surface — the 4-layer Soul File composer

The Day-4 PR (`session-4/day-4-soul-file-library-postgres-schema-and-composer`) lands the FIRST stateful surface of this service: a single Postgres table, an asyncpg-backed composer, and the `GET /composed-prompt` HTTP route the orchestrator calls per chat turn.

### The 4 layers (per E8 — order is the public contract)

```
   Layer 1: Global       (scope_key='')                — one row, applies to every turn
   Layer 2: Archetype    (scope_key='companion'/...)   — 3 rows, one per archetype
   Layer 3: Per-Influencer (scope_key=ai_influencer_id) — N rows; Day-4 deferred to Day-4.5 data port
   Layer 4: Per-User-Segment (scope_key='new'/'paying'/'dormant') — 3 rows, one per segment
```

Concatenation order is L1 → L2 → L3 → L4, joined by `LAYER_SEPARATOR` (`\n\n---\n\n`, locked in `shared-config.yaml`). The composer reads the L3 row first to find the archetype the L2 lookup uses.

### Why one table for all 4 layers

Per A2.1 — "one table for all 4 layers (NOT four tables)." Single `soul_file_layers` table + `layer` SMALLINT column + `scope_key` TEXT column models every layer + history without 4× the schema surface.

### Schema (per the Alembic migration)

```
soul_file_layers
  id           UUID PK (gen_random_uuid())
  layer        SMALLINT NOT NULL  (CHECK 1..4)
  scope_key    TEXT NOT NULL      ('' for L1, archetype for L2, influencer_id for L3, segment for L4)
  archetype    TEXT NULL          (L3 rows only — the archetype the composer joins on for L2)
  body         TEXT NOT NULL      (the actual Soul File body)
  version      INTEGER NOT NULL DEFAULT 1
  is_current   BOOLEAN NOT NULL DEFAULT TRUE
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
  created_by   TEXT NULL          (future Prompt-Coach attribution)

Indexes:
  soul_file_layers_one_current_per_slot — PARTIAL UNIQUE (layer, scope_key) WHERE is_current=TRUE
  soul_file_layers_history              — (layer, scope_key, version DESC)
  soul_file_layers_current_by_layer     — (layer) WHERE is_current=TRUE
```

The partial unique index is the durable safety net: exactly ONE current row per `(layer, scope_key)`. The repository's `create_new_version` retire-then-insert pattern keeps this invariant intentional; a concurrent direct-SQL insert would be rejected by the index.

### Request flow

```
   orchestrator (Day-5+)
        │  GET /composed-prompt?influencer_id={uuid}&user_segment={new|paying|dormant}
        │  (overlay yral-v2-internal per C3; no auth on Day 4)
        ▼
   FastAPI route in app/api/composed_prompt_routes.py
        │  → calls compose(influencer_id, user_segment)
        ▼
   four_layer_composer.compose()
        │  Step 1: get_current(LAYER_PER_INFLUENCER, influencer_id)
        │           → None → raise InfluencerSoulFileMissingError → 404
        │           → has L3 row → continue with L3.archetype
        │  Step 2: get_current(LAYER_GLOBAL, '') / (LAYER_ARCHETYPE, L3.archetype) / (LAYER_PER_USER_SEGMENT, user_segment)
        │           → any None → raise SoulFileDataIntegrityError → 500
        │  Step 3: concat L1.body + LAYER_SEPARATOR + L2.body + ... + L4.body
        │  Step 4: version_pin = sha256(versions)[:16]
        ▼
   ComposedPromptResponse { layered_prompt, version_pin, cache_hit=False }
```

### Why the byte-stable prefix matters

Provider-side prompt caching (Anthropic `cache_control: ephemeral`, Gemini context cache, OpenAI prompt cache) keys on the byte-prefix. One drifting byte = full cache miss = 3-10× TTFT regression on cache-eligible turns. The composer guarantees byte-identity for the same `(influencer_id, user_segment)` pair — no timestamps, UUIDs, dates, or random ordering inside the prompt string. Tests assert this with a 5-rep parametrize.

## Status

Day-4 surface live. Day-5+ wiring: Redis cache promote (composer's `cache_hit` flag flips when serving from Redis); provider `cache_control` markers wired by the orchestrator (this service stays opaque-bytes); Day-4.5 data port populates Layer 3 rows from chat-ai's `ai_influencers.system_prompt` (per F11).
