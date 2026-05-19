# RUNBOOK — yral-rishi-agent-soul-file-library

> One-line purpose: **how to operate this service in production safely.** Read top-to-bottom in an incident; the more urgent the situation, the higher the relevant section.

## ⭐ START HERE if it's an incident

1. Open the Grafana dashboard for this service (link goes here once Session 1's stateful-core stack is up).
2. Check `https://sentry.rishi.yral.com` for spike in errors tagged `service=<this-service>` (per A7 + D3).
3. Check the Google Chat webhook alert channel for any active P0/P1 (per D6).
4. If multiple replicas are flapping → see "Replicas crash-looping" below.
5. If latency is over 2× baseline → see "Slow requests" below.
6. If user reports broken behavior with no alerts firing → check the synthetic user heartbeat (per H9).

## Deploy

Production deploys are GitHub-Actions-triggered per I3 (manual "promote to prod" button after staging green).

```bash
# Staging auto-deploys on push to main (path-scoped per F16).
# Production = click the workflow_dispatch button on the deploy workflow.
# Canary order per I2: rishi-4 first → health check → rishi-5 → rishi-6.
# Auto-rollback to last-good image tag if any replica fails health within 30s.
```

DO NOT push directly to rishi-4/5/6 SSH (per A2). The deploy workflow is the only blessed path.

## Rollback

Two ways:

1. **Automatic** — happens on health failure during rolling update per I2. No human action needed.
2. **Manual** — re-run the deploy workflow with the last-known-good image tag (set as a workflow input). Tag format: `ghcr.io/dolr-ai/yral-rishi-agent-soul-file-library:<prior-git-sha>`.

Never `docker stack rm` to "fix" a deploy — that takes the service fully offline. Use rollback.

## Common operations

| Task | How |
|---|---|
| Restart a replica | Don't (let Swarm + healthcheck handle it). If genuinely needed: `docker service update --force <stack>_<service>` from a Swarm manager. |
| Tail logs | Logs ship to Loki via the structured-logging pipeline. Query in Grafana with `{service="yral-rishi-agent-soul-file-library"}`. |
| Flush Redis cache | The service NEVER caches anything that requires a manual flush. If you're tempted, file a ticket first. |
| Schema migration | `alembic upgrade head` from a one-off task container. Per H11 the CI runs migrations against a WAL-restored snapshot before merge. |

## Incident severity classifications

- **P0** — service fully down (all replicas unhealthy) OR data correctness compromised. Page immediately via Google Chat (per D6).
- **P1** — partial degradation (one replica down, latency > 2× baseline). Notify in Google Chat within 15 min.
- **P2** — non-user-facing issue (CI flaky, dashboard offline). Open a GitHub issue.

## Replicas crash-looping

1. Check the most recent deploy — if within last 10 min, automatic rollback per I2 should already be in motion.
2. If not in a deploy window: check Sentry for the actual stack trace (filter by `service=<this-service>`).
3. Common causes:
   - Missing Swarm secret (DATABASE_URL, REDIS_SENTINEL_PASSWORD, SENTRY_DSN) → check `docker secret ls` on a manager.
   - Postgres connection limit hit → check `POSTGRES_CONNECTION_LIMIT` in project.config vs cluster-wide budget.
   - Bad image tag pushed → manually roll back to the previous git SHA per "Rollback" above.

## Slow requests

Per E1 we have a 50%-faster-than-Python-yral-chat-ai target. If you're slower:

1. Check the Grafana latency panel (link goes here once Session 1's stateful-core is up).
2. Open Langfuse (`langfuse.rishi.yral.com`) — look at the slowest LLM calls for this service's traces.
3. Check Sentry performance tab (`sentry.rishi.yral.com`) — filter by `service=yral-rishi-agent-soul-file-library`.
4. If pgBouncer queue is full, you'll see asyncpg connection timeouts. Bump `POSTGRES_CONNECTION_LIMIT` in `project.config` (cluster-wide).

## Monitoring + alerts

- **Sentry** (`sentry.rishi.yral.com`, per A7): unhandled exceptions, performance regressions.
- **Langfuse** (`langfuse.rishi.yral.com`, per D4): LLM call traces, cost spikes.
- **Grafana / Loki**: structured logs query-able by `service`, `request_id`, `path`, etc.
- **Uptime Kuma** (`status.yral.com`, per D5): `/health/ready` checked every minute.
- **Alertmanager → Google Chat webhook** (per D6): all critical alerts route here.

## Backups + recovery

Per D2's three-layer strategy. This service stores nothing local; persistent state lives in the shared Patroni cluster (per F3) which has L1 HA + L2 WAL-G to S3 + L3 daily/weekly/monthly dumps.

## RELATED FILES

- `docker-compose.swarm.yml` — production Swarm stack
- `project.config` — per-service config (replica count, resource caps)
- `shared-config.yaml` — cross-service shared values
- `SECURITY.md` — threat model + auth/auth flow
- `DEEP-DIVE.md` — deploy + DB HA diagrams
- `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md` — the rules that make these procedures safe

## Day-4 operational procedures

### How to add a new archetype (Layer 2)

```bash
# 1. Insert the new archetype row via the repository (auth'd Python session
#    or a Day-5+ admin script — Day 4 has NO HTTP write endpoint).
python -c "
import asyncio
from app.database import init_pool, close_pool
from app.repository.soul_file_repository import create_new_version, LAYER_ARCHETYPE

async def main():
    await init_pool()
    row = await create_new_version(
        layer=LAYER_ARCHETYPE,
        scope_key='cheerleader',
        body='<the new archetype body text>',
        archetype=None,  # NULL on L2 rows — archetype column is L3-only
    )
    print('inserted', row.scope_key, 'version', row.version)
    await close_pool()

asyncio.run(main())
"
```

The partial unique index guarantees only one current row per `(layer, scope_key)` — no extra cleanup needed. Until at least one L3 row references the new archetype, the new L2 row is dead weight.

### How to rollback a Layer 3 edit

```bash
# Pick a known-good prior version; create_new_version with its body.
# This both retires the bad current row + lands a new current at prior+1.
python -c "
import asyncio
from app.database import init_pool, close_pool
from app.repository.soul_file_repository import list_versions, create_new_version, LAYER_PER_INFLUENCER

INFLUENCER_ID = '<the affected influencer UUID>'

async def main():
    await init_pool()
    history = await list_versions(LAYER_PER_INFLUENCER, INFLUENCER_ID)
    for r in history:
        print('  v', r.version, 'is_current=', r.is_current, '—', r.body[:60])

    good = history[1]  # one version before current
    rolled_back = await create_new_version(
        layer=LAYER_PER_INFLUENCER,
        scope_key=INFLUENCER_ID,
        body=good.body,
        archetype=good.archetype,
        created_by='runbook-rollback',
    )
    print('rolled back to v', rolled_back.version)
    await close_pool()

asyncio.run(main())
"
```

NOT `retire_current` — that leaves the slot empty + composer 500s. Always replace.

### Alembic migrations

```bash
# Local dev (against docker-compose Postgres):
export POSTGRES_DSN_SOUL_FILE_LIBRARY="postgresql://service:service-local-password@localhost:6432/service_local_database"
alembic upgrade head

# Production (against the Patroni cluster — operator action):
# 1. Source the DSN per D8.
# 2. alembic upgrade head from the service folder root.
# 3. Verify with `alembic current` — should match `head`.
# 4. If anything goes wrong: `alembic downgrade -1`.
```

## Status

Day-4 operational procedures current. Day-5+ adds: Redis cache invalidation hooks; Prompt-Coach auth'd HTTP write surface; Patroni-failover RUNBOOK additions.
