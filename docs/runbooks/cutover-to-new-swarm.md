# Runbook — move production from the Hetzner swarm to the new swarm

**Decided:** 2026-10-01 (Option B: build a new, clean swarm on the WireGuard mesh and move
into it). **Shape:** a *cold* move. The prod app is pulled, so an hour of planned downtime
costs nothing and is far simpler than moving with zero downtime.

**After this:** the Hetzner nodes join the new swarm one at a time (advertising their
`100.96.1.x` mesh address), and the swarm is tested by switching nodes off one at a time.

## Where things stand before the window

| | Old swarm (Hetzner, serving) | New swarm (3 Azure India managers, `100.96.2.x`) |
|---|---|---|
| Postgres | Patroni, pinned per node | `postgres` stack, **standby** replaying prod WAL-G (minutes behind) |
| Redis | `yral-v2-redis` | `redis` stack, same password secret, `caddy*` keys copied |
| Agent | serving | `yral-rishi-agent` cloned, loops off, Sentry off |
| Everything else | serving | not created: it would crash-loop against a read-only DB |
| Data | ClickHouse ×2 + Metabase on rishi-6 | bulk pre-copied to india-1 (`rsync` over WireGuard) |

New-swarm overlays: `yral-v2-data-plane` (10.0.3.0/24), `yral-v2-internal` (10.0.2.0/24),
`yral-v2-public-web` (10.0.1.0/24). Same names and subnets as prod, **unencrypted**
(WireGuard encrypts everything between nodes), MTU 1370.

## Tools (`scripts/migration/`)

- `migrate_service.sh <service>`: clones one live prod service onto the new swarm. It reads
  the spec from a prod manager, secret values from inside a running task, config files,
  and network names, then pipes them to `clone_service.py` on india-1. Nothing secret is
  printed. Postgres addresses (`patroni-rishi-N`, `pgbouncer`) are rewritten to
  `postgres-primary` in env **and** secrets. Placement pins are dropped; more than one
  copy means at most one per node.
- `redis_keys.py export|import '<pattern>'`: copies Redis keys (DUMP/RESTORE + TTL).

## The window, step by step

Each step has a check. Don't start the next step until the check passes.

1. **Stop writers on Hetzner** (Rishi runs these: `docker service scale … =0` is denied for agents).
   `yral-rishi-agent amorae_web yral-v2-langfuse_langfuse-web yral-v2-langfuse_langfuse-worker
   yral-analytics_analytics yral-analytics-events_events-consumer yral-metabase_metabase
   edge-issuer`, then ClickHouse ×2. *Check:* `pg_stat_activity` on the prod leader shows
   no app connections.
2. **Flush and catch up.** On the prod leader, `select pg_switch_wal()`; wait for the archive.
   *Check:* the standby's `pg_last_wal_replay_lsn()` ≥ the prod leader's
   `pg_current_wal_lsn()` from before the switch.
3. **Final data sync**, sources stopped: rerun the rsync (it sends only changes), then copy
   india-1 → india-2/3 over the Azure private network.
   *Check:* file count + `du` match on all three.
4. **Promote.** `patronictl edit-config -s standby_cluster=null` (previewed 2026-10-03: it
   removes exactly that block). *Check:* `patronictl list` shows a **Leader**, TL 72+.
5. **Make the new cluster self-sufficient.** Redeploy `postgres-stack.yml` *without*
   `STANDBY_*`, *with* its own WAL-G prefix (a **new** path, never prod's) and a real
   `PGPASSWORD_STANDBY`. Then `ALTER ROLE standby PASSWORD …` to match (it's still Spilo's
   public default). *Check:* a base backup lands under the new prefix.
6. **Recopy `caddy*` keys** (`redis_keys.py`) in case a certificate renewed since the pre-copy.
7. **Create services** with `migrate_service.sh`, in order: amorae, langfuse-clickhouse,
   langfuse-web, langfuse-worker, analytics clickhouse, analytics, events-consumer,
   metabase, edge-issuer, overlay-watchdog, then the edge Caddy. Restore the agent copy's
   `SENTRY_DSN`; leave background loops **off** (Rishi's call, 2026-10-03).
   *Check:* every service at full replicas; `curl --resolve <host>:443:<india ip>` for every
   site below returns what prod returned.
8. **DNS** (Cloudflare, via Chrome; current values in memory/reference). Only A records change,
   to `20.219.36.211 20.219.222.27 20.235.107.58`:
   - `*.rishi.yral.com`: 6 A, DNS only (account "Go Bazzinga")
   - `amorae.ai`: 3 A, DNS only (account "Rishi@gobazzinga.io's Account")
   - `atmz.ai`: 2 A, **proxied** (ATMZ account); keep proxied
   Email records (MX/SPF/DKIM/DMARC) are never touched.
9. **Verify on the real names:** agent `/health` + a registry chat call, amorae, Langfuse
   login + a trace, analytics + events flowing, Metabase, atmz.ai, sentry.rishi.yral.com.

## Rollback

Until step 4, just restart the Hetzner services: nothing on Hetzner was changed.
After step 4, the two databases diverge: rolling back means DNS back to the old values,
restarting Hetzner services, and accepting that writes made on the new cluster during the
window are lost (with no users, there should be none). Nothing on Hetzner is deleted until
the new swarm has run cleanly for a week.

## Known gaps, deliberately left for after the move

- Background loops stay off until they're moved off RunPod (down since 2026-09-18).
- ClickHouse ×2 + Metabase self-heal by restarting on another node from that node's copy
  (Rishi chose option (a): restore from copy, ≤1 day loss). Scheduled re-copies come next.
- Gemini from India: `GEMINI_VERTEX_LOCATION=asia-south1` (chat ~0.7–1.4 s vs ~0.6 s from Germany).
