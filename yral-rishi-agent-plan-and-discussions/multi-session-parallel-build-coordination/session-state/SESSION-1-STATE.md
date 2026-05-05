# Session 1 STATE — Infra & Cluster
> Updated: 2026-05-05 (Day 3 chaos-test drafts — PR A: kill scripts opened).

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 1. I own infrastructure: rishi-4/5/6 cluster bootstrap (Docker
Swarm + Patroni HA + Redis Sentinel + Langfuse + Caddy Swarm service), the
Sentry baseline cron, chaos tests, and the rishi-1/2 Caddy snippet via the
yral-rishi-hetzner-infra-template repo.

## LAST THING I DID

Drafted the Day 3 chaos test "kill" scripts on branch
`session-1/day-3-chaos-test-scripts` for PR A: `kill-rishi-6.sh` (drain a
node + verify hot-path reschedule + restore) and `kill-patroni-leader.sh`
(SIGKILL leader + verify replica promotes within 30 s + write/read sanity
+ wait for rejoin). All triple-gated by
`YRAL_CHAOS_RUN_AUTHORISED=$(date +%Y-%m-%d)` + Swarm-manager check + lock
file. Drafts only — real chaos runs happen Day 6 with separate Rishi YES.

The other three chaos files (`fill-rishi-5-disk.sh`,
`partition-rishi-6.sh`, `run-all-chaos-tests.sh` orchestrator) are written
but staged for PR B on a separate branch — bundle would have hit ~1380
lines, over the 1000-line trigger to split.

## CURRENT TASK

PR A awaiting commit + push + open. Then immediately branch + commit + push
PR B (fill + partition + runner). Both PRs target `main`; PR B's
orchestrator references PR A's scripts but is reviewable independently.

## NEXT 3 PLANNED ACTIONS

1. Commit + push + open PR A (kill scripts).
2. Branch `session-1/day-3-chaos-tests-fill-partition-runner` from main,
   add the fill/partition/runner files + LOG/STATE updates, push, open PR B.
3. After both PRs merge: Day 4-7 cluster provisioning (separate Rishi YES
   per A13). Day 7 Caddy snippet PR against
   `dolr-ai/yral-rishi-hetzner-infra-template` queued after that.

## BLOCKERS

None.

## PENDING PRs (mine)

- **PR A (this push)** `session-1/day-3-chaos-test-scripts` —
  kill-rishi-6.sh + kill-patroni-leader.sh + LOG/STATE.
- **PR B (next)** `session-1/day-3-chaos-tests-fill-partition-runner` —
  fill-rishi-5-disk.sh + partition-rishi-6.sh + run-all-chaos-tests.sh +
  LOG/STATE.

## MERGED PRs (mine, recent)

- **PR #11** D.4 ownership-doc + lint reconciliation (coordinator's, not
  mine — but referenced for context).
- **PR #10** Day 1-2 stateful core (Patroni + Redis + Langfuse).
  Merged on main 2026-05-05.
- **PR #9** Day 1-2 foundation (node-bootstrap + Caddy + secrets-manifest).
  Merged on main 2026-05-05 as `6668eb5`.
- **PR #4** Day 0.5 Sentry baseline pull cron. Merged 2026-05-04 as `e2a0743`.

## CROSS-SESSION DEPS (mine)

None open.

## CONFIRM TO RISHI (pre-written for resume)

```
I'm resuming Session 1. PR A (Day 3 kill chaos tests) opened on branch
session-1/day-3-chaos-test-scripts. PR B (fill + partition + orchestrator)
queued next on a fresh branch from main. Both drafts only — no chaos
runs anywhere until you type a separate YES on Day 6 per A13. Days 4-7
cluster provisioning is the next gate after both PRs merge. Ready to
continue?
```
