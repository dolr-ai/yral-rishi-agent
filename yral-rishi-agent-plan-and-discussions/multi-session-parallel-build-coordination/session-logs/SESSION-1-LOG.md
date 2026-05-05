# Session 1 LOG — Infra & Cluster
> Append-only diary. Most recent entries at TOP. Auto-appended by `.claude/hooks/post-tool-use.sh` on every git commit. Manual milestone entries welcome.

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

