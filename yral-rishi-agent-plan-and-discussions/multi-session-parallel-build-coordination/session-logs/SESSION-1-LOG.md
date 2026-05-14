# Session 1 LOG — Infra & Cluster
> Append-only diary. Most recent entries at TOP. Auto-appended by `.claude/hooks/post-tool-use.sh` on every git commit. Manual milestone entries welcome.

## 2026-05-14 — DEP-003 RESOLVED: align overlay names with CONSTRAINTS C3 (rename across 5 files)

### Action
Day 5 green-light arrived this morning; resume protocol surfaced
Session 2's DEP-003 (raised 2026-05-13) — Session 2's `docker-compose.
swarm.yml` (PR #18) declared three `external: true` overlay networks
matching **CONSTRAINTS C3 verbatim**:

- `yral-v2-public-web`
- `yral-v2-internal`
- `yral-v2-data-plane`

My Day-1-2 drafts (PR #9 + PR #10) had used different names taken
from `V2_INFRASTRUCTURE_AND_CLUSTER_ARCHITECTURE_CURRENT.md`:

- `yral-agent-public-web-overlay`
- `yral-agent-internal-service-to-service-overlay`
- `yral-agent-data-plane-overlay`

Per the CURRENT-TRUTH.md authority chain CONSTRAINTS wins when it
disagrees with the infra doc — which it did here. Session 2 was
right; Session 1 needed to rename.

Rishi typed YES on Option (a) this morning (typed-YES 2026-05-14)
covering both the rename PR + a narrow A1 carve-out to remove the
3 wrong-named overlays on the live cluster.

### Files touched (this PR)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/node-bootstrap.sh (constants + file-header doc + role-comment expanded to capture the doc-vs-CONSTRAINTS divergence for future re-readers)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/patroni-stack.yml (data-plane overlay refs)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/redis-sentinel-stack.yml (data-plane overlay refs)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/langfuse-stack.yml (data-plane + internal overlay refs)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/caddy-swarm-service.yml (public-web overlay refs)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md (DEP-003 moved to RESOLVED)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry)

### Why
Pre-condition for Day 5 stateful-core deploys + Session 2's Day 3
hello-world spawn. Without this fix, Patroni / Redis Sentinel /
Langfuse stacks Session 1 deploys would land on
`yral-agent-data-plane-overlay`, and Session 2's template-spawned
services would attempt to attach to `yral-v2-data-plane` (declared
`external: true`) — fail-fast at deploy with "network not found".

Per A2.1: pure mechanical rename, single concern, no new
abstractions, no new dependencies. ~30-line diff total. Same shape
as #19/#21/#23/#29/#33 small-fix pattern from Day 4.

### Test evidence
- `sed` substitution across 5 files (longest match first to avoid prefix-clobber).
- `bash -n node-bootstrap.sh` → syntax OK.
- `python3 -c "yaml.safe_load(...)"` against all 4 .yml stacks → all parse OK after placeholder substitution.
- Active `yral-agent-*` references: 0 across the 5 files. The single remaining mention is in a fresh role-comment that REFERENCES the old name as context (so future readers know what the rename corrected).
- New `yral-v2-{public-web,internal,data-plane}` reference counts per file: node-bootstrap.sh=4, patroni-stack.yml=3, redis-sentinel-stack.yml=3, langfuse-stack.yml=6, caddy-swarm-service.yml=4. Matches the original old-name counts.

### Cluster-side recovery (post-merge, narrow A1 carve-out)
After this PR merges I run a tight cleanup sequence per Rishi's typed-YES scope:

1. On each of rishi-4/5/6: `docker network inspect <each-old-name> --format '{{json .Containers}}'` to confirm zero containers attached. If ANY shows containers, STOP and ping.
2. `docker network rm yral-agent-public-web-overlay yral-agent-internal-service-to-service-overlay yral-agent-data-plane-overlay` — scope = exactly those 3 names.
3. Re-run swarm-init phase on rishi-4 only. Script's idempotency on the Swarm itself skips re-init (cluster + leader + labels + resync systemd unit all preserved). Overlay-create loop creates the new CONSTRAINTS-correct names with `encrypted=true`.
4. Verify `'{{index .Options "encrypted"}}'` returns `"true"` on all 3 new overlays on each of rishi-4/5/6.
5. Confirm `docker network ls --filter driver=overlay` shows only `yral-v2-*` + the Swarm `ingress` (no `yral-agent-*` remnants).
6. STOP. Ping with rename-cluster outcome before starting Day 5 Patroni.

### Blockers raised
None.

---

## 2026-05-13 — MILESTONE: Day 4 cluster bringup COMPLETE (3 nodes, 3 encrypted overlays, 5 script bugs caught + fixed)

### Summary
The v2 Docker Swarm cluster on rishi-4 / rishi-5 / rishi-6 is up. All
three nodes are managers with IPv4 advertise addresses; the three
intended encrypted overlays exist cluster-wide with `encrypted=true`
confirmed on both an edge node and the compute node; placement labels
match V2 §5 across all three; the H1 `yral-v2-swarm-resync.service`
systemd unit is enabled on every node; rishi-deploy with the CI key
works on every node (Sunday-deadline parity for permanent SSH
independent of root achieved).

### Final cluster state

```
$ docker node ls
ID                              HOSTNAME   STATUS  AVAILABILITY   MANAGER STATUS
eplqvaqurcf2ah7mzh01xs76s *     rishi-4    Ready   Active         Leader
6jvpxdj9s27kzyp8qnfghh7p9       rishi-5    Ready   Active         Reachable
aib9ppvtzid3ntntt32s54790       rishi-6    Ready   Active         Reachable
```

| Node | Advertise Addr | Labels | Role per V2 §5 |
|---|---|---|---|
| rishi-4 | `138.201.128.108:2377` | `node_role=edge, state_tier=primary` | edge + state primary |
| rishi-5 | `88.99.160.251:2377` | `node_role=edge, observability_tier=primary` | edge mirror + observability |
| rishi-6 | `162.55.88.112:2377` | `node_role=compute, langfuse_tier=primary` | compute + quorum + Langfuse |

Three encrypted overlays, propagated cluster-wide, `encrypted=true`
confirmed on rishi-4 (leader) and rishi-6 (compute, different host class):

- `yral-agent-public-web-overlay`
- `yral-agent-internal-service-to-service-overlay`
- `yral-agent-data-plane-overlay`

Systemd state: `yral-v2-swarm-resync.service` enabled on all 3 nodes
(per CONSTRAINTS H1 reboot resilience). `chrony` + `fail2ban` +
`unattended-upgrades` active on all 3.

### Phase walkthrough per node

**rishi-4 (138.201.128.108) — Swarm leader + state primary**
- root-window phase: ran twice today. First run failed at apt-get-update
  due to docker.sources idempotency miss (PR #19); second run after fix
  merge succeeded. Installed Docker (already present), chrony, fail2ban,
  unattended-upgrades, UFW rules, rishi-deploy + narrow sudoers + CI
  pubkey, sshd hardened.
- swarm-init phase: ran three times today. First run (with PR #19 fix)
  failed because of swarm-state substring trap (`grep active` matched
  `inactive`); PR #21 fix merged; second run created Swarm + 3 overlays
  + labels + systemd unit, but overlays were silently unencrypted
  (PR #23 root cause). After PR #23 + the narrow A1 carve-out for the
  3 unencrypted overlays (Rishi YES'd, scope = exactly those 3 names,
  zero containers attached at time of rm), third run finished with
  `encrypted=true` everywhere — but rishi-4 was now advertising IPv6
  (PR #29 root cause). After PR #29 merged, the A1-carve-out
  `docker swarm leave --force` on rishi-4 (Rishi YES'd, scope =
  rishi-4 only, cascade-destroys overlays + labels, preserves
  rishi-deploy/UFW/systemd/etc.), then re-ran swarm-init with
  `YRAL_NODE_ADVERTISE_IPV4=138.201.128.108`. Clean final state.

**rishi-5 (88.99.160.251) — Swarm manager + edge mirror + observability**
- root-window phase: ran cleanly on first try (PR #19 + #21 already
  merged by then).
- swarm-join phase: ran twice. First run (yesterday, pre-PR-#29)
  advertised IPv6 → leader timed out trying to call back → join
  failed → Docker rolled back BUT left a `Down` ghost node entry with
  hostname `rishi-5` in the cluster membership list. Second run
  (today, after PR #29 merge + re-scp of updated script) succeeded
  with IPv4 advertise. BUT the script's hostname-based
  `docker node update --label-add ... rishi-5` then errored with
  "node rishi-5 is ambiguous (2 matches found)" because of the ghost.
  Recovery: narrow A1 carve-out (Rishi YES'd, scope = exactly the
  ghost's node ID `jvt7swmbe2yvlaouoh4nytczs`) — `docker node rm`
  the ghost, then `docker node update --label-add ... 6jvpxdj9...`
  by explicit ID. Hardening PR #33 makes this hostname-ambiguity
  failure mode no longer possible.

**rishi-6 (162.55.88.112) — Swarm manager + compute + Langfuse host**
- root-window phase: ran cleanly on first try. Note: rishi-6's role is
  `compute` (not `edge`), so the script correctly did NOT open
  `ufw allow 443/tcp` — only rishi-4 + rishi-5 expose :443. Datacenter
  observation: rishi-6's IPv6 subnet `2a01:4f8:271:17c1::/64` differs
  from rishi-5's `2a01:4f8:10a:3116::/64`, consistent with the V2 §10
  open question of rishi-6 possibly being in NBG1 vs rishi-5/4 in
  FSN1. IPv4 cluster topology doesn't care, but worth noting for the
  Patroni async-replica positioning planned for Day 5.
- swarm-join phase: **single shot, ZERO bugs.** All script paths
  executed cleanly. Labels applied first try because rishi-6 was a
  fresh server with no prior Swarm history → no hostname ambiguity
  possible by construction. The "1 bug per attempt" pattern that held
  for the first four real-server attempts (rishi-4 root-window,
  rishi-4 swarm-init, rishi-5 swarm-join, rishi-5 label-apply) broke
  cleanly on the fifth.

### The 5-bug arc (PR table)

Each bug was a script check that worked against the "no edge case /
fresh box" mental model and failed against real-server state. Pattern:
pause-fix-merge-retry, single-concern PR, tight diff, no
over-engineered test harnesses (per A2.1). All five caught cleanly
with no server damage.

| PR | Function | Bug |
|---|---|---|
| #19 | `add_docker_apt_repository_if_missing` | Checked only legacy `docker.list`; missed deb822-format `docker.sources` Hetzner uses, so a second apt source got added with a different Signed-By key path. `apt-get update` errored with `Conflicting values set for option Signed-By`. |
| #21 | `initialize_docker_swarm_on_first_manager_node` + `join_docker_swarm_as_manager_node` | Used `grep active` (substring) to detect already-in-Swarm. Docker's `inactive` state contains `active` — naive grep matched, skipped `docker swarm init`, then `docker network create --driver overlay` errored with "This node is not a swarm manager". |
| #23 | `create_encrypted_overlay_networks` | Used `--opt encrypted` (no value). Docker CLI parses as `encrypted=""`, `strconv.ParseBool("")` returns false, IPsec silently NOT enabled even though the option key appears in `docker network inspect`. **CONSTRAINTS C3 violation.** Fix added `=true` plus a defense-in-depth verifier on the existing-overlay-skip path. |
| #29 | `initialize_docker_swarm_on_first_manager_node` + `join_docker_swarm_as_manager_node` | Used `hostname --ip-address | awk '{print $1}'` for `--advertise-addr`. On Hetzner Ubuntu, `hostname --ip-address` returns IPv6 first. Cluster ended up advertising IPv6 to itself, but UFW peer rules + `RISHI_N_PUBLIC_IPV4` secret scheme are IPv4-only → call-backs timed out → first swarm-join failed. Required a new `YRAL_NODE_ADVERTISE_IPV4` env var + the rishi-4 swarm-leave-and-re-init carve-out. |
| #33 | `apply_placement_labels_to_this_node` | Addressed `docker node update --label-add ...` by hostname. A stale ghost entry sharing the hostname (left by PR #29's prior failed join) made the command ambiguous. Fix uses local Swarm NodeID (globally unique) via `docker info --format '{{.Swarm.NodeID}}'`. |

### A1 deletion carve-outs Rishi typed YES on today
All three were narrow, scope-bound, recovery-only:

1. **`docker network rm`** of the 3 unencrypted overlays on rishi-4
   (scope = exactly those 3 names, ALL had zero containers attached at
   inspect time). Followed PR #23 merge.
2. **`docker swarm leave --force`** on rishi-4 (scope = rishi-4 only).
   Cascade-destroyed the (now-validated encrypted) overlays + placement
   labels; preserved rishi-deploy + UFW + sshd + chrony + fail2ban +
   `yral-v2-swarm-resync.service`. Required to re-init with IPv4 advertise
   after PR #29 merged.
3. **`docker node rm jvt7swmbe2yvlaouoh4nytczs`** — the `Down` ghost
   rishi-5 left by PR #29's earlier failed IPv6 join (scope = exactly
   that node ID, ghost had no `ManagerStatus` map at all so confidently
   identifiable as the stale entry).

### Operator note: IPsec XFRM policies are LAZY
Verified during the rishi-4 post-fix verification: `ip xfrm policy`
returns 0 lines on a single-manager Swarm even when the overlay's
`Options.encrypted` is `"true"`. **This is not a bug — Docker creates
XFRM policies lazily** when there's actual inter-node overlay traffic.
With only one node in the Swarm at init time, no policies are needed
yet. Policies populate cluster-wide once two or more nodes exchange
overlay traffic (e.g., when the first Patroni replica replicates to
its peer over the data-plane overlay on Day 5).

If a future operator is debugging "Swarm says encrypted but `ip xfrm
policy` is empty — is my overlay actually encrypted?", the answer is:
yes, IF (a) `docker network inspect <name> -f '{{.Options.encrypted}}'`
returns `"true"`, AND (b) actual cross-node service traffic is flowing
through the overlay. Without (b), the policies haven't been needed
yet, so they don't exist yet. The `--opt encrypted=true` setting is
the configuration that *triggers* policy creation when traffic flows;
the absence of policies pre-traffic is normal.

### Files touched in this close-out PR
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry — single milestone block covering the full Day 4 arc)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md (set to "Day 4 done; idle pending Day 5 green-light")

### Test evidence
All Day 4 acceptance criteria verified directly from rishi-4 leader +
rishi-6 compute (different host class for cross-check) in the runs
captured during the bringup:
- `docker node ls` → 3 Ready managers, IPv4 advertise on all three.
- `docker network inspect <name> -f '{{index .Options "encrypted"}}'`
  → `"true"` on all 3 overlays from both rishi-4 and rishi-6.
- `docker node inspect <id> -f '{{.Spec.Labels}}'` → matches V2 §5
  table on all 3 nodes.
- `systemctl is-enabled yral-v2-swarm-resync.service` → `enabled` on
  all 3 nodes.
- `ssh -i ~/.ssh/rishi-hetzner-ci-key rishi-deploy@<each-IP>` →
  succeeds on all 3 (Sunday-deadline parity for permanent SSH
  independent of root).

### Blockers raised
None for Day 4. Day 4 is fully closed.

### What's next: Day 5 (separate Rishi green-light required per A13)
Day 5 = stateful core deployment onto the now-live cluster:
- Patroni HA Postgres (`patroni-install.sh` + `patroni-stack.yml`)
  across rishi-4 (sync) / rishi-5 (sync replica) / rishi-6 (async
  replica) with sync commit per F3 + G3.
- Redis Sentinel (`redis-sentinel-install.sh` +
  `redis-sentinel-stack.yml`) with primary on rishi-4, replica on
  rishi-5, sentinels per C11.
- Langfuse self-hosted on rishi-6 (`langfuse-install.sh` +
  `langfuse-stack.yml`) per D4.
- Caddy as Swarm service on rishi-4/5 (`caddy-swarm-service.yml`)
  per C10.
- Chaos test runner (`run-all-chaos-tests.sh`) — H3 Phase 0 exit
  criterion.

All four scripts already drafted + merged on main (PRs #9 / #10 from
the Days 1-2 work + PRs #12 / #13 from Day 3 chaos tests). The five
fixes Day-4 surfaced are already in main, so the install scripts
won't re-trip on the same idempotency / advertise / labels traps when
they exercise the same Docker patterns.

---

## 2026-05-13 — HARDENING: apply_placement_labels_to_this_node targets local NodeID (defense-in-depth follow-up to rishi-5 ghost incident)

### Action
Surfaced by the rishi-5 Day-4 swarm-join incident earlier today. The
script's `apply_placement_labels_to_this_node` ran
`docker node update --label-add ... ${YRAL_NODE_NAME}` (i.e., addressed
by hostname). On rishi-5, yesterday's failed IPv6-advertise swarm-join
had left a Down ghost node with hostname `rishi-5` in the cluster's
membership list. When today's successful IPv4-advertise join created a
second `rishi-5` entry, `docker node update ... rishi-5` errored with:

```
Error response from daemon: node rishi-5 is ambiguous (2 matches found)
```

Labels never applied. Recovery required a narrow A1 carve-out
(`docker node rm` of the ghost) plus a one-off label-update targeted by
explicit node ID.

Hardening: capture the local node's Swarm-assigned NodeID via
`docker info --format '{{.Swarm.NodeID}}'`, then `docker node update`
by ID — not by hostname — in every case branch. A stale ghost sharing
the hostname can no longer make this command ambiguous because the ID
is globally unique. Added a role-comment block documenting the
rishi-5 ghost incident as the motivator so future re-readers don't
think the by-ID indirection is gratuitous.

Pre-flight: if `docker info` doesn't yield a NodeID (e.g., the swarm
init/join step earlier somehow didn't complete), the function exits 1
with a clear error pointing at the missing prerequisite.

### Files touched
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/node-bootstrap.sh (+18 / -3, single function)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry)

### Why
Day 4 close-sequence (step a per Rishi 2026-05-13). Defense-in-depth
against the failure mode the rishi-5 ghost incident surfaced. NOT
load-bearing for any rishi-N step we've already done — rishi-6's
swarm-join + labels landed clean on the first try without this fix
because rishi-6 was a fresh server with no prior Swarm history → no
hostname ambiguity possible by construction. Hardening is for the
NEXT cluster build (recovery, re-init, new node addition) where
hostname collisions are far more likely than on the green-field
first build.

Per A2.1: single concern, tight diff (+18/-3 net +15 lines), no new
abstractions, no new dependencies. Sixth Session-1 mechanical fix
PR in the #19/#21/#23/#29/(this) sequence — all same shape.

### Test evidence
- `bash -n node-bootstrap.sh` → syntax OK.
- `git diff --stat` → 1 file, +18/-3.
- Behaviour matrix:
  - Healthy single-Swarm-membership node (typical case) → NodeID
    capture succeeds, label-update by ID works identically to the
    old hostname-based path.
  - Node with stale ghost sharing hostname → label-update by ID
    targets the REAL node (the one whose NodeID `docker info`
    reports here, locally). Ghost is unaffected (correctly — it's
    a stale entry that someone else with A1 YES should clean up).
  - Node not yet in a Swarm → `docker info` returns empty NodeID
    → pre-flight exit 1 with clear pointer to missing prerequisite.
- No live re-run on real servers — Day 4 is done; this hardening
  doesn't need re-execution. It will exercise on Day-5+ if Patroni
  install scripts ever re-invoke label-apply or on the next fresh
  cluster build.

### Blockers raised
None.

### What's next after this merges (Day 4 close, step b)
Single `session-1/day-4-cluster-bringup-complete` PR with:
- One bundled SESSION-1-LOG milestone capturing all 3 nodes' phases
  (root-window + swarm-init/join), the 4 bugs-caught arc
  (substring / encrypted / IPv4 / ambiguity), the IPsec-XFRM-policies-
  lazy note for future operators, and the final cluster-state table.
- SESSION-1-STATE update to "Day 4 done; idle pending Day 5 green-light".
- No code changes; should auto-merge per I14 (.md-only + small +
  Codex APPROVE expected).

---

## 2026-05-13 — MILESTONE: Day 4 fix — IPv4 `--advertise-addr` (4th script bug; caught on rishi-5 swarm-join)

### Action
Caught on the **rishi-5 first swarm-join attempt** today, in the swarm-join
code path the user specifically flagged as having had less real-server
testing than swarm-init.

`docker swarm join` failed with:

```
Error response from daemon: manager stopped: can't initialize raft node:
rpc error: code = DeadlineExceeded desc = could not connect to prospective
new cluster member using its advertised address
```

Root cause: the script's `--advertise-addr` heuristic in both
`initialize_docker_swarm_on_first_manager_node` and
`join_docker_swarm_as_manager_node` was:

```bash
--advertise-addr "$(hostname --ip-address | awk '{print $1}')"
```

On Hetzner Ubuntu boxes, `hostname --ip-address` returns BOTH addresses
with **IPv6 first**:

```
2a01:4f8:10a:3116::2 88.99.160.251
```

`awk '{print $1}'` picks the IPv6. rishi-5 advertised its IPv6 to the
cluster; rishi-4 tried to connect back to verify; UFW on rishi-5
(IPv4-only peer allow rules from `YRAL_CLUSTER_PEER_CIDRS`) dropped the
IPv6 reply; the join timed out. Same bug exists on rishi-4 — it
advertised IPv6 during the earlier swarm-init, but failure was masked
because rishi-4 was alone in the cluster (no one needed to call back).

Fix on branch `session-1/fix-advertise-ipv4`: require a new env var
`YRAL_NODE_ADVERTISE_IPV4`, plumb it through `--advertise-addr` in both
functions, add a role-comment in each spot explaining the IPv6-first
trap. Pre-flight now asserts the env var for swarm-init + swarm-join
phases. File-header `INPUTS` section updated to document the new var.

### Files touched
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/node-bootstrap.sh (+28 / -2)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry)

### Why
A2.1 + the user's "1 bug per attempt across rishi-4, pause-fix-merge-retry"
protocol. 4th script bug Session 1 has caught in production execution
on Day 4 (docker.sources → swarm-state substring → overlay encrypted
flag → now advertise-addr IPv6-first). Pattern is holding: each bug
caught cleanly, single-concern PR, tight diff, no over-engineered test
harness per A2.1.

This one is the most cluster-architecturally significant of the four:
CONSTRAINTS C6 names the secrets `RISHI_N_PUBLIC_IPV4`, the UFW rules
take IPv4 CIDRs, the cluster.hosts.yaml shape lists IPv4 — all of these
are IPv4-only by intent. The script silently advertising IPv6 was the
inverse of that design.

### Test evidence
- `bash -n node-bootstrap.sh` → syntax OK.
- `grep -n "hostname --ip-address" node-bootstrap.sh` → 3 remaining matches,
  all inside role-comments explaining why the heuristic was removed.
  No live code path still uses the heuristic.
- Pre-flight matrix:
  - `YRAL_BOOTSTRAP_PHASE=root-window` (no IPv4 needed) — pre-flight OK.
  - `swarm-init` without `YRAL_NODE_ADVERTISE_IPV4` → fails pre-flight.
  - `swarm-join` without `YRAL_NODE_ADVERTISE_IPV4` → fails pre-flight.
  - With env var set → docker swarm init/join uses it via
    `--advertise-addr`.

### Day-4 recovery flow after this PR merges (per the user's typed YES)
1. `docker swarm leave --force` on rishi-4 (narrow A1 carve-out, scope =
   rishi-4 only). Cascades: the 3 currently-validated encrypted=true
   overlays are torn down, placement labels reset. rishi-deploy +
   sudoers + UFW + sshd + chrony + fail2ban + resync systemd unit STAY.
2. Re-run swarm-init phase on rishi-4 with
   `YRAL_NODE_ADVERTISE_IPV4=138.201.128.108`. Script recreates the 3
   overlays with `encrypted=true` (PR #23 fix), re-applies placement
   labels, idempotent systemd no-op.
3. Verify rishi-4 advertises 138.201.128.108:2377 (NOT IPv6 anymore).
4. STOP. Ping before touching rishi-5.

Then normal flow resumes: swarm-join rishi-5 with
`YRAL_NODE_ADVERTISE_IPV4=88.99.160.251`, then rishi-6 with
`YRAL_NODE_ADVERTISE_IPV4=162.55.88.112`.

### State of rishi-5 right now
- ✅ root-window phase fully landed (rishi-deploy + CI-key SSH verified,
  Sunday-deadline parity with rishi-4 cleared)
- ✅ Docker + chrony + fail2ban + UFW + sshd hardening intact
- ✅ Swarm: `inactive` (Docker rolled back the failed join cleanly — no
  partial state)
- No cleanup needed on rishi-5; just retry swarm-join after the merge +
  rishi-4 re-init.

### Blockers raised
None. PR (this one) + the typed A1 carve-out are the only blockers on
finishing Day 4. Same pause-fix-merge-retry cadence as the prior 3 PRs.

---

## 2026-05-13 — NOTE: PR #23 follow-up — defense-in-depth verify for existing overlays

### Action
Codex review on PR #23 flagged a real C3 gap that the encrypted-flag fix alone
did not close: `create_encrypted_overlay_networks` skips overlay creation when
the name already exists, but doesn't verify the existing overlay actually has
`encrypted=true`. A pre-existing UNencrypted overlay (legacy provision, manual
`docker network create`, prior buggy run of this very script) would be
silently accepted — exact C3 violation invisible on the second-run path. Today's
trap on rishi-4 (PR #23's body) would have been undetectable to the script on
a re-run.

Added a defense-in-depth check inside the "already exists — skipping" branch:
read `Options.encrypted` via `docker network inspect` and `exit 1` with a clear
error if it isn't `"true"`. Fail loud, do NOT auto-rm — auto-rm would re-open
the A1 deletion surface. Remediation requires a separate Rishi YES.

Pushed as a follow-up commit on the existing PR #23 branch
(`session-1/fix-overlay-encrypted-flag`).

### Files touched
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/node-bootstrap.sh (+17 / -1; one branch in one function)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this note)

### Why
Codex caught a real defense-in-depth issue on the PR. CONSTRAINTS C3 violation
is the stake — Codex was right; merging PR #23 without this check would have
left the gap permanently open even though the immediate bug was fixed. Per
A2.1: this is tightening an existing verification (not adding new abstractions
or test harnesses), single-concern, ~17 lines, well under the 100-line trigger.

### Test evidence
- `bash -n node-bootstrap.sh` → syntax OK.
- Code path matrix:
  - Overlay does not exist → script creates with `encrypted=true` (unchanged).
  - Overlay exists with `encrypted=true` → script skips (unchanged, just prints clearer message).
  - Overlay exists with `encrypted=""` or `encrypted=false` or missing key →
    script EXIT 1 with ERROR pointing the operator at manual A1 remediation
    (new behaviour — was silent skip before).
- Diff: +17 / -1, single branch inside the existing function. No new functions,
  no new dependencies.

### Blockers raised
None. After PR #23 (this updated version) merges, the previously-planned
rishi-4 recovery still applies: rm the 3 unencrypted overlays under the
pre-authorised narrow A1 carve-out, re-run swarm-init, verify encrypted=true,
STOP, ping.

---

## 2026-05-13 — MILESTONE: Day 4 fix — overlay `--opt encrypted=true` (PR coming)

### Action
Caught on the rishi-4 verify-after-swarm-init step today, immediately
after PR #21 (swarm-state exact-match) merged and swarm-init re-ran
cleanly. The Swarm came up healthy with rishi-4 as leader; placement
labels + resync systemd unit landed correctly. **But** the three named
overlays (`yral-agent-public-web-overlay`,
`yral-agent-internal-service-to-service-overlay`,
`yral-agent-data-plane-overlay`) were created **without IPsec
encryption** despite being intended-encrypted per CONSTRAINTS C3.

Root cause: my `docker network create` invocation used the value-less
flag form

```bash
docker network create --driver overlay --opt encrypted --attachable <name>
```

Docker CLI parses a value-less `--opt encrypted` as `encrypted=""`. The
overlay driver then runs `strconv.ParseBool("")` which returns false,
so IPsec is silently NOT enabled — even though `docker network inspect`
shows the key. Three overlays sat on rishi-4 with `"encrypted":""` in
their Options map (verified via `docker network inspect --format
'{{json .Options}}'`).

Fix on branch `session-1/fix-overlay-encrypted-flag`: change `--opt
encrypted` → `--opt encrypted=true`. One-line code change. Expanded
the function's role-comment to capture the trap (so future re-readers
don't re-shorten back to the value-less form).

Per the user's pre-authorised narrow A1 carve-out for this recovery:
after PR merges I `docker network rm` the 3 unencrypted-but-named-
correctly overlays on rishi-4 (scope strictly = the 3 names listed
above, ONLY because they are minutes-old artifacts my own script
just created with the wrong flag, and only after confirming zero
attached services on each via `docker network inspect | jq
'.[].Containers'`). Then re-run swarm-init phase; the script's
existing idempotency on the Swarm itself preserves the cluster +
leader + labels + resync systemd unit, while the overlay-create loop
re-creates the 3 networks with `encrypted=true` properly applied.

### Files touched
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/node-bootstrap.sh (+7 / -2)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry)

### Why
A2.1 + the user's "if anything goes weird, STOP and ask" rule. Third
script bug Session 1 has caught in production execution on rishi-4
today (docker.sources path mismatch → swarm-state substring trap →
this overlay encrypted-flag misuse). Pattern is holding: each bug
caught cleanly, no server damage, tight single-concern fix PR, ~few
hours total slowdown. Per A2.1 not over-engineering test harnesses —
the pause-fix-merge-retry loop is the right cadence for a one-shot
bootstrap touching real production-shape infra for the first time.

CONSTRAINTS C3 explicitly mandates encrypted overlays; shipping the
unencrypted ones would have been a real violation, not cosmetic. Worth
the pause.

### Test evidence
- `bash -n node-bootstrap.sh` → syntax OK.
- Diff: +7 / -2, single file, single function. Well under A2.1's
  100-line trigger.
- Verification step planned post-merge:
  `docker network inspect <name> --format '{{.Options.encrypted}}'`
  returns `"true"` (not empty string) for all 3 overlays.

### State of rishi-4 right now
- ✅ rishi-deploy + CI-key SSH (Sunday deadline cleared)
- ✅ Docker + chrony + fail2ban + UFW + sshd hardening
- ✅ Swarm: active, rishi-4 = leader/manager (1/3 nodes; 5+6 still
       pending separate Rishi green-light)
- ✅ Placement labels: `node_role=edge, state_tier=primary`
- ✅ yral-v2-swarm-resync.service installed + enabled
- ❌ 3 named overlays exist but ARE NOT encrypted — to be removed
       after PR merges per the pre-authorised narrow A1 carve-out

### Blockers raised
None. PR (this one) is the only blocker on Day 4 re-verify; once it
merges I rm the 3 unencrypted overlays, re-run swarm-init, verify
encryption flag is `"true"`, STOP for Rishi green-light on rishi-5/6.

---

## 2026-05-13 — MILESTONE: Day 4 fix — swarm-state exact-match idempotency bug (PR #21)

### Action
Caught on the rishi-4 first swarm-init run today, right after PR #19
(docker.sources idempotency fix) merged. The idempotency check in both
`initialize_docker_swarm_on_first_manager_node` and
`join_docker_swarm_as_manager_node` was:

```bash
if docker info --format '{{.Swarm.LocalNodeState}}' | grep --quiet active; then
    return 0
fi
```

Docker's possible Swarm states include `inactive` for an un-joined node.
`grep active` is a substring match — and "inactive" contains "active" —
so the check returned true on a node that was NOT yet in a Swarm. Script
skipped `docker swarm init`, then `docker network create --driver overlay`
errored with "This node is not a swarm manager" and `set -e` exited
cleanly with no partial state on the box.

Fix on branch `session-1/fix-swarm-state-exact-match`: capture state once
into `swarm_local_node_state`, compare with `[[ ... == "active" ]]`
exact equality. Added a role-comment in each spot explaining *why*
exact-match (so future re-readers don't reintroduce the substring trap).
Same low-risk pattern as PR #19.

### Files touched
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/node-bootstrap.sh (+13 / -2, two functions)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry)

### Why
A2.1 + the user's "if anything goes weird, STOP and ask" rule. Second
script-idempotency bug Session 1 has caught against unexpected
real-server state (PR #19 was the first). Both fixes followed the same
pause-fix-merge-retry loop — no server damage, no over-engineered test
harness, just tight diffs against the real failure mode.

### Test evidence
- `bash -n node-bootstrap.sh` → syntax OK.
- `grep --quiet active` across the whole bootstrap folder → 0 remaining
  matches. Sibling scripts (patroni-install, redis-sentinel-install,
  langfuse-install, chaos tests) already use `[[ ... == "active" ]]`.
- Behaviour matrix in the PR body covers the four LocalNodeState values
  (inactive / active / pending|locked / error).
- **No chaos run** — drafts only. State of rishi-4 right now: root-window
  phase landed on the prior run (rishi-deploy + CI-key SSH verified =
  Sunday deadline cleared), Docker + chrony + fail2ban + UFW + sshd
  hardening done; Swarm itself still inactive (the next step after this
  fix merges).

### Blockers raised
None. PR #21 is the only blocker on Day 4 re-run; once it merges I
`git pull` in the worktree, re-run swarm-init on rishi-4, verify the
3 overlays + placement labels + resync systemd unit, then STOP for
Rishi green-light on rishi-5/6.

---

## 2026-05-05 — MILESTONE: Day 3 chaos-test drafts (fill + partition + runner → PR B)

### Action
Drafted the remaining three Phase 0 H3 chaos files on branch
`session-1/day-3-chaos-tests-fill-partition-runner`. PR B bundles three
new files in `bootstrap-scripts-for-the-v2-docker-swarm-cluster/chaos-tests/`:

1. **`fill-rishi-5-disk.sh`** (277 lines) — `fallocate`s a single dummy
   file on rishi-5's `/data` partition sized to bring usage to ~80%
   (matches the `disk free < 20%` Alertmanager threshold per V2 §6.5),
   waits 5 minutes, asserts the `DiskFreeLessThan20Percent` alert is
   firing in Alertmanager's `/api/v2/alerts` API, runs a write+read
   sanity to confirm Patroni still accepts writes under disk pressure,
   then `rm`s the dummy file and polls `df` until usage falls back
   below threshold. Cleanup trap deletes the dummy file even on
   early failure.
2. **`partition-rishi-6.sh`** (302 lines) — captures rishi-6's IPv4
   then runs `iptables --append INPUT/OUTPUT --jump DROP` on rishi-4 +
   rishi-5 (both directions, every packet to/from the captured IP),
   tagged with the unique comment `yral-v2-chaos-partition-rishi-6` so
   cleanup deletes only our rules. Holds the partition for 10 minutes
   per H3 row 4. Verifies (a) etcd quorum healthy on rishi-4/5,
   (b) Patroni still committing writes, then removes iptables rules and
   confirms rishi-6's etcd member reports healthy again. EXIT trap
   removes iptables rules even on early failure — leaving DROP rules
   in place would permanently break the cluster.
3. **`run-all-chaos-tests.sh`** (280 lines) — Phase 0 exit-criteria
   orchestrator that invokes all four chaos scripts in sequence with a
   2-minute settle window between each. Writes a Markdown report at
   `/tmp/yral-v2-chaos-test-report-<YYYY-MM-DD-HHMM>.md` with each test's
   start/end times + pass/fail outcome. Operator pastes this into the
   Phase 0 completion checklist on Day 6.

PR A (kill scripts) is open at #12 with `kill-rishi-6.sh` and
`kill-patroni-leader.sh`. PR B's orchestrator references PR A's scripts;
both are independently reviewable.

### Files touched
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/chaos-tests/fill-rishi-5-disk.sh (new, 277 lines)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/chaos-tests/partition-rishi-6.sh (new, 302 lines)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/chaos-tests/run-all-chaos-tests.sh (new, 280 lines)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md (resume snapshot)

### Why
Phase 0 Day 3 deliverable per agent spec line 70-72 + CONSTRAINTS H3.
Drafts only — no chaos run anywhere. Day 6 is the first time these
scripts touch the cluster, with separate Rishi YES per A13.

### Test evidence
- `bash -n` against all three scripts → syntax OK.
- B2 banned-abbrev grep → matches limited to literal `/tmp/...` lock
  paths (Linux), `--arg` (jq flag). Same exemption pattern as PR #4 /
  #9 / #10 / PR A.
- **No chaos run** — drafts only. Same triple-gated trigger as PR A's
  scripts: `YRAL_CHAOS_RUN_AUTHORISED` matches today + Swarm-manager +
  lock file. Cleanup traps tested by reading the EXIT trap exits in
  bash but not by actual chaos execution.

### PR split rationale
Day 3 bundle is ~1381 lines of code total (5 chaos files). Per the
user's "if past 1000 lines, split into 2 PRs" instruction:
- PR A (#12): kill scripts (522 lines, ~620 with LOG/STATE).
- PR B (this): fill + partition + orchestrator (859 lines, ~960 with
  LOG/STATE).

### Blockers raised
None.

---

## 2026-05-05 — MILESTONE: Day 3 chaos-test drafts (kill scripts → PR A)

### Action
Drafted the two "kill" chaos tests for Phase 0 H3 exit criteria on branch
`session-1/day-3-chaos-test-scripts`. PR A bundles two new files in
`bootstrap-scripts-for-the-v2-docker-swarm-cluster/chaos-tests/`:

1. **`kill-rishi-6.sh`** (235 lines) — drains rishi-6 from the Swarm via
   `docker node update --availability drain`, waits 60 s, asserts every
   hot-path service has 0 replicas on rishi-6 + Patroni leader still
   rishi-4 + etcd quorum healthy on remaining members, then sets the
   node back to `availability=active`. Triple-gated trigger
   (`YRAL_CHAOS_RUN_AUTHORISED=$(date +%Y-%m-%d)` + Swarm-manager check
   + lock file). Idempotent + reversible.

2. **`kill-patroni-leader.sh`** (287 lines) — discovers the current
   Patroni leader via REST API, SIGKILLs the underlying container,
   polls Patroni until SOME other node reports `leader` role within 30 s
   (matches Patroni's `loop_wait × 3` default), runs a write+read
   sanity roundtrip via pgBouncer to confirm no data loss, then waits
   for the killed container to rejoin as a follower (replica or
   sync_standby).

PR B (queued, separate branch from main) will hold fill-rishi-5-disk.sh
+ partition-rishi-6.sh + run-all-chaos-tests.sh orchestrator. Split per
user instruction "if past 1000 lines, split into 2 PRs": full bundle is
~1381 lines of code, this PR is ~620 (kill scripts + LOG/STATE).

### Files touched
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/chaos-tests/kill-rishi-6.sh (new, 235 lines)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/chaos-tests/kill-patroni-leader.sh (new, 287 lines)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md (resume snapshot)

### Why
Phase 0 Day 3 deliverable per agent spec line 70-72 + CONSTRAINTS H3.
Drafts only — no servers touched, no chaos run anywhere. Real execution
happens Day 6 of cluster provisioning with separate explicit Rishi YES.

### Test evidence
- `bash -n` against both scripts → syntax OK.
- B2 banned-abbrev grep → matches limited to: literal `/tmp/...` lock
  paths (Linux standard), `--arg` (jq command-line flag). Same exemption
  pattern as PR #4 / #9 / #10 (`keychain-db`, `/tmp`, `/var/lib/etcd`).
  CI lint scopes to `*.py` so `.sh` doesn't fail.
- **No chaos run** — drafts only. The triple-gated authorisation
  refuses to run unless `YRAL_CHAOS_RUN_AUTHORISED` equals today's date
  AND the operator is on a Swarm manager AND no other chaos run is in
  progress. Running on Day 6 will be the first time any of these
  scripts touch the cluster.

### Blockers raised
None.

---

## 2026-05-05 — MILESTONE: Day 1-2 stateful core drafts (PR B)

### Action
Drafted the stateful-core portion of the rishi-4/5/6 cluster bootstrap on
branch `session-1/cluster-stateful-core-draft` (separate branch from
PR A's `session-1/cluster-bootstrap-scripts-draft`). PR B bundles six new
files in `bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/`:

1. **`patroni-install.sh`** (329 lines) — pre-flight checks (Swarm
   manager + required env vars + data-plane overlay), creates `/data/
   patroni-data` bind-mount on each node, materialises 5 SHA-rotating
   Swarm secrets per H2, envsubst-renders the stack, deploys, registers
   with the H1 resync service.
2. **`patroni-stack.yml`** (368 lines) — 3 etcd services pinned via
   `node.hostname` constraints to rishi-4/5/6, 3 Spilo Patroni services
   (`ghcr.io/zalando/spilo-15:3.0-p1`) one per host, sync commit on ≥1
   replica per F3, async-only tag on rishi-6 per V2 §5 cross-DC plan,
   2-replica edoburu pgBouncer per G3, all on data-plane overlay only.
3. **`redis-sentinel-install.sh`** (205 lines) — same install pattern,
   1 SHA-rotating secret (`REDIS_PRIMARY_PASSWORD`).
4. **`redis-sentinel-stack.yml`** (242 lines) — Redis 7 primary on
   rishi-4 (with AOF + RDB + 8GB maxmemory-policy=allkeys-lru), replica
   on rishi-5, 3 Sentinels (one per host) with quorum=2 and 5s
   `down-after-milliseconds` per C11.
5. **`langfuse-install.sh`** (227 lines) — same install pattern, 4
   SHA-rotating secrets (NextAuth + Encryption + Postgres + ClickHouse).
6. **`langfuse-stack.yml`** (191 lines) — Langfuse 3 web + worker pinned
   to rishi-6 via `node.hostname`, ClickHouse 24.3 on rishi-6 for trace
   events, Postgres metadata on the shared Patroni cluster (`langfuse`
   schema). Web spans both data-plane and internal-service overlays so
   v2 services can post traces.

PR A (foundation: node-bootstrap + caddy + secrets-manifest) is open at
https://github.com/dolr-ai/yral-rishi-agent/pull/9.

### Files touched
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/patroni-install.sh (new, 329 lines)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/patroni-stack.yml (new, 368 lines)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/redis-sentinel-install.sh (new, 205 lines)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/redis-sentinel-stack.yml (new, 242 lines)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/langfuse-install.sh (new, 227 lines)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/langfuse-stack.yml (new, 191 lines)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md (resume snapshot)

### Why
Phase 0 Day 1-2 stateful-core deliverable per agent spec. Drafts only —
no SSH to rishi-4/5/6, no live data pulls. Days 4-7 execution requires
separate explicit Rishi YES per A13.

Anchored constraints: A13 (drafts only), B1/B2/B5/B7 (English naming +
3-tier doc), C3 (data-plane overlay only — no host ports), C11 (Redis
Sentinel topology), D2 (WAL-G archive command for L2 backups), D4
(Langfuse self-hosted on rishi-6), D7 (secrets manifest), F3 (HA
Postgres + sync commit + schema-per-service), G3 (pgBouncer in front),
H1 (resync service registration), H2 (SHA-rotating Swarm secret names).

### Test evidence
- `bash -n` against all three install scripts → syntax OK.
- `python3 yaml.safe_load` against all three stack YAMLs (with placeholder
  substitution for `${YRAL_*_RESOLVED_*}`) → parse OK. Initial
  redis-sentinel-stack.yml had a YAML-vs-shell-heredoc indentation bug
  in the Sentinel command blocks; fixed by switching from `command: >`
  folded scalar to `command: [sh, -c, |...]` literal-block form so the
  embedded heredoc terminates at column 0 after YAML strips leading
  whitespace.
- B2 banned-abbrev grep across all 6 files → clean. Three matches are
  literal Linux paths (`/var/lib/etcd`, `/var/lib/clickhouse`,
  `/tmp/...`) — same exemption logic as PR A's `/tmp` and `keychain-db`.
- **No live execution** — drafts only.

### Codex truncation note
Bundle is 1562 lines of code/yaml + ~150 lines of LOG/STATE = ~1712 line
PR diff. Codex's smart-truncation guard (per coordinator commit
`3a42a93`) will likely cap visibility at ~800 lines; I've ordered the
files in `git add` so Patroni (the most security/correctness-critical)
appears first in the diff. Could split further into 3 PRs (Patroni;
Redis; Langfuse) but the user explicitly requested ≤2 PRs for the Day
1-2 bundle, and 3 PRs would multiply review overhead.

### Blockers raised
None.

---

## 2026-05-05 — MILESTONE: Day 1-2 cluster bootstrap drafts (PR A: foundation)

### Action
Drafted the foundation portion of the rishi-4/5/6 cluster bootstrap on
branch `session-1/cluster-bootstrap-scripts-draft`. PR A bundles three
files in `bootstrap-scripts-for-the-v2-docker-swarm-cluster/`:

1. `scripts/node-bootstrap.sh` — three-phase bootstrap (root-window /
   swarm-init / swarm-join). Phase routing via `YRAL_BOOTSTRAP_PHASE`
   env var. Pre-flight refuses non-root + non-Ubuntu-24.04. root-window
   phase installs Docker, creates rishi-deploy + narrow sudoers,
   configures UFW with allow-list-only SSH + per-role port rules,
   enables unattended security upgrades, disables root password auth.
   swarm-init phase initialises Docker Swarm on rishi-4, creates the
   three encrypted overlay networks per CONSTRAINTS C3, applies
   placement labels, installs the H1 yral-v2-swarm-resync.service.
   swarm-join joins rishi-5/6 with same systemd + label setup.

2. `scripts/caddy-swarm-service.yml` — Caddy 2.8.4 as a 2-replica Swarm
   service pinned to edge-labelled nodes (rishi-4, rishi-5). Ingress
   mode :443 only (CONSTRAINTS C3), `tls internal`, attached to the
   public-web overlay only (NOT internal/data-plane — isolation), per-
   replica volume for cert cache, SHA-rotating Caddyfile via Swarm
   config object alias `yral_v2_edge_caddyfile_current` (CONSTRAINTS H2).
   read_only filesystem + tmpfs for /tmp.

3. `secrets-manifest.yaml` — declarative cluster-level manifest in the
   D7 schema. 16 secrets declared: HETZNER_CI_SSH_PRIVATE_KEY,
   RISHI_{4,5,6}_PUBLIC_IPV4, POSTGRES_SUPERUSER_PASSWORD,
   PATRONI_{REPLICATION,REST_API}_PASSWORD, REDIS_PRIMARY_PASSWORD,
   LANGFUSE_{NEXTAUTH_SECRET,ENCRYPTION_KEY},
   HETZNER_S3_{ACCESS_KEY_ID,SECRET_ACCESS_KEY},
   BACKBLAZE_B2_{APPLICATION_KEY_ID,APPLICATION_KEY_SECRET},
   GOOGLE_CHAT_WEBHOOK_URL, GHCR_PULL_TOKEN. Each entry: required_in
   per env, source per env, rotation_policy with runbook, consumed_by
   cross-references, classification (blast_radius / access_pattern /
   sensitivity).

PR B (queued, separate branch + PR after PR A merges) will hold
patroni-install.sh + redis-sentinel-install.sh + langfuse-install.sh.

### Files touched
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/node-bootstrap.sh (new, 599 lines)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/scripts/caddy-swarm-service.yml (new, 184 lines)
- bootstrap-scripts-for-the-v2-docker-swarm-cluster/secrets-manifest.yaml (new, 378 lines)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md (resume snapshot updated)

### Why
Phase 0 Day 1-2 deliverable per agent spec (`.claude/agents/session-1-
infra-cluster.md` line 64-69). Drafted only — no SSH to rishi-4/5/6,
no live data pulls, per CONSTRAINTS A13. Days 4-7 execution requires
separate explicit Rishi YES.

### Test evidence
- `bash -n node-bootstrap.sh` → syntax OK.
- `python3 -c "yaml.safe_load(...)"` against caddy + secrets-manifest → parse OK.
- B2 banned-abbrev grep across all three files → clean. (One false-positive
  match in caddy-swarm-service.yml is the literal Linux mount path
  `/tmp` — same exemption logic as the `keychain-db` match in PR #4;
  CI lint scopes to *.py so YAML never trips.)
- `python3 -c "..."` against secrets-manifest confirmed 16 secrets
  parse with all required fields (name, classification.sensitivity,
  source per env, rotation_policy).
- **No live execution** — drafts only, per A13. Ubuntu version check,
  Swarm init, UFW config, etc. will be verified on real rishi-4/5/6
  during Day 4-6 with separate Rishi YES.

### PR split rationale
Bundle would have hit ~1900 lines including PR B contents. Per user
guidance "<800 per PR for Codex truncation" we split into:
- **PR A (this commit)**: foundation = node + edge + secrets manifest (~1160 lines).
- **PR B (next)**: stateful core = patroni + redis + langfuse (~800 lines).

PR A is still over 800 because node-bootstrap is unavoidably a
~600-line script (multiple phases + B7 doc on each). Codex will
truncate but should see the most security-critical paths first
(pre-flight, UFW, sudoers, Swarm init).

### Blockers raised
None. All three files in Session 1 scope per the lint-scope-violations
fix from PR #5.

---

## 2026-05-04 — MILESTONE: Session 1 launched + Day 0.5 deliverable opened

### Action
First Session 1 launch. Read all 11 mandatory pre-work files (CONSTRAINTS,
CURRENT-TRUTH, MASTER-PLAN, SESSION-SHARDING, AUTO-MODE-GUARDRAILS, TIMELINE,
STATE-PERSISTENCE, db-schema-ownership, V2 infra arch, plus my own STATE +
LOG stubs). Confirmed orientation to Rishi; received "continue".

Built the Day 0.5 deliverable end-to-end on branch
`session-1/sentry-baseline-cron`:

1. `pull-sentry-baseline.py` — Python 3 stdlib-only script that
   reads `SENTRY_AUTH_TOKEN` from macOS Keychain via
   `security find-generic-password -a dolr-ai -s SENTRY_AUTH_TOKEN -w`,
   calls Sentry Discover API on `sentry.rishi.yral.com` for top 30
   transactions in `yral-chat-ai` over the last 24 hours, appends one
   row per transaction to `daily-baseline.csv`, and atomically rewrites
   `latest-baseline.md` for at-a-glance reading.
2. `pull-sentry-baseline.plist.template` — launchd schedule firing daily
   at 9:00 a.m. local time (Asia/Kolkata on Rishi's MacBook = 9 a.m. IST),
   with Background process priority and queued-on-wake behaviour.
3. `install-launchd-job.sh` — idempotent installer that renders the plist
   into `~/Library/LaunchAgents/`, validates with `plutil -lint`, boots
   any prior version out, and `launchctl bootstrap`s the new copy.
4. `secrets.yaml` — per-folder declarative secrets manifest in the
   schema CONSTRAINTS D7+D8 require, declaring SENTRY_AUTH_TOKEN with
   source = macOS Keychain (local), rotation runbook, and the
   `consumed_by` cross-reference to the Python script.
5. `README.md` — first-time install, verify-it-ran, troubleshooting,
   uninstall, and rotation instructions written for a non-programmer
   reader (per B7 + Rishi's ADHD framing).

### Files touched
- yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/pull-sentry-baseline.py (new)
- yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/pull-sentry-baseline.plist.template (new)
- yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/install-launchd-job.sh (new)
- yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/secrets.yaml (new)
- yral-rishi-agent-plan-and-discussions/latency-baseline-capture-from-live-services-the-numbers-v2-must-beat/scripts/README.md (new)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md (DEP-001 raised)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-logs/SESSION-1-LOG.md (this entry)
- yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/session-state/SESSION-1-STATE.md (resume snapshot updated)

### Why
Per CONSTRAINTS row E1 (HARD: v2 must run ≥50% faster than Python yral-chat-ai)
and the agent spec, Day 0.5 is the very first deliverable. The CSV that grows
from this cron is the moving target every future v2 PR's latency gate compares
against. Pre-authorized by I7 (Sentry API aggregated reads — no per-run YES).

### Test evidence
- `python3 -c "import ast; ast.parse(...)"` — syntax valid.
- `bash -n install-launchd-job.sh` — bash syntax valid.
- `plutil -lint` against rendered plist (with placeholders substituted) — OK.
- `python3 -c "import yaml; yaml.safe_load(secrets.yaml)"` — parses cleanly.
- B2 banned-abbrev grep across `*.py *.sh *.yaml *.template` — clean.
  (One match in README.md is the literal macOS filename `login.keychain-db`,
  which references an external system path; CI lint scopes to `*.py` so this
  is not a CI concern.)
- Live end-to-end smoke against Sentry NOT run yet — depends on Rishi adding
  the Keychain entry per the README's first-time-install steps. The script's
  failure modes are surfaced via launchd's StandardErrorPath log file.

### Blockers raised
- **DEP-001** in cross-session-dependencies.md flags three CI/scope mismatches
  between the agent spec, the workflow definitions, and the real folder
  paths. Coordinator decision needed before this PR can pass CI.
- **DEP-002** raised AFTER first commit landed: the
  `.claude/hooks/post-tool-use.sh` heredoc has an unquoted tag that fires
  a bash parser error on every commit. Commit itself succeeds; the hook
  fails to write the auto-diary entry. Manual milestone entries are the
  workaround. Coordinator should change `<<ENTRY` to `<<'ENTRY'` and
  rework the variable substitution.

---

