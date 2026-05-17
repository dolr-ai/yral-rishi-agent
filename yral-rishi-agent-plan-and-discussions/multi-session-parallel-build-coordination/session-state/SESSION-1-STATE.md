# Session 1 STATE — Infra & Cluster
> Updated: 2026-05-17 (**Day-5 COMPLETE — Phase 0 OFFICIALLY CLOSED.** All 5 Day-5 steps closed, H3 chaos verification 3-of-4 PASS with Test 4 deferred to Phase 1+ per coordinator call. v2 stateful core operational on rishi-4/5/6. Session 1 idle pending Phase 1 green-light).

## ⭐ START-OF-SESSION SUMMARY (read first when resuming)

I am Session 1. I own infrastructure: rishi-4/5/6 cluster bootstrap (Docker
Swarm + Patroni HA + Redis Sentinel + Langfuse + Caddy Swarm service), the
Sentry baseline cron, chaos tests, and the rishi-1/2/3 Caddy snippet via the
yral-rishi-hetzner-infra-template repo (Day 7, currently deferred per agent
spec + A2 tightening 2026-05-13).

## LAST THING I DID

**Day-5 COMPLETE 2026-05-17. PHASE 0 OFFICIALLY CLOSED.**

All 5 Day-5 steps closed in sequence (Patroni HA → Redis Sentinel → Langfuse → Caddy edge-ingress → chaos-test H3 verification). v2 cluster stateful core operational on rishi-4/5/6 with verified HA semantics.

Live cluster state at Phase 0 close (12:55 UTC):

```
$ patronictl list  (yral-v2-postgres)
patroni-rishi-4  | Leader        | TL 11 |
patroni-rishi-5  | Replica       | TL 11 | lag 0
patroni-rishi-6  | Sync Standby  | TL 11 | lag 0

$ docker service ls --filter name=yral-v2-langfuse
langfuse-clickhouse  : 1/1
langfuse-web         : 1/1 (healthy)
langfuse-worker      : 1/1

$ docker service ls --filter name=yral-v2-edge-caddy
caddy-edge-ingress   : 2/2 on edge nodes (rishi-4 + rishi-5)
```

H3 chaos verification: Tests 1+2+3 PASSED programmatically (drain, kill-leader, fill-disk). Test 4 (partition) deferred to Phase 1+ per coordinator call — partition-graceful-degradation testing is moot until Sessions 3+4 deploy apps with that behavior; sudoers expansion for `sudo iptables` deferred to bounded privileged-sidecar approach when Phase 1 begins.

Full Day-5 close detail: see latest SESSION-1-LOG.md entry (timeline, 17-bug audit, 2 override invocations, 25 captured insights, 5 queued follow-ups).

## CURRENT TASK

**Idle pending Phase 1 green-light from coordinator.** Session 1's Phase 0 deliverables are complete. Phase 1 is Sessions 3+4 spawning real services (public-api, conversation-turn-orchestrator, etc.) against this cluster.

When Phase 1 starts, Session 1's planned work picks up the queued follow-ups (see below) as services come online. No process gate from Session 1's side; the gate is at the coordinator level (deciding when Phase 1 launches based on Sessions 2 + 5 progress).

## NEXT 3 PLANNED ACTIONS (when Phase 1 launches)

