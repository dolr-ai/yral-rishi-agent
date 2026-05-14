# Session 1 STATE — Infra & Cluster
> Updated: 2026-05-14 EOD (Day-5 Step 1 complete — Patroni HA verified, 3-member cluster with sync standby + 3 successful switchovers; idle pending Day-5 Step 2 green-light for Redis Sentinel).

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 1. I own infrastructure: rishi-4/5/6 cluster bootstrap (Docker
Swarm + Patroni HA + Redis Sentinel + Langfuse + Caddy Swarm service), the
Sentry baseline cron, chaos tests, and the rishi-1/2/3 Caddy snippet via the
yral-rishi-hetzner-infra-template repo (Day 7, currently deferred per
agent spec + A2 tightening 2026-05-13).

## LAST THING I DID

**Day-5 Step 1 (Patroni HA) is COMPLETE.** Final cluster state at EOD:

```
+ Cluster: yral-v2-postgres --+--------------+---------+----+---------+
| Member          | Host      | Role         | State   | TL | Lag MB |
+-----------------+-----------+--------------+---------+----+---------+
| patroni-rishi-4 | 10.0.3.88 | Leader       | running |  5 |        |
| patroni-rishi-5 | 10.0.3.89 | Replica      | running |  5 |      0 |
| patroni-rishi-6 | 10.0.3.90 | Sync Standby | running |  5 |      0 |
+-----------------+-----------+--------------+---------+----+---------+
```

- 3-member etcd quorum healthy (`etcdctl endpoint health --cluster`: all 3 OK)
- F3 satisfied: rishi-6 is Sync Standby (sync_state=`sync` per pg_stat_replication)
- pgBouncer 2 replicas Running on rishi-4 + rishi-5 (`edoburu/pgbouncer:1.21.0-p2`)
- 3 successful patronictl switchovers verified (TL 2 → 3 → 4 → 5); replicas
  re-streamed at each new timeline, sync standby re-elected after each switch,
  no data loss, quorum maintained throughout.

