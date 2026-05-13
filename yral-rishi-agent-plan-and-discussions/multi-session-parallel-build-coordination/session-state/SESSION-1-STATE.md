# Session 1 STATE — Infra & Cluster
> Updated: 2026-05-13 EOD (Day 4 cluster bringup complete; idle pending Day 5 green-light).

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 1. I own infrastructure: rishi-4/5/6 cluster bootstrap (Docker
Swarm + Patroni HA + Redis Sentinel + Langfuse + Caddy Swarm service), the
Sentry baseline cron, chaos tests, and the rishi-1/2/3 Caddy snippet via the
yral-rishi-hetzner-infra-template repo (Day 7, currently deferred per
agent spec + A2 tightening 2026-05-13).

## LAST THING I DID

**Day 4 cluster bringup is COMPLETE.** All three Hetzner Ubuntu boxes
(rishi-4 / rishi-5 / rishi-6) are Swarm managers advertising IPv4 with
the three intended encrypted overlays present cluster-wide
(`encrypted=true` verified on both rishi-4 and rishi-6 — different host
classes for cross-check), placement labels matching V2 §5, and the
H1 `yral-v2-swarm-resync.service` systemd unit enabled on every node.
rishi-deploy with the CI key works on every node (Sunday-deadline
parity for permanent SSH achieved).

Five script bugs were caught in production execution during Day 4, each
fixed via a single-concern PR (#19 docker.sources, #21 swarm-state
substring, #23 encrypted=true + verifier, #29 IPv4 advertise, #33
labels-by-NodeID). Three A1 deletion carve-outs were typed YES'd by
Rishi during the day for recovery (overlay rm on rishi-4, swarm-leave
cascade on rishi-4, ghost node rm on rishi-5). Pause-fix-merge-retry
loop per A2.1; no over-engineered test harnesses. Full Day-4 narrative
captured in the close-out LOG milestone block.

## CURRENT TASK

**Idle pending Day 5 green-light.** Day 5 is the stateful-core deploy
onto the now-live cluster — Patroni HA Postgres, Redis Sentinel,
Langfuse on rishi-6, Caddy Swarm service on rishi-4/5, and the chaos
test runner (H3 Phase 0 exit criterion). All install scripts and stack
files for these are already on main from the Days 1-2 (PR #9, PR #10)
and Day 3 (PR #12, PR #13) work; the cluster is the prerequisite that
was missing.

Day 5 requires a separate explicit Rishi YES per A13 — "Days 4-7
cluster provisioning / deploy require explicit per-day Rishi YES" is
the deliberate process gate, not a technical blocker.

## NEXT 3 PLANNED ACTIONS

1. Wait for Rishi's "go Day 5" / equivalent green-light.
2. When the YES lands: scp `patroni-install.sh` (and sibling
   `patroni-stack.yml`) to rishi-4 and run as rishi-deploy (now that
   permanent SSH works). Deploys 3-node etcd + 3-node Patroni
   (sync commit per F3) + 2-replica pgBouncer per G3 onto the
   `yral-agent-data-plane-overlay`. Verify HA failover before
   declaring Day 5 partial-done.
3. Then `redis-sentinel-install.sh` + `langfuse-install.sh` +
   `caddy-swarm-service.yml`, in that order. Each followed by
   verification. Then `run-all-chaos-tests.sh` against the live
   cluster as the H3 exit criterion. Each step gated for a
   per-step Rishi YES because the install scripts haven't been
   exercised on real servers yet — the "1 bug per attempt" pattern
   from Day 4 may continue.

## BLOCKERS

None at the technical level. Day 5 deployment is GATED on explicit
Rishi YES per A13 — that's a deliberate process gate, not a blocker.

Day 7 (rishi-1/2/3 Caddy snippet via the
`yral-rishi-hetzner-infra-template` repo) remains DEFERRED per agent
spec + A2 tightening 2026-05-13. Needs a separate fresh Rishi YES +
fresh audit of the current rishi-1/2/3 Caddy state before any PR opens
against that external repo.

## PENDING PRs (mine)

- **`session-1/day-4-cluster-bringup-complete`** (this push): single
  PR closing Day 4 with the comprehensive milestone block above + this
  STATE update. .md-only, auto-merge-eligible per I14.

## MERGED PRs (mine, today 2026-05-13)

- **PR #19** — `add_docker_apt_repository_if_missing` deb822 idempotency
- **PR #21** — swarm-state exact-match (catches `inactive` substring trap)
- **PR #23** — overlay `--opt encrypted=true` + existing-overlay C3 verifier
- **PR #29** — IPv4 `--advertise-addr` (`YRAL_NODE_ADVERTISE_IPV4` env var)
- **PR #33** — `apply_placement_labels_to_this_node` targets local Swarm NodeID

## MERGED PRs (mine, earlier)

- **PR #15** — Day 3 EOD STATE-only update (2026-05-05)
- **PR #13** — Day 3 chaos tests: fill + partition + run-all
- **PR #12** — Day 3 chaos tests: kill scripts
- **PR #10** — Day 1-2 stateful core install scripts + stacks
- **PR #9** — Day 1-2 cluster bootstrap foundation
- **PR #4** — Day 0.5 Sentry baseline pull cron

## CROSS-SESSION DEPS (mine)

None open.

## CONFIRM TO RISHI (pre-written for resume)

```
I'm resuming Session 1. Day 4 cluster bringup is COMPLETE — 3 manager
Swarm with 3 IPsec-encrypted overlays on rishi-4/5/6, permanent
rishi-deploy + CI-key SSH on all three (Sunday deadline cleared
several days ago). Day 4 surfaced 5 script bugs, all fixed via
single-concern PRs (#19/#21/#23/#29/#33). I'm idling pending your
Day 5 green-light for stateful-core deploy (Patroni / Redis Sentinel
/ Langfuse / Caddy + the H3 chaos test runner). Day 7 (rishi-1/2/3
Caddy snippet) stays deferred per A2 tightening. Ready to continue?
```