1. **`session-1/caddy-swarm-config-generator`**: materialize per-service Caddy routes from a template + service registry as Sessions 3+4 register their first deliverable. Replaces the Phase-0 `placeholder.rishi.local` site block.
2. **`session-1/deprecate-langfuse-bypass`**: re-deploy Langfuse without `YRAL_LANGFUSE_SKIP_PREFLIGHT_BIND_MOUNT_VERIFY` env var (PR #72 bypass) — now that PR #75 provisioned intra-cluster SSH the verifier works natively.
3. **`session-1/long-run-stability-check`**: extend `confirm_stack_actually_deployed` post-deploy verifier with a 5-min `sleep + service ps` gate across all `*-install.sh` scripts.

## BLOCKERS

None technical. Phase 1 launch is GATED on coordinator's overall multi-session readiness call (Sessions 2 + 5 progress alongside this Session 1 close).

Day 7 (rishi-1/2/3 Caddy snippet via the `yral-rishi-hetzner-infra-template` repo) remains DEFERRED per agent spec + A2 tightening 2026-05-13.

## PENDING PRs (mine)

- This PR (`session-1/day-5-close`) is the close LOG entry + state file update. `.md`-only; auto-merge eligible if under 400 lines, otherwise admin-merged manually per coordinator (same pattern as PR #76 / Step-3-close).
- No other PRs pending. Queued follow-ups (see top of file) wait for Phase 1.

## MERGED PRs (mine, 2026-05-17 — Day-5 Step 5 chaos-test hardening arc)

- **PR #80** — host-vs-overlay-DNS + trap-cleanup + service-existence-gating across 3 scripts
- **PR #81** — kill mechanism uses `docker service scale=0` (not `docker kill`) so Patroni actually elects new leader
- **PR #82** — psql executor filter (Spilo containers only) + restore-before-sanity-check ordering
- **PR #83** — node-agnostic Patroni-leader check in kill-rishi-6 (no hardcoded `patroni-rishi-4`)
- **PR #84** — fill-rishi-5-disk SSH-by-IP + drop-sudo + write-under-rishi-deploy-home
- **PR #85** — kill-patroni-leader retries psql write/read during pgbouncer routing-settling window

## MERGED PRs (mine, 2026-05-17 — Day-5 Step 4 Caddy arc)

- **PR #76** — Caddy deploy artifacts (caddy-install.sh + caddyfile.placeholder + stack edits)
- **PR #77** — drop `auto_https off` so `tls internal` provisions certs
- **PR #78** — drop `read_only: true` so `/tmp` is writable for `tls internal` cert temp files
- **PR #79** — pin hostname `placeholder.rishi.local` on the Caddy site block (coordinator-authorized override)

## MERGED PRs (mine, 2026-05-17 — Day-5 Step 3 close-out PRs)

- **PR #71** — `HOSTNAME=0.0.0.0` so Next.js binds to loopback
- **PR #72** — operator-bypass for pre-flight bind-mount verifier (`YRAL_LANGFUSE_SKIP_PREFLIGHT_BIND_MOUNT_VERIFY`)
- **PR #73** — healthcheck probe targets `127.0.0.1` (IPv4/IPv6 resolution mismatch); coordinator-authorized override
- **PR #74** — Day-5 Step 3 close LOG entry + STATE update
- **PR #75** — `node-bootstrap.sh` provisions intra-cluster SSH keypair for rishi-deploy (architectural fix for bug #12 + #13)

## MERGED PRs (mine, 2026-05-16 — Day-5 Step 3 env arc + supporting fixes)

- **PR #61** — `DATABASE_URL` single env var (Langfuse 3 no discrete form)
- **PR #62** — pgbouncer `AUTH_USER` + `AUTH_QUERY` for dynamic lookup
- **PR #63** — pgbouncer `DB_PASSWORD` inline (edoburu ignores `_FILE`)
- **PR #64** — pgbouncer `AUTH_TYPE: scram-sha-256` (PG 15)
- **PR #65** — pgbouncer image bump 1.21.0-p2 → v1.23.1-p3
- **PR #66** — `CLICKHOUSE_MIGRATION_URL` (native port 9000)
- **PR #67** — `CLICKHOUSE_PASSWORD` inline (no `_FILE` variant in Langfuse 3 migration CLI)
- **PR #68** — Prisma `DIRECT_URL` bypass + worker `LANGFUSE_S3_EVENT_UPLOAD_BUCKET` placeholder
- **PR #69** — single-node ClickHouse Keeper + `default` cluster (Langfuse `ON CLUSTER` migrations)
- **PR #70** — Langfuse 3 Next.js inline env vars (`SALT` + `NEXTAUTH_SECRET` + `ENCRYPTION_KEY`)

## MERGED PRs (mine, earlier 2026-05-16 — Day-5 Step 2 close + hardening)

- **PR #51** — `confirm_stack_actually_deployed` post-deploy verifier (admin-merged after Codex truncation FP)
- **PR #52** — auto-merge trigger fix (`workflow_run` instead of `check_suite`)
- **PR #53** — Patroni ETCD3 native code-path migration (`ETCD_HOSTS` → `ETCD3_HOSTS`)
- **PRs #54-#59** — Day-5 Step 2 Redis Sentinel deploy bug arc + close
- **PR #60** — pre-emptive Langfuse install-script hardening (5 patterns ported from Patroni/Sentinel)

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
I'm resuming Session 1. PHASE 0 IS COMPLETE 2026-05-17 — v2 cluster
stateful core operational on rishi-4/5/6 with verified HA semantics.
All 5 Day-5 steps closed: Patroni HA, Redis Sentinel, Langfuse, Caddy
edge-ingress, chaos-test H3 verification (3/4 passing; Test 4
partition deferred to Phase 1+ per coordinator call).

17-bug Day-5 deploy-time arc closed across Steps 3+4 (PRs #61-#73 +
#76-#79). 2 coordinator-override invocations (PR #73 healthcheck IPv4,
PR #79 Caddy SUBJECT pin) both validated the discipline. 6-PR chaos-
test mechanics hardening arc (#80-#85). 25 captured insights for
future v2-template-design work.

Session 1 idle pending Phase 1 green-light. When Phase 1 launches,
queued follow-ups pick up: caddy-swarm-config-generator (real
service routes), deprecate-langfuse-bypass, long-run-stability-check,
keychain-multiline-base64-wrap, network-partition-chaos-test (Phase
1+ design). Ready to continue?
```
