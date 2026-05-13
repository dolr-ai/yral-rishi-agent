# Session 1 LOG — Infra & Cluster
> Append-only diary. Most recent entries at TOP. Auto-appended by `.claude/hooks/post-tool-use.sh` on every git commit. Manual milestone entries welcome.

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

