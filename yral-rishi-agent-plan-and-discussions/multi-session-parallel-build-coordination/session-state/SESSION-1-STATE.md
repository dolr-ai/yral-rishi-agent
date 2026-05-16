# Session 1 STATE — Infra & Cluster
> Updated: 2026-05-16 (Day-5 Step 1 complete: Patroni HA verified + verifier PR #51 + ETCD3 migration PR #53 + auto-merge workflow PR #50/#52 all landed. Idle pending Day-5 Step 2 green-light for Redis Sentinel).

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 1. I own infrastructure: rishi-4/5/6 cluster bootstrap (Docker
Swarm + Patroni HA + Redis Sentinel + Langfuse + Caddy Swarm service), the
Sentry baseline cron, chaos tests, and the rishi-1/2/3 Caddy snippet via the
yral-rishi-hetzner-infra-template repo (Day 7, currently deferred per agent
spec + A2 tightening 2026-05-13).

## LAST THING I DID

**Day-5 Step 1 (Patroni HA) is COMPLETE.** Live cluster state at the end of
the 2026-05-14 deploy session (no redeploy has happened since, so this is
still authoritative):

```
+ Cluster: yral-v2-postgres --+--------------+---------+----+---------+
| Member          | Host      | Role         | State   | TL | Lag MB |
+-----------------+-----------+--------------+---------+----+---------+
| patroni-rishi-4 | 10.0.3.88 | Leader       | running |  5 |        |
| patroni-rishi-5 | 10.0.3.89 | Replica      | running |  5 |      0 |
| patroni-rishi-6 | 10.0.3.90 | Sync Standby | running |  5 |      0 |
+-----------------+-----------+--------------+---------+----+---------+
```

- 3-member etcd quorum healthy
- F3 satisfied (sync standby streaming `sync`)
- pgBouncer 2 replicas Running on rishi-4 + rishi-5 (`edoburu/pgbouncer:1.21.0-p2`)
- 3 successful patronictl switchovers verified (TL 2 → 3 → 4 → 5)

On code today (2026-05-16) three follow-up PRs landed via the new auto-merge flow:

- **PR #51** — `confirm_stack_actually_deployed` post-deploy verifier (closes the silent-failure gap caught during yesterday's bug arc; admin-merged manually after Codex truncation false-positive).
- **PR #52** — auto-merge trigger fix (replaced `check_suite` with `workflow_run` on the 3 required linter workflows).
- **PR #53** — Patroni ETCD3 native code-path migration (`ETCD_HOSTS` → `ETCD3_HOSTS`); auto-merged cleanly via PR #52's fixed workflow even though Codex flagged the truncation false-positive again. Code change only — live cluster keeps running v2 REST until next install run.

## CURRENT TASK

**Idle pending Day-5 Step 2 green-light** (Redis Sentinel deploy). Stack file
+ install script already on main from Day 1-2 (PR #10). Same shape as
Patroni; expect 1-2 real-server bugs given the established Day-5 pattern,
though Sentinel's stateful surface is much smaller than Patroni's so the
bug-count ceiling should be lower. The new `confirm_stack_actually_deployed`
shape from PR #51 should be ported to redis-sentinel-install.sh in a small
follow-up; that's the natural first piece of work once Step 2 starts.

Process gate: Day-5 Step 2 deploy requires Rishi's typed YES per A13.

## NEXT 3 PLANNED ACTIONS

1. Wait for Rishi's typed green-light on Day-5 Step 2 (Redis Sentinel).
2. When YES lands: port the `confirm_stack_actually_deployed` post-deploy
   verifier from PR #51 into `redis-sentinel-install.sh` (small fix-PR;
   auto-merge under PR #50). Then scp the install script + stack to rishi-4
   and run. Verify Sentinel quorum + failover.
3. After Sentinel: same shape into `langfuse-install.sh`, then deploy
   Langfuse on rishi-6.

## BLOCKERS

None technical. Day-5 Step 2 deploy is GATED on explicit Rishi YES per A13 — deliberate process gate, not a blocker.

Day 7 (rishi-1/2/3 Caddy snippet via the `yral-rishi-hetzner-infra-template` repo) remains DEFERRED per agent spec + A2 tightening 2026-05-13.

## PENDING PRs (mine)

- None open. Day-5-Step-1 close PR is THIS PR (`session-1/day-5-step-1-patroni-ha-complete`).
- Stale branch `session-1/day-5-step-1-eod-capture` (yesterday's pre-auto-merge EOD capture) is now superseded by this PR; can be deleted after merge.

## MERGED PRs (mine, today 2026-05-16)

- **PR #51** — `confirm_stack_actually_deployed` post-deploy verifier (admin-merged after Codex truncation false-positive)
- **PR #53** — Patroni ETCD3 code-path migration (`ETCD_HOSTS` → `ETCD3_HOSTS`)

## MERGED PRs (mine, 2026-05-14 — Day-5 Step 1 bug arc)

- **PR #44** — non-empty placeholder for S3 secrets when WAL-G off
- **PR #45** — export resolved-secret-name in BOTH create + skip branches
- **PR #46** — etcd per-node bind dirs in pre-flight + pgbouncer image tag fix
- **PR #47** — `--enable-v2=true` on etcd command line (Spilo 3.0 v2 REST compat)
- **PR #48** — `/data/patroni-data` owned 101:103 not 999:999 (Spilo postgres uid)
- **PR #49** — empty WAL/S3 env vars when WAL-G off (skip wale_restore.sh)

## MERGED PRs (mine, 2026-05-13 and earlier)

- **PR #35** — Day 4 close
- **PR #33** — apply_placement_labels by local Swarm NodeID
- **PR #29** — IPv4 `--advertise-addr`
- **PR #23** — overlay `--opt encrypted=true` + C3 verifier
- **PR #21** — swarm-state exact-match
- **PR #19** — deb822 docker.sources idempotency
- **PR #15** — Day 3 EOD STATE
- **PR #13, PR #12** — Day 3 chaos test scripts
- **PR #10** — Day 1-2 stateful core install scripts + stacks
- **PR #9** — Day 1-2 cluster bootstrap foundation
- **PR #4** — Day 0.5 Sentry baseline cron

## CROSS-SESSION DEPS (mine)

None open.

## CONFIRM TO RISHI (pre-written for resume)

```
I'm resuming Session 1. Day-5 Step 1 (Patroni HA) is COMPLETE — live
cluster: rishi-4 Leader / rishi-5 Replica / rishi-6 Sync Standby on TL 5,
0 lag, 3 successful switchovers verified, F3 satisfied. 8-bug arc closed
(PRs #44-#49 on 2026-05-14). Today landed PR #51 (silent-failure verifier),
PR #52 (auto-merge trigger fix), PR #53 (Patroni ETCD3 migration), all
under the new auto-merge flow. Awaiting your typed green-light for Day-5
Step 2 (Redis Sentinel). Day 7 (rishi-1/2/3 Caddy) stays deferred. Ready
to continue?
```
