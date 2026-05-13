# yral-rishi-agent-new-service-template

> One-line purpose: **this is the v2 template every yral-rishi-agent-* service forks from.** When `scripts/new-service.sh` spawns a new service, this whole folder is copied to `yral-rishi-agent-<name>/` and the placeholder names are sed-substituted. For a spawned service, this same README is the service-specific entrypoint.

## ⭐ START HERE

- **New to the codebase?** → `ONBOARDING.md`
- **Need to understand how it fits together?** → `ARCHITECTURE.md`
- **Operating it in production?** → `RUNBOOK.md`
- **Something's broken?** → `TROUBLESHOOTING.md`
- **Lost in the maze?** → `WHEN-YOU-GET-LOST.md`

## Quick start (local dev)

```bash
# From this folder:
cp .env.example .env.local       # fill in placeholders as needed
docker compose up --build        # builds the image + runs the full local stack
curl http://localhost:8000/openapi.json    # service responds (after /health endpoints land)
```

Stack pieces wired in by `docker-compose.yml`:
- The service itself (FastAPI + uvicorn, port 8000)
- Postgres 17-alpine (port 5432)
- pgBouncer 1.23.1 (session mode locally; transaction mode in prod)
- Redis 7-alpine (port 6379)

Langfuse stays disabled locally via `LANGFUSE_TRACING_ENABLED=false` (the ~1 GB Langfuse stack is overkill for a laptop; production hits the rishi-6 instance).

## Doc index (the 8 required per F8)

| Doc | What it tells you |
|---|---|
| `README.md` (this file) | Service summary + quick start + doc index |
| `ARCHITECTURE.md` | How the modules fit together, what depends on what |
| `RUNBOOK.md` | Operating procedures: deploy, rollback, incident response |
| `ONBOARDING.md` | Day-1-to-week-1 reading order for a new maintainer |
| `TROUBLESHOOTING.md` | Common errors and how to fix them |
| `WALKTHROUGH.md` | Narrative trace of one user action through the code |
| `GLOSSARY.md` | Plain-English definitions of every domain term used here |
| `WHEN-YOU-GET-LOST.md` | One-page north-star orientation |

## Constraints this service honors

Locked in CONSTRAINTS.md (see `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md`). Most load-bearing for the template:

- **A2.1** — avoid over-engineering; check in before elaborate solutions.
- **A7 + C4 + D3** — Sentry = `sentry.rishi.yral.com`, never `apm.yral.com`. Service tag stamped per D3.
- **B7** — 3-tier code documentation standard; this README is the Tier-1 entrypoint.
- **C7** — no hardcoded shared values; see `shared-config.yaml`.
- **D8** — per-service secrets manifest at `secrets.yaml.template` (or `secrets.yaml` for spawned services).
- **F12** — Python 3.12 + FastAPI + asyncio + asyncpg uniformly.
- **F16** — monorepo. Spawned services are SUBFOLDERS, not new repos.
- **H6** — PII allowlist enforcement in the structured-logging processor.

## Status

Scaffold. Real content per-doc fills in Days 5-6 per the role spec.
