# GLOSSARY — yral-rishi-agent-soul-file-library

> One-line purpose: **plain-English definitions of every domain term used in this template.** Tier-1 readers can skim alphabetically; Tier-2 readers can use it as a reference while reading code.

## ⭐ START HERE

Terms are alphabetical. Each entry: term + 1-2 sentence definition + the relevant CONSTRAINTS row if there is one.

| Term | Plain-English definition |
|---|---|
| **Allowlist** | A list of values explicitly approved. Anything not on the list is blocked. The template's `_FIELD_ALLOWLIST` in `app/logging.py` blocks PII per H6 — any field name not on the list gets redacted before logs ship to Loki/Sentry/Langfuse. |
| **asyncpg** | The asyncio-native Postgres driver for Python 3.12. Doesn't block the event loop while waiting for the database. Replaces the synchronous `psycopg2` from the legacy template. (F12.) |
| **Caddy** | The reverse-proxy / TLS-terminator deployed on rishi-1/2 (edge) and inside the Swarm cluster on rishi-4/5/6. Owned by Rishi via `yral-rishi-hetzner-infra-template` per A2. |
| **Canary deploy** | Rolling deploy that updates one replica at a time and checks its health before moving to the next. Per I2 + the `update_config.parallelism: 1` in `docker-compose.swarm.yml`. |
| **ContextVar** | Python's asyncio-safe primitive (`contextvars.ContextVar`) for per-request state. Propagates across `await` boundaries; the request-ID middleware uses one so log lines + the Sentry scope inherit the same ID. |
| **GHCR** | GitHub Container Registry — `ghcr.io/dolr-ai/<service-name>:<git-sha>` is where every v2 image lives per F13. |
| **Idempotency key** | A client-supplied header that lets the server deduplicate retried requests. The template's middleware (lands later per F10) dedupes non-GET requests via a Redis 24-hr TTL key. |
| **JWKS** | JSON Web Key Set. The public-key bundle the JWT validator fetches from `auth.yral.com/.well-known/jwks.json` to verify signatures (per E6 + E9). |
| **Langfuse** | Self-hosted LLM tracing server on rishi-6. Records every LLM call's prompt + response + tokens + latency + cost per D4. |
| **Lifespan** | FastAPI's startup/shutdown callback. Code before `yield` runs on first request; code after runs on SIGTERM. Used in this template to flush Langfuse traces on shutdown. |
| **Loki** | Grafana's log aggregation backend. Structured log lines emitted via structlog land here; query by `service`, `request_id`, `path`, etc. |
| **Middleware** | Code that wraps the request handler. Starlette/FastAPI middleware mounts via `app.add_middleware`; ordering is LIFO (last added = first to see incoming requests). |
| **Multi-stage build** | A Dockerfile pattern with two `FROM` lines. Stage 1 installs deps into a venv; stage 2 copies the venv into a slim image without the build tools. Smaller + safer final image. |
| **No-op** | "Does nothing" — a function that returns immediately. The template's middlewares no-op when their feature flag is disabled (Sentry with empty DSN, Langfuse with tracing disabled, etc.). |
| **Overlay network** | Docker Swarm's encrypted virtual network spanning multiple nodes. v2 has three per C3: `yral-v2-public-web`, `yral-v2-internal`, `yral-v2-data-plane`. |
| **Patroni** | The Postgres HA orchestrator running on rishi-4/5/6 per F3. Auto-promotes a replica when the primary fails. |
| **pgBouncer** | A connection pooler in front of Postgres. Multiplexes many client connections to fewer Postgres connections. Session mode locally (avoids the asyncpg prepared-statement gotcha); transaction mode in prod (real multiplexing). |
| **pydantic** | Python's de-facto data-validation library. `pydantic.BaseModel` defines typed request/response shapes; `pydantic-settings` extends it for env-driven runtime settings. |
| **Replica** | A running copy of the service. Swarm runs 3 per G2 — load-balanced behind the edge Caddy. |
| **Sentinel (Redis)** | The HA quorum service in front of Redis primary + replica. Per C11 — three sentinels (rishi-4/5/6) monitor the master; clients ask Sentinel "who's the current primary?" instead of connecting directly. |
| **Sentry** | Self-hosted error-tracking server at `sentry.rishi.yral.com` per A7 (reinforced 3 times by Rishi). NEVER `apm.yral.com`. |
| **Service tag** | The `service=<name>` label stamped on every Sentry event + Loki log line per D3. Lets us filter by service in the dashboard. |
| **Singleton** | A module-level instance constructed once + reused everywhere. The template uses singletons for Langfuse (`_client`) and Settings (`functools.lru_cache(maxsize=1)`). |
| **Soul File** | The DOLR product term for what other systems call a "system prompt" or "character card." Per B4 + E8 — layered (global / archetype / per-influencer / per-user-segment). |
| **Stateful core** | The cluster's shared Postgres + Redis + Langfuse + pgBouncer stack on rishi-4/5/6. Owned by Session 1, not by individual services. |
| **structlog** | The Python library that produces structured (key=value / JSON) log lines. Has a processor pipeline — the template's processors inject `request_id` + redact per H6 allowlist + render JSON in prod, pretty-console locally. |
| **Swarm** | Docker's native cluster orchestrator. v2 uses Swarm per C2 (not Kubernetes). |
| **Swarm secret** | Encrypted secret stored by the Swarm manager + mounted into the container as a file at `/run/secrets/<name>`. The template's `docker-compose.swarm.yml` declares them as `external: true` per D1. |
| **Synthetic user** | A canary process that runs a real API turn every 5 min on production per H9. Alerts on failure or latency > 2× baseline. |
| **uvicorn** | The ASGI server that runs FastAPI in production. PID 1 in the container (per the Dockerfile's exec-form CMD) so SIGTERM reaches FastAPI's lifespan shutdown. |

## RELATED FILES

- `DEEP-DIVE.md` — many of these terms appear in the diagrams
- `WALKTHROUGH.md` — terms in execution order
- `READING-ORDER.md` — which files use which terms
- `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md` — the rules that reference these terms

## Day-4 vocabulary

- **Layer 1 / Global** — one Soul File row applied to every chat turn. Per E8. `scope_key=''` in `soul_file_layers`.
- **Layer 2 / Archetype** — one row per archetype (companion / therapist / coach today). The composer picks the L2 row by joining on the L3 row's `archetype` column. `scope_key=<archetype-name>`.
- **Layer 3 / Per-Influencer** — one row per AI Influencer ID. Carries influencer-specific personality body + the `archetype` field. `scope_key=<ai_influencer_id>`. NOT seeded Day 4 — populated by Day-4.5 data port from chat-ai's `ai_influencers.system_prompt`.
- **Layer 4 / Per-User-Segment** — one row per `new` / `paying` / `dormant`. Tunes tone per user state. `scope_key=<segment-name>`.
- **scope_key** — second half of the composite key into `soul_file_layers`. The partial unique index `(layer, scope_key) WHERE is_current=TRUE` enforces exactly one current row per slot.
- **version_pin** — `sha256(f"{l1.version}:{l2.version}:{l3.version}:{l4.version}")[:16]` returned alongside the layered prompt. Lets a future Redis cache invalidate when any layer's version bumps without examining the prompt content.
- **Byte-stable prefix** — the engineering contract per `PRE-SPAWN-CONTRACTS-FROM-COORDINATOR.md`. Provider-side prompt caching keys on the byte-prefix; one drifting byte = full cache miss = 3-10× TTFT regression.
- **LAYER_SEPARATOR** — the literal `\n\n---\n\n` the composer joins layer bodies with. Locked in `shared-config.yaml` per C7.
- **is_current / version** — simple rollback model. Repository's `create_new_version` flips the prior current to FALSE + inserts at version=prior+1 within one transaction.

## Status

Day-4 vocabulary current. Day-5+ adds: cache_hit, ephemeral cache breakpoint, opaque-bytes contract (mostly orchestrator-side concerns).
