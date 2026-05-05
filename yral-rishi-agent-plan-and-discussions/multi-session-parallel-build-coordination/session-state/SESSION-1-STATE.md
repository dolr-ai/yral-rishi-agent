# Session 1 STATE — Infra & Cluster
> Updated: 2026-05-05 (Day 1-2 cluster bootstrap drafts — PR A foundation opened).

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 1. I own infrastructure: rishi-4/5/6 cluster bootstrap (Docker
Swarm + Patroni HA + Redis Sentinel + Langfuse + Caddy Swarm service), the
Sentry baseline cron, chaos tests, and the rishi-1/2 Caddy snippet via the
yral-rishi-hetzner-infra-template repo.

## LAST THING I DID

Drafted PR A (foundation) of the Day 1-2 cluster bootstrap deliverables on
branch `session-1/cluster-bootstrap-scripts-draft`: `node-bootstrap.sh`
(three-phase Hetzner-node setup with root-window / swarm-init / swarm-join),
`caddy-swarm-service.yml` (Caddy 2-replica Swarm stack pinned to edge nodes,
:443 ingress only), and the cluster-level `secrets-manifest.yaml` (16
secrets declared in the CONSTRAINTS D7 schema). Drafts only — no SSH to
rishi-4/5/6, no live data pulls, per CONSTRAINTS A13. Day 0.5 work (PR #4)
merged on main yesterday as commit `e2a0743` via admin override.

## CURRENT TASK

PR A awaiting commit + push + PR open. After PR A merges or gets the green
light, draft PR B (stateful core): `patroni-install.sh` (HA Postgres +
etcd + pgBouncer + WAL-G archive per F3 + G3 + D2), `redis-sentinel-
install.sh` (primary on rishi-4 + replica on rishi-5 + sentinels per C11),
`langfuse-install.sh` (self-hosted on rishi-6 per D4).

## NEXT 3 PLANNED ACTIONS

1. Commit PR A bundle (3 new files + LOG/STATE updates) on
   `session-1/cluster-bootstrap-scripts-draft`. Push. Open PR with
   PR_REQUEST_TEMPLATE. Coordinator + Codex review picks it up.
2. Draft PR B (stateful core) on the same branch as a follow-up commit
   if PR A reviewer feedback is light, OR on a new branch if PR A needs
   more rework. ~800 lines: patroni + redis + langfuse install scripts.
3. After both PRs merge, Day 3 work begins: chaos test scripts per H3
   (kill-rishi-6.sh, kill-patroni-leader.sh, fill-rishi-5-disk.sh,
   partition-rishi-6.sh, run-all-chaos-tests.sh) — still drafts only.

## BLOCKERS

None. DEP-001 + DEP-002 from yesterday are RESOLVED on main. Session 1
scope path now correctly includes `bootstrap-scripts-for-the-v2-docker-
swarm-cluster/` and the per-session log/state/deps file paths.

## PENDING PRs (mine)

- **PR A** (this push): `session-1/cluster-bootstrap-scripts-draft` —
  node-bootstrap.sh + caddy-swarm-service.yml + secrets-manifest.yaml +
  LOG/STATE updates. ~1160 lines of code/yaml + ~120 lines of LOG/STATE.
  Codex may truncate the diff above ~800 lines; security-critical paths
  (pre-flight + UFW + sudoers + Swarm init) appear first in
  node-bootstrap.sh so they should land within the visible window.

## CROSS-SESSION DEPS (mine)

None open.

## CONFIRM TO RISHI (pre-written for resume)

```
I'm resuming Session 1. Day 1-2 PR A (node-bootstrap.sh + Caddy stack +
cluster-level secrets-manifest.yaml) is open on branch
session-1/cluster-bootstrap-scripts-draft. PR B (Patroni + Redis +
Langfuse install scripts) queued next on same or follow-up branch.
Day 3 chaos-test drafts come after both. No blockers, no open deps.
Drafts only — Days 4-7 server-touching work needs a separate explicit
YES from you per A13. Ready to continue?
```
