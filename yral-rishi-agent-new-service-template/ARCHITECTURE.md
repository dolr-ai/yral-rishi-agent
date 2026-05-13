# ARCHITECTURE — yral-rishi-agent-new-service-template

> One-line purpose: **what this service IS architecturally, and how its pieces fit together.** Tier-1 readers get a 30-second answer in the first section; Tier-2 readers can read the section headers; Tier-3 readers can chase the file-by-file map.

## ⭐ 30-second summary

A FastAPI + asyncio service that:
1. Boots with Sentry + Langfuse + structured logging wired up (per A7 / D4 / H6).
2. Tags every request with a UUID4 correlation ID and threads it through logs + Sentry scope (per the request-ID middleware).
3. Reads typed configuration from environment variables through a `pydantic-settings` singleton (per the config loader).
4. Talks to Postgres (via pgBouncer + asyncpg) and Redis (via the Sentinel-aware client when in prod).
5. Runs as 3 replicas behind Swarm with rolling-update auto-rollback (per G2 + I2).

That's it. Every spawned v2 service inherits this baseline.

## Module map (`app/` directory)

| Module | What it does |
|---|---|
| `__init__.py` | Marks `app/` as a Python package. |
| `main.py` | FastAPI app + lifespan + middleware wiring. Module-load calls: `init_sentry` → `init_langfuse` → `configure_logging` → app construction → mount `RequestIdMiddleware`. |
| `sentry_middleware.py` | `init_sentry()` — ships errors to `sentry.rishi.yral.com` (per A7). No-op on empty DSN. |
| `langfuse_middleware.py` | `init_langfuse()` / `get_langfuse()` / `flush_langfuse()` — LLM tracing to rishi-6 (per D4). |
| `request_id_middleware.py` | `RequestIdMiddleware` + `get_request_id()`. UUID4 per request, ContextVar, Sentry tag, response header echo. |
| `logging.py` | `configure_logging()` — structlog + H6 allowlist redaction processor + request-ID auto-injection. |
| `config.py` | `Settings` (pydantic-settings) + cached `get_settings()` singleton. Typed env-var access. |

## Inbound dependencies (who calls THIS service)

For the template itself: nobody. For spawned services: list here who calls you (mobile, public-api, orchestrator, etc.).

## Outbound dependencies (what THIS service calls)

For the template itself, the wiring is in place but no callers exist yet. For spawned services, list here:
- **Postgres** (via pgBouncer on `pgbouncer.yral-v2-data-plane:6432`) — schema-isolated per F3.
- **Redis** (via Sentinel quorum, master `yral-v2-redis-primary`) — per C11.
- **Sentry** (sentry.rishi.yral.com) — per A7.
- **Langfuse** (langfuse.rishi.yral.com) — per D4.
- **Other yral-rishi-agent-* services** — via overlay network `yral-v2-internal`.

## Data flow placeholder

Diagram fills in Days 5-6. Sketch:

```
Mobile → rishi-1/2 Caddy edge → rishi-4/5 Swarm ingress (via yral-v2-public-web overlay)
       → this service (3 replicas)
       → Postgres + Redis + Langfuse (via yral-v2-data-plane overlay)
```

## Constraints honored architecturally

See `README.md` for the full list. The architecture decisions encoding them:

- **A2.1** — minimum-viable middleware skeleton, no speculative abstractions.
- **A7 + D3** — Sentry DSN never points at `apm.yral.com`; service-tag injection global.
- **C3** — Swarm-only networking; no host ports except 443 at the edge.
- **C7** — `shared-config.yaml` for cross-service values; `project.config` for per-service values.
- **C11** — Sentinel-aware Redis client (Day-2 PR yet to land for the database/redis modules).
- **F9** — three-tier `/health/{live,ready,deep}` endpoints (yet to land).
- **F10** — idempotency-key middleware default-on (yet to land).
- **G2** — 3 replicas default; horizontal-scale from day 1.
- **H5** — prompt-injection defense middleware (yet to land for services on the LLM path).
- **H6** — PII allowlist in `app/logging.py`.

## RELATED FILES

- `app/` — the module map source code
- `README.md` — entrypoint
- `pyproject.toml` — runtime + dev deps
- `docker-compose.yml` / `docker-compose.swarm.yml` — local + prod wiring
- `project.config` — per-service values
- `shared-config.yaml` — cross-service shared values
- `secrets.yaml.template` — D8 secrets manifest

## Status

Scaffold. Module map is current as of Day-2 PR 4 merge. Diagram + data-flow detail fills in Days 5-6.
