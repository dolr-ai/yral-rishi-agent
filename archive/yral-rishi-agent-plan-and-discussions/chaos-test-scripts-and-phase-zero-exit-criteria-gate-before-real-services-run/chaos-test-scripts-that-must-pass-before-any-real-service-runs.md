# Chaos Test + Phase 0 Exit Criteria

**Status: empty placeholder** until we run Phase 0.

Per `V2_TEMPLATE_AND_CLUSTER_PLAN.md` §6.7 and CONSTRAINTS.md row H3, these chaos tests MUST all pass before any real v2 service runs production traffic.

## The tests

| # | Test | What it verifies | Success criterion |
|---|---|---|---|
| C1 | `drain-rishi-6.sh` — `docker node update --availability drain` on rishi-6 | Hot-path replicas reschedule to rishi-4/5; Patroni leader holds; Redis Sentinel retains quorum (2 of 3); Langfuse moved away without data loss | All services respond /health/ready within 60s of drain |
| C2 | `kill-patroni-leader.sh` — `docker kill <current Patroni leader container>` on rishi-4 | Auto-failover to sync replica on rishi-5 within 30s; all apps continue serving via pgBouncer reconnect | Zero writes lost (sync commit); read availability <60s downtime |
| C3 | `fill-disk-to-80-percent.sh` — dd a large file until rishi-5 disk is 80% full | Prometheus disk-space alert fires; Loki log-shipping degrades gracefully (buffered); Patroni WAL archive still ships | Alert fires within 2 min of threshold; no data loss; cleanup via `rm` recovers |
| C4 | `partition-rishi-6-network.sh` — iptables rule blocks rishi-6 from rishi-4/5 for 10 min, then restores | etcd quorum 2 of 3 holds (rishi-4/5 stay); rishi-6 re-joins cleanly; Patroni async replica catches up within 5 min of restore | No split-brain; no Patroni promotion on rishi-6; read-write traffic uninterrupted |
| C5 | `reboot-rishi-6.sh` — actual `reboot` on rishi-6 | `yral-v2-swarm-resync.service` systemd unit redeploys all stacks within 2 min; no manual intervention needed | All stacks healthy within 2 min; `docker ps` shows expected containers |
| C6 | `caddy-swarm-service-restart.sh` — `docker service update --force <caddy-stack>` on rishi-4 | Rolling restart of Caddy Swarm service; Swarm ingress mesh continues serving via the other replica; no 502s | Zero dropped requests during restart |
| C7 | `wal-g-restore-drill.sh` — restore yesterday's WAL backup into throwaway Postgres, run sanity queries, destroy | L2 backup layer works end-to-end | Queries return expected row counts; drill completes within 10 min |
| C8 | `backblaze-offsite-restore-drill.sh` — restore last week's pg_dump from Backblaze B2 into fresh server, verify, destroy | L3 backup layer works end-to-end | Dump restores cleanly; full restore time measured |
| C9 | `feature-flag-kill-switch.sh` — flip `SERVICE_DISABLED=true` on one service; verify 503 + graceful message; flip back; verify recovery | Kill-switch middleware works | 503 returned instantly on flip; recovery after flip-back <30s |
| C10 | `prompt-injection-defense.sh` — submit 20 known prompt-injection payloads through public-api | Injection defense blocks + logs them | 18/20 blocked (95% recall); blocks logged to Sentry with `type=prompt_injection` |

## Structure

```
chaos-test-and-phase-zero-exit-criteria-scripts/
├── README.md (this file)
├── C1-drain-rishi-6.sh                 (to be written)
├── C2-kill-patroni-leader.sh           (to be written)
├── ...
├── run-all.sh                           # Runs C1-C10 sequentially, reports
└── phase-zero-exit-certification.md    # Signed off by Rishi when all pass
```

## When to use

1. **Phase 0 exit gate** — all 10 must pass before Phase 1 starts
2. **Periodic re-run** — run monthly to catch regressions
3. **After cluster changes** — any node add/remove, any Swarm stack update
4. **Before major cutover decisions** — re-run before Rishi considers cutover