**8-bug arc resolved across 6 deploy attempts** (PR #44, #45, #46, #47, #48,
#49 + 1 no-PR fix). Pattern was pause-fix-merge-retry per A2.1; each fix
under 50 strict-code lines; cumulative Day-5 diff ~250 lines. Full bug-arc
table + per-PR root-cause summary in the Day-5-Step-1 close milestone block
in SESSION-1-LOG.md.

## CURRENT TASK

**Idle pending Day-5 Step 2 green-light.** Step 2 is Redis Sentinel deploy.
Stack file + install script already on main from Day 1-2 (PR #10); same
shape as Patroni, expect 1-2 real-server bugs given the pattern. Rishi
explicitly green-lighted Day 5 this morning (per-step YES per A13) so the
process gate is just "ping coordinator with Patroni outcome and confirm
Redis Sentinel is next."

Two small follow-up PRs queued for tomorrow morning (after coordinator's
auto-merge GitHub Action + PR-comment session-messaging lands, ~75 min):

- **(a)** `confirm_stack_actually_deployed` verifier — closes the silent-
  failure gap where `docker stack deploy` returned 0 despite Rejected
  services on Day-5 deploys 2 + 3. Adds a post-deploy poll-and-fail-loud
  function to patroni-install.sh (and pattern can be lifted into redis +
  langfuse install scripts).
- **(b)** Patroni → ETCD3 code path migration — moves Spilo's Patroni
  from the v2 REST API (re-enabled in PR #47 with `--enable-v2=true`) to
  the ETCD3 native code path. Forward-proofs for etcd 3.6 where v2 API is
  removed. Pure config change; needs its own testing window.

Both auto-merge under the new flow tomorrow.

## NEXT 3 PLANNED ACTIONS

1. Tomorrow morning, after coordinator wires auto-merge + PR-comment
   messaging: open PR (a) — `confirm_stack_actually_deployed` verifier.
   Lightweight: 20-30 strict-code lines in patroni-install.sh + same-commit
   LOG entry. Auto-merges under new flow.
2. Open PR (b) — Patroni ETCD3 code path migration. Config-only diff on
   patroni-stack.yml's 3 patroni service env blocks + brief note in install
   script. Test by redeploying and verifying patronictl list still shows
   3 members on the new code path. Auto-merges under new flow.
3. Open the Day-5-Step-1 close PR bundling: this STATE update + the EOD
   LOG milestone (currently uncommitted on local branch
   `session-1/day-5-step-1-eod-capture`) into one wrap-up. Then await
   Rishi's typed green-light for Day-5 Step 2 (Redis Sentinel).

## BLOCKERS

None technical. Day-5 Step 2 (Redis Sentinel) is GATED on explicit Rishi
YES per A13 — deliberate process gate, not a blocker.

Day 7 (rishi-1/2/3 Caddy snippet via the `yral-rishi-hetzner-infra-template`
repo) remains DEFERRED per agent spec + A2 tightening 2026-05-13.

## PENDING PRs (mine)

- None open. The local branch `session-1/day-5-step-1-eod-capture` has the
  EOD STATE + LOG capture; will bundle into the (a)+(b) wrap PR tomorrow
  rather than open a standalone PR tonight (less admin-merge overhead).

## MERGED PRs (mine, today 2026-05-14 — Day-5 Step 1 bug arc)

- **PR #44** — non-empty placeholder for S3 secrets when WAL-G off
  (Docker secret create rejects 0-byte stdin)
- **PR #45** — export resolved-secret-name in BOTH create + skip branches
  (skip-branch bug caused empty YAML keys on second run)
- **PR #46** — etcd per-node bind dirs in pre-flight + pgbouncer image
  tag fix (`1.21.0` → `1.21.0-p2`, since edoburu tags are `-pN` suffixed)
- **PR #47** — `--enable-v2=true` on etcd command line (Spilo 3.0 Patroni
  uses etcd v2 API which is disabled by default in etcd 3.4+)
- **PR #48** — `/data/patroni-data` owned 101:103 not 999:999 (Spilo
  postgres uid is 101 not the official `postgres:*` image's 999)
- **PR #49** — empty out WAL/S3 env vars when WAL-G off so Spilo's
  `wale_restore.sh` skips the standby-bootstrap loop and falls through
  to `pg_basebackup` (replicas were hung on `wal-e backup-list` urllib
  retry against placeholder S3 creds)

## MERGED PRs (mine, earlier)

- PR #35 — Day 4 close
- PR #33 — apply_placement_labels by local Swarm NodeID
- PR #29 — IPv4 `--advertise-addr`
- PR #23 — overlay `--opt encrypted=true` + C3 verifier
- PR #21 — swarm-state exact-match
- PR #19 — deb822 docker.sources idempotency
- PR #15 — Day 3 EOD STATE
- PR #13, PR #12 — Day 3 chaos test scripts
- PR #10 — Day 1-2 stateful core install scripts + stacks
- PR #9 — Day 1-2 cluster bootstrap foundation
- PR #4 — Day 0.5 Sentry baseline cron

## CROSS-SESSION DEPS (mine)

None open.

## CONFIRM TO RISHI (pre-written for resume)

```
I'm resuming Session 1. Day-5 Step 1 (Patroni HA) is COMPLETE — final
cluster: patroni-rishi-4 Leader / rishi-5 Replica (async) / rishi-6 Sync
Standby on TL 5, all 0-lag, etcd quorum healthy, 3 successful
patronictl switchovers verified, F3 satisfied. 8-bug arc resolved
through PRs #44-#49 (cumulative ~250 strict-code lines). Local branch
`session-1/day-5-step-1-eod-capture` has the EOD STATE + LOG capture
ready to bundle into the wrap PR.

Tomorrow morning, once coordinator wires auto-merge + PR-comment session
messaging: I'll open (a) `confirm_stack_actually_deployed` verifier, then
(b) Patroni → ETCD3 code path migration, then the Day-5-Step-1 close
PR bundling those plus the EOD capture. After that I'll need a typed
green-light for Day-5 Step 2 (Redis Sentinel).

Day 7 (rishi-1/2/3 Caddy snippet) remains deferred. Ready to continue?
```
