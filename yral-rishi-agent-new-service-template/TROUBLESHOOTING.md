# TROUBLESHOOTING — yral-rishi-agent-new-service-template

> One-line purpose: **symptom-to-fix lookup table.** If something's broken, find the symptom first; "why" comes second.

## ⭐ START HERE

Pick the symptom that matches what you're seeing:

| Symptom | Section below |
|---|---|
| `docker compose up` fails | "Service won't start locally" |
| Service starts but health endpoint 503s | "Service starts but unhealthy" |
| Requests are slow | "Slow requests" |
| Postgres connection refused | "Database connection issues" |
| Redis errors | "Redis connection issues" |
| Sentry not capturing errors | "Sentry not working" |
| Langfuse traces missing | "Langfuse not capturing" |
| Logs not structured (no JSON) | "Logs look wrong" |
| Production deploy failed | "Deploy failed" |

## Service won't start locally

| Likely cause | Check | Fix |
|---|---|---|
| Port conflict on 5432 / 6379 / 8000 | `lsof -iTCP:8000` | Stop the other process, or change the host port in `docker-compose.yml`. |
| Stale image | n/a | `docker compose down && docker compose up --build`. |
| `.env.local` missing | `ls .env.local` | `cp .env.example .env.local`. |
| Docker daemon not running | `docker ps` errors | Start Docker Desktop / dockerd. |

## Service starts but unhealthy

Once `/health/{live,ready,deep}` endpoints land (per F9), this section gets symptom-specific fixes per which tier is failing.

For now: check `docker logs <container>` for the actual error. The structured logger emits a `request_id` field — grep for that to correlate.

## Slow requests

Per E1 we have a 50%-faster-than-Python-yral-chat-ai target. If you're slower:

1. Check the Grafana latency panel (link goes here once Session 1's stateful-core is up).
2. Open Langfuse (`langfuse.rishi.yral.com`) — look at the slowest LLM calls for this service's traces.
3. Check Sentry performance tab (`sentry.rishi.yral.com`) — filter by `service=yral-rishi-agent-new-service-template`.
4. If pgBouncer queue is full, you'll see asyncpg connection timeouts. Bump `POSTGRES_CONNECTION_LIMIT` in `project.config` (cluster-wide).

## Database connection issues

| Error | Fix |
|---|---|
| `connection refused` (local) | Is the Postgres container up? `docker compose ps`. |
| `prepared statement does not exist` (prod) | This means pgBouncer transaction-mode + asyncpg cache mismatch. Check `shared-config.yaml`'s `database.asyncpg_statement_cache_size: 0`. |
| `role "..." does not exist` | Per-service Postgres role wasn't created at deploy. Check Session 1's stateful-core bootstrap. |
| Schema missing | Same as above — service schema is created by the bootstrap script. |

## Redis connection issues

| Error | Fix |
|---|---|
| `connection refused` (local) | Is the Redis container up? `docker compose ps`. |
| `NOAUTH Authentication required` (prod) | `REDIS_SENTINEL_PASSWORD` env var missing. Check the Swarm secret mount. |
| `MOVED ...` | We're NOT using Redis Cluster, only Sentinel. This error means something's misrouted. Check the Sentinel master name (`yral-v2-redis-primary` per shared-config). |

## Sentry not working

| Symptom | Fix |
|---|---|
| No events in Sentry at all | `SENTRY_DSN` env var empty? It's empty by default locally. In prod, check the Swarm secret. |
| Events landing in `apm.yral.com` (NOT `sentry.rishi.yral.com`) | **Stop deploy immediately.** DSN is pointing at the wrong instance. Rotate to the correct DSN per A7 (reinforced 3 times by Rishi). |
| Service tag missing | Check `SENTRY_SERVICE_TAG` env var; should be sourced from `project.config`. |

## Langfuse not capturing

| Symptom | Fix |
|---|---|
| No traces showing up | `LANGFUSE_TRACING_ENABLED` defaults to `false`. In prod, set to literal `"true"` (any other value evaluates as disabled per `app/langfuse_middleware.py`'s default-deny). |
| Auth errors | One of `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` is empty. Module no-ops; check the Swarm secrets. |
| Traces missing for in-flight requests on deploy | The lifespan shutdown's `flush_langfuse()` should be catching this. If not, the SIGTERM may not be reaching uvicorn — verify CMD is exec-form (per the Dockerfile). |

## Logs look wrong

| Symptom | Fix |
|---|---|
| Plain-text instead of JSON | `ENVIRONMENT` env var is `local` (or unset). Production sets it to `production` which switches to JSONRenderer. |
| Fields show as `"<redacted>"` | Field key isn't in `_FIELD_ALLOWLIST` (`app/logging.py`). Per H6 this is intentional. To allow a new field name, add it via a 1-line PR. |
| `request_id` always shows `no-request` | Log line emitted outside a request context (e.g. during lifespan startup). Expected. |

## Deploy failed

Per I2 the rolling update auto-rolls-back on failure. If the failure persists:

1. Check the GitHub Actions workflow log for the deploy.
2. Check `gh pr view` for any post-deploy verification failures.
3. SSH to a Swarm manager only if Rishi explicitly authorizes (per A2): `ssh rishi-4 'docker service logs <stack>_<service>'`.

## RELATED FILES

- `RUNBOOK.md` — operating procedures + monitoring links
- `app/sentry_middleware.py` / `app/langfuse_middleware.py` — middleware-specific behavior
- `app/logging.py` — log allowlist + redaction
- `shared-config.yaml` — Sentinel topology, pgBouncer location, etc.
- `yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md` — the rules

## Status

Scaffold. Real fix-recipes for the post-deploy era fill in Days 5-6 once production traffic starts producing real symptoms.
