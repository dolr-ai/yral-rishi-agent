# Patroni failover drill — runbook

**Phase**: 21αβ.H4
**Status**: prepared 2026-06-13, **execution gated on Rishi green-light post rollout-stable for 24h**

## Why this drill exists

We claim the V2 Patroni cluster (rishi-4/5/6) is HA. The migration runbook + cutover playbook both rely on automatic leader promotion when a node dies. We've never actually tested it in production — promotion is unproven. If a real leader incident hits, we'd be debugging promotion under pressure instead of executing a known-good runbook.

This drill proves:
- Graceful failover with the sync replica as candidate = zero committed data loss
- Promotion settles in <30s under steady traffic
- The old leader rejoins as a replica (cluster stays 3/3 healthy)
- `agent.rishi.yral.com/health` disruption window is <30s (matches Caddy's healthcheck cadence)

## Pre-flight (operator)

- [ ] Rollout has been stable for 24h (no Sentry pages, no /health 5xx alerts)
- [ ] No active alpha-soak user session in progress (check #yral-alpha for the last 30 min)
- [ ] Backups are current: `/admin/backup-health` shows GREEN verdict
- [ ] Off-hours: 02:00-05:00 UTC ideal (low traffic + your team awake)
- [ ] You have SSH access to rishi-4/5/6 (`rishi-deploy` user, key in macOS Keychain)
- [ ] You have `patronictl` available — either via `docker exec` into the patroni service container on the target host, or installed on your laptop

## Execution paths

### Path A — GitHub Actions workflow (recommended)

1. Open the [Patroni failover drill workflow](https://github.com/dolr-ai/yral-rishi-agent/actions/workflows/patroni-failover-drill.yml).
2. Click **Run workflow**.
3. Inputs:
   - `target_host_label`: the current leader host (check `/admin/backup-health` or `patronictl list`). Default: `rishi-4`.
   - `reason`: free-text audit trail. Format: "H4 drill — <date> — <operator initials>"
   - `i_understand`: type `RUN PATRONI DRILL` exactly.
4. Click **Run workflow**. The workflow:
   1. SSHs to the target host
   2. Stages `scripts/patroni_failover_drill.sh` under `/tmp/`
   3. Runs the drill inside the patroni service container via `docker exec`
   4. Captures the report + uploads as a workflow artifact

### Path B — SSH + manual (if workflow is broken)

```sh
# From your laptop
ssh rishi-deploy@<leader-host-ip>

# On the leader host
docker exec -it $(docker ps --filter "label=com.docker.swarm.service.name=yral-v2-patroni" --format "{{.ID}}" | head -n 1) bash

# Inside the patroni container
chmod +x /tmp/patroni_failover_drill.sh   # if you scp'd it manually
bash /tmp/patroni_failover_drill.sh
```

## What the script does — step-by-step

1. **Snapshot pre-drill cluster state** via `patronictl list --format=json` → identify leader, sync replica, async replica, current timeline.
2. **Pre-drill `pg_dump`** (~5 min for prod size) as safety net. Refuses to proceed without it.
3. **Baseline `/health` probe** for 30s → measure p50 latency pre-drill.
4. **Initiate graceful failover** via `patronictl failover --master <leader> --candidate <sync_replica> --force`.
5. **In parallel**: hit `/health` continuously every 1s → captures the disruption window.
6. **Watch for promotion** — poll `patronictl list` until the sync replica is the new leader AND the timeline incremented by 1. Timeout 120s.
7. **Compute disruption metrics** — longest contiguous non-200 window, total non-200 probes.
8. **Switchover back** to the original leader via `patronictl switchover --master <new_leader> --candidate <pre_leader> --force` → cluster post-drill state matches pre-drill state (idempotent drill).
9. **Write report** to `/tmp/patroni-drill-report-<ts>.txt` with timeline, disruption window, total wall time.

## Exit codes

| Code | Meaning | Operator action |
|---|---|---|
| 0 | PASS — failover + promotion + switchback all clean, /health disruption ≤30s | Record timestamp in DAILY-LOG.md, flip 21αβ.H4 → ✅ in PROGRESS.md |
| 1 | Prereqs missing (patronictl, curl, pg_dump, jq not found) | Install missing tool; rerun |
| 2 | Pre-drill `pg_dump` failed | DO NOT retry the drill until pg_dump works (no safety net) |
| 3 | Promotion never observed within 120s | Cluster may be stuck mid-failover. Check `patronictl list`. If stuck: revert via `patronictl resume <pre_leader>` — see "Recovery" below |
| 4 | Switchback failed | Cluster has new leader, NOT original. **Data is intact** — retry switchback manually. See "Recovery" below |
| 5 | PASS-with-note — drill passed but /health saw >30s of 5xx | Review app-side reconnect logic. Sentry should have captured the event — check for `Patroni leader changed` errors. May indicate the asyncpg pool isn't reconnecting cleanly |

## Recovery — if the drill leaves the cluster in an unexpected state

### Stuck in promotion (exit 3)

```sh
# See current state
patronictl -c /etc/patroni/patroni.yml list

# If the pre-leader is still up + healthy but Patroni thinks there's no leader:
patronictl -c /etc/patroni/patroni.yml resume <pre_leader>
```

### Switchback failed (exit 4)

```sh
# Cluster has new leader (the former sync replica). To restore original:
patronictl -c /etc/patroni/patroni.yml switchover \
    --master <current_leader> \
    --candidate <pre_leader> \
    --force
```

If repeated switchback attempts fail, **DO NOT** force-reconfigure. Investigate replication lag on the candidate (`patronictl list` shows Lag column) before any further moves.

### Worst case — restore from pre-drill `pg_dump`

The script wrote a `pg_dump` to `/tmp/patroni-drill-pre-dump-<ts>.sql.gz` before doing anything. If the cluster is unrecoverable:

```sh
# As a last resort — this is destructive. Get Rishi on the line first.
pg_restore -h <new_leader> -U postgres -Fc -c /tmp/patroni-drill-pre-dump-<ts>.sql.gz
```

We have never had to do this. The graceful-failover-with-sync-candidate path has zero documented data-loss cases in the Patroni community.

## Post-drill checklist

- [ ] Drill report attached to the workflow run (or saved from `/tmp/`)
- [ ] `patronictl list` shows 1 leader + 2 replicas, all on the same NEW timeline (`TL` column matches)
- [ ] Original leader is the leader again (the switchback worked)
- [ ] `/admin/backup-health` still GREEN
- [ ] `/health` returns 200 from `https://agent.rishi.yral.com/health`
- [ ] DAILY-LOG.md entry added: timestamp, exit code, disruption window, operator name
- [ ] PROGRESS.md row 21αβ.H4 flipped ⏳ → ✅ with the drill date + workflow run URL

## What this drill does NOT cover

- **Network partition between nodes** — needs a separate chaos test (split-brain detection). Out of scope for H4.
- **etcd disruption** — Patroni's DCS layer. If etcd is unreachable, Patroni refuses to promote (correct behavior); not testing here.
- **Hard kill of the leader** (vs graceful failover) — this drill uses `patronictl failover` which is the graceful path. The `kill -9` path is what happens on real hardware failure; we can simulate by `docker stop` on the patroni container, but the sync-replica promotion logic is the same.
- **WAL-G restore** — that's H6 (`scripts/walg_restore_drill.sh`), already shipped 2026-06-11.

## Related

- `scripts/patroni_failover_drill.sh` — the drill itself
- `.github/workflows/patroni-failover-drill.yml` — workflow_dispatch entry point
- `scripts/walg_restore_drill.sh` — H6 (already proven 2026-06-11)
- Phase 0 cluster setup: see `docker-compose/patroni-cluster.yml` from the initial cluster bootstrap
