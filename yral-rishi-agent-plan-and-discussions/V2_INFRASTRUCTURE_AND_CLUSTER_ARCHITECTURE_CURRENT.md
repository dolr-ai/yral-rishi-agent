# V2 Infrastructure + Cluster Architecture — CURRENT

**Status:** CURRENT. Supersedes `V2_TEMPLATE_AND_CLUSTER_PLAN_HISTORICAL_2026_04_23.md` (kept for archival context).

**What this doc is:** the forward-useful architecture reference for the `yral-rishi-agent` monorepo's infrastructure + cluster + template. Updated to match current decisions (monorepo locked, sentry.rishi.yral.com, bootstrap at umbrella root, etc.).

**What this doc is NOT:** the big plan (that's `README.md`), the day-by-day phase schedule (`TIMELINE.md`), the hard-rules ledger (`CONSTRAINTS.md`). Those are sibling canonical docs.

**Naming, secrets, no-hardcoded-IPs, explicit-English** rules all live in CONSTRAINTS.md and their respective memories. Not duplicated here.

---

## 1. Hardware — rishi-4/5/6 confirmed specs

Per Saikat's allocation 2026-04-23:

| Thing | Value | Implication |
|---|---|---|
| CPU | Intel Core i7-6700 @ 3.4 GHz (4 cores / 8 threads, 2015 desktop-class) | CPU is the real constraint. 12 physical cores across 3 nodes. LLM-heavy workload mostly I/O-bound on external APIs, so workable — but replica math must be CPU-aware |
| RAM | 62.6 GB per node | ~188 GB cluster total. No RAM pressure expected at Month-6 load |
| OS | Ubuntu 24.04.4 LTS | Same as rishi-1/2/3 |
| Disk | Likely 2× 512 GB NVMe (Hetzner EX61-NVMe class); software RAID1 expected → ~450 GB usable. **Verify via `cat /proc/mdstat` on first SSH** |
| Network | IPv4 allocated per `reference_saikat_server_allocation.md` memory. No IPv6 in any config |
| Datacenter | rishi-1/2/3/4 confirmed Falkenstein (FSN1). rishi-6 likely Nuremberg (NBG1). rishi-5 ambiguous. **Verify day-0 via `hcloud server describe` or `/etc/hetzner-provision`** |
| No ECC memory | Desktop CPU | Bit-flip risk negligible at this scale; mitigated by Patroni sync replication + WAL PITR + offsite backups |

---

## 2. Access model — root → rishi-deploy handoff

**Canonical SSH key:** `~/.ssh/rishi-hetzner-ci-key` (same key as rishi-1/2/3 fleet). Public half goes into `authorized_keys` on every new node.

### Day 0 (Saikat's root window open, ~1 week)

SSH in as root on rishi-4/5/6:

1. Create unix user `rishi-deploy` (uid/gid matching rishi-1/2/3 convention)
2. Copy `rishi-hetzner-ci-key.pub` into `/home/rishi-deploy/.ssh/authorized_keys`
3. Add two personal keys (Rishi's laptop + backup key) to `authorized_keys`
4. Grant `rishi-deploy` narrow sudoers (NOT blanket ALL):
   - `docker` (full — needed for Swarm ops)
   - `systemctl restart|status|reload` for specific units: `caddy`, `docker`, `yral-v2-swarm-resync`
   - `journalctl`
   - `apt update` + `apt upgrade` (for unattended-upgrades cover)
5. Add `rishi-deploy` to `docker` group (most docker ops need no sudo)
6. Disable root password auth; keep root SSH-key auth as emergency break-glass
7. Install base packages: `docker-ce`, `docker-compose-plugin`, `ufw`, `fail2ban`, `unattended-upgrades`, `chrony` (Patroni needs time sync), `htop`, `ncdu`
8. Configure `ufw`:
   - Allow `:22` from known IPs only (Rishi's laptop + Saikat's)
   - Allow `:80/:443` on rishi-4 and rishi-5 ONLY (edge nodes)
   - Allow Swarm ports `2377/7946/4789` between the 3 nodes
   - Default deny
9. Enable `unattended-upgrades` for SECURITY PATCHES ONLY (not full dist-upgrades)

### Day 1-3 (still during root window, but operating as rishi-deploy)

All work happens as `rishi-deploy` via SSH key. Root is NOT used for day-to-day ops.

- Swarm init on rishi-4 (`docker swarm init`), join rishi-5 + rishi-6 as managers
- Apply node labels (see §4)
- Install `yral-v2-swarm-resync.service` systemd unit (see §5.1)
- Stand up Layer-1 stateful core (Patroni + etcd + Redis Sentinel + PgBouncer)

### Day 4+ (Saikat revokes root)

- Day-to-day ops never need root
- If something genuinely needs root (kernel upgrade, UFW rule change, new systemd unit), ping Saikat with a specific request. Cost ~30 min wait. Acceptable.

**Break-glass:** SSH lost (key removed, UFW misconfig) → Saikat has Hetzner web console; can recover. Fault model assumes one box down at a time — Swarm 3-manager quorum tolerates.

---

## 3. DNS + Cloudflare routing — via rishi-1/2 Caddy

Locked per Saikat 2026-04-23 and CONSTRAINTS row C5:

**Cloudflare DNS stays as-is.** `*.rishi.yral.com` keeps pointing to rishi-1 and rishi-2. No new Cloudflare records are created for v2.

To get traffic to rishi-4/5/6: **add reverse-proxy routing in Caddy config on rishi-1/2** for the specific v2 subdomains. rishi-1/2 becomes the reverse-proxy edge for v2 services.

```
User → Cloudflare → rishi-1 Caddy → reverse_proxy → rishi-4 Caddy → service container
                 OR rishi-2 Caddy → reverse_proxy → rishi-5 Caddy → service container
```

### What this means

- **No new DNS records needed.** Every v2 service reachable at `<name>.rishi.yral.com` (not `.v2.rishi.yral.com`)
- **v2 availability coupled to rishi-1/2 Caddy uptime.** Same coupling that yral-chat-ai Python already has
- **Latency cost:** one extra TCP hop rishi-1/2 → rishi-4/5. Same DC = ~1 ms. Negligible even for SSE
- **Cutover (when Rishi approves it):** one Caddy config change on rishi-1/2 — point `chat.yral.com` at rishi-4/5 instead of the current Python chat-ai. Zero Cloudflare touch

### Caddy config management on rishi-1/2

- SSH in as user `deploy` (NOT `rishi-deploy`) using `~/.ssh/rishi-hetzner-ci-key`
- Caddy config snippets live at `/home/deploy/caddy/conf.d/*.caddy` (confirmed 2026-04-23 from SSH audit; see `reference_rishi_1_2_caddy_layout.md`)
- Add one new snippet per v2 service reachable from mobile. Snippet does `reverse_proxy` to rishi-4/5 upstream IPs, but those IPs are injected from GitHub-Secret-backed cluster config (see §4), NEVER hardcoded
- `docker exec caddy caddy validate --config /etc/caddy/Caddyfile` first
- Then `docker exec caddy caddy reload --config /etc/caddy/Caddyfile --force` — zero-downtime reload

### TLS

Every snippet uses `tls internal` (Caddy generates self-signed cert). Cloudflare sits in front in **"Full" mode** (not Strict), accepting self-signed origins. This is the pattern rishi-1/2 already uses for yral-chat-ai / sentry / metabase. V2 rishi-4/5 Caddy does the same `tls internal`; rishi-1/2 Caddy reverse-proxies to it over HTTPS (no cert verification by default).

### Private networking

Public network + TLS for now. Same-DC latency ~1 ms. Revisit at Month 6 if we want private vSwitch for defense-in-depth or egress savings. Not blocking.

---

## 4. Swarm-only networking + cluster-shape config (CONSTRAINTS C3, C6)

### Swarm-only rule

Only port `:443` is exposed to the host — on rishi-4 and rishi-5 via Caddy Swarm-service ingress mode. Nothing else touches host networking.

- **No `ports:` directive** in any compose file except the edge Caddy stack. CI lint (yq) fails the build if any other stack exports a host port.
- **All inter-service traffic is Swarm overlay.** Service A → Service B by Swarm DNS name.
- **Postgres, Redis, Langfuse, Prometheus, Grafana, Loki — all overlay-only.** No published ports. Caddy proxies their admin UIs via subdomain if external access needed (`grafana.rishi.yral.com`, `langfuse.rishi.yral.com`).
- **Three encrypted overlay networks** (`--opt encrypted`):
  - `yral-agent-public-web-overlay` — Caddy ↔ public-facing services
  - `yral-agent-internal-service-to-service-overlay` — service ↔ service
  - `yral-agent-data-plane-overlay` — services ↔ Postgres/Redis/Langfuse
- **UFW host rules** (simpler because of Swarm-only):
  - 22 from known IPs only (Rishi + Saikat)
  - 80/443 on rishi-4 and rishi-5 only
  - Swarm ports 2377/7946/4789 between the 3 nodes
  - Default deny

### Dynamic cluster shape via cluster.hosts.yaml

Live in `bootstrap-scripts-for-the-v2-docker-swarm-cluster/cluster.hosts.yaml` at the monorepo root (NOT inside template; per Rishi's 2026-04-24 decision — CONSTRAINTS F7 updated).

Shape only (no IPs):

```yaml
# cluster.hosts.yaml — shape only, no IPs here. IPs live in GitHub Secrets.
cluster_name: yral-rishi-agent-cluster
datacenter_name: hetzner-falkenstein  # adjust if rishi-6 confirmed Nuremberg

proxy_edge_hosts:  # rishi-1/2 — we SSH in, don't own them
  - host_name: rishi-1
    ipv4_github_secret_name: rishi_1_public_ipv4
    ssh_user: deploy
    ssh_key_reference: hetzner-ci-key
    caddy_snippets_directory: /home/deploy/caddy/conf.d/
  - host_name: rishi-2
    ipv4_github_secret_name: rishi_2_public_ipv4
    ssh_user: deploy
    ssh_key_reference: hetzner-ci-key
    caddy_snippets_directory: /home/deploy/caddy/conf.d/

swarm_hosts:  # rishi-4/5/6 — our new cluster
  - host_name: rishi-4
    ipv4_github_secret_name: rishi_4_public_ipv4
    ssh_user: rishi-deploy
    ssh_key_reference: hetzner-ci-key
    swarm_role: manager
    placement_labels:
      node_role: edge
      state_tier: primary
  - host_name: rishi-5
    ipv4_github_secret_name: rishi_5_public_ipv4
    ssh_user: rishi-deploy
    ssh_key_reference: hetzner-ci-key
    swarm_role: manager
    placement_labels:
      node_role: edge
      observability_tier: primary
  - host_name: rishi-6
    ipv4_github_secret_name: rishi_6_public_ipv4
    ssh_user: rishi-deploy
    ssh_key_reference: hetzner-ci-key
    swarm_role: manager
    placement_labels:
      node_role: compute
      langfuse_tier: primary
```

IPv4 addresses live as GitHub Secrets: `RISHI_1_PUBLIC_IPV4`, `RISHI_2_PUBLIC_IPV4`, `RISHI_4_PUBLIC_IPV4`, `RISHI_5_PUBLIC_IPV4`, `RISHI_6_PUBLIC_IPV4`. Rotation = update secret + redeploy.

### Downstream consumers of cluster.hosts.yaml

None hardcode IPs. All read from rendered config:

| Script | Purpose |
|---|---|
| `scripts/render-cluster-config.py` | Merges shape + secrets at CI time into runtime config |
| `scripts/generate-ssh-config.sh` | Renders `~/.ssh/config` entries so `ssh rishi-4` works |
| `scripts/swarm-init.sh` | Swarm join on each node from shape |
| `scripts/apply-node-labels.sh` | Applies placement_labels |
| `scripts/generate-caddy-snippets.sh` | Renders rishi-1/2 Caddy snippets with IP-from-secret |
| `scripts/generate-prometheus-targets.sh` | Prometheus scrape targets |
| `scripts/sync-uptime-kuma-monitors.py` | Host-name-based Uptime Kuma registration |
| `scripts/bootstrap-new-node.sh` | Add a new node with one command |
| `scripts/drain-and-remove-node.sh` | Remove a node cleanly |
| `backup.sh` | Backup runner target list |

### Enforcement

CI lint rejects any literal IPv4/IPv6 in any file checked into git. Override requires `# allow-literal-ip` comment with justification. PR template asks: "any literal IPs?"

Adding a node = one `cluster.hosts.yaml` entry + one new GitHub Secret + `scripts/bootstrap-new-node.sh`. CI re-renders everything. Zero code churn elsewhere.

---

## 5. Node role layout + replica placement

```
rishi-4 — edge + state primary             rishi-5 — edge mirror + observability        rishi-6 — compute + quorum
(4c/8t, 62 GB, ~450 GB NVMe)              (4c/8t, 62 GB, ~450 GB NVMe)                (4c/8t, 62 GB, ~450 GB NVMe)
─────────────────────────────              ─────────────────────────────────────        ──────────────────────────────
Swarm manager                              Swarm manager                                Swarm manager (3rd for quorum)
Caddy Swarm svc replica (:443 host port)   Caddy Swarm svc replica (:443 host port)     No public ports
Patroni leader (usual)                     Patroni sync replica (FSN1, ~1ms)            Patroni async replica (possibly NBG1)
etcd 1                                     etcd 2                                       etcd 3
PgBouncer                                  PgBouncer                                    —
Redis primary                              Redis replica                                Redis Sentinel (3rd quorum)
WAL-G shipper                              Prometheus + Grafana + Loki + Alloy          Langfuse (web + worker + PG + CH)
Hot-path services (3-replica)              Hot-path services (3-replica)                Hot-path services (3-replica)
Core services (2-replica)                  Core services (2-replica)                    Heavy services (media-gen, skill-runtime)
                                           Uptime Kuma                                  arq worker pool
                                                                                        Background services (scheduler, meta-advisor)
                                                                                        Backup runner
```

### Replica tiers (cluster-wide replica count)

| Tier | Services | Replicas | Where |
|---|---|---|---|
| Hot path | public-api, conversation-turn-orchestrator | 3 | everywhere (all 3 nodes) |
| Core stateful | soul-file-library, user-memory-service, influencer-and-profile-directory, content-safety-and-moderation | 2 | rishi-4 + rishi-5 |
| Supporting | events-and-analytics, creator-studio, payments-and-creator-earnings | 2 | one edge + rishi-6 |
| Heavy | media-generation-and-vault, skill-runtime | 2 | rishi-6 + one edge |
| Background | proactive-message-scheduler, meta-improvement-advisor | 1 | rishi-6 |

### Per-replica resource limits (defaults; overridable in each service's `project.config`)

| Tier | CPU limit | RAM limit |
|---|---|---|
| Hot path | 1.0 | 768 MB |
| Core stateful | 0.5 | 384 MB |
| Supporting | 0.5 | 384 MB |
| Heavy | 1.5 | 1.5 GB |
| Background | 0.25 | 256 MB |

### Patroni topology — handles cross-DC if rishi-6 is Nuremberg

If day-0 verification confirms rishi-6 in NBG1:
- Patroni **leader** on rishi-4 (FSN1)
- Patroni **sync replica** on rishi-5 (FSN1, ~1 ms commit wait)
- Patroni **async replica** on rishi-6 (NBG1, streams WAL continuously, writes don't wait)
- etcd quorum 3 of 3 across both DCs (etcd tolerates 5-8 ms cross-DC)

**Benefit:** writes don't pay cross-DC latency. Disaster resilience across DCs for reads.
**Risk if FSN1 loses power:** rishi-4 + rishi-5 down. rishi-6 async-promotable; <1s typical lag; Patroni handles auto-promote. Acceptable.

### Staging environment

Every service has a staging deploy alongside prod from day 1:

- **Shared expensive infra:** one Patroni cluster, one Redis, one Langfuse — separated by namespace
  - Postgres schemas: prod `agent_*`, staging `staging_agent_*`
  - Redis keys: prod prefix `prod:`, staging prefix `staging:`
  - Langfuse tag: `environment=production` vs `staging`
  - Sentry tag: `environment` distinguishes
- **1 replica per staging service** at 50% prod resource limits
- **DNS:** `*.staging.v2.rishi.yral.com` wildcard alongside prod (both resolve to rishi-4/5 Caddy)
- **Backups:** prod schemas have all three layers; staging reseeded weekly from redacted prod snapshot
- **CI flow:** push to main → auto-deploy to staging → smoke test + latency gate run automatically → **manual "promote to prod"** button in GitHub Actions → canary deploy to prod

### Headroom math

Per node steady state:
- ~2 GB OS/Docker + ~3 GB Patroni + ~3 GB Redis (primary only on r4) + ~1 GB etcd/PgBouncer/Caddy + observability/Langfuse on r5/r6 (~4-8 GB) + prod app replicas (~6-8 GB) + staging replicas (~1.5-2 GB)
- Total ~20-27 GB used out of 62 GB → ~55% RAM headroom
- CPU: staging adds ~1 vCPU per node → ~45% CPU headroom at steady state

Enough headroom for rolling deploys, Month-6 traffic growth, and a full node failure (remaining 2 carry the load).

---

## 6. Cluster management — the bulletproof disciplines

These are the ops hygiene practices that make the cluster not bite at 3 AM. Every one goes into the v2 template or the cluster-bootstrap folder. No service deploys before they're all in place.

### 6.1 Reboot resilience — `yral-v2-swarm-resync.service`

Response to the April 19 incident (`reference_docker_restart_policy_edge_case.md`): `restart: always` failed non-deterministically on both rishi-1 and rishi-2 after the same reboot. Docker's restart policy is not trustworthy on reboot.

**Mitigation:** `yral-v2-swarm-resync.service` — systemd oneshot that runs AFTER `docker.service` is up. Iterates every `.yml` in `/opt/yral-v2/stacks/` and runs `docker stack deploy -c <file> <stackname> --with-registry-auth`. Idempotent. Restores full cluster state regardless of Docker's behavior.

**Verification:** reboot rishi-6 during Phase 0. Every stack must come back within 2 minutes without manual intervention. Phase 0 exit criterion.

### 6.2 Swarm config immutability — SHA-rotating pattern

From `reference_template_haproxy_cfg_bug.md`: every Swarm config (haproxy.cfg, redis.conf, pgbouncer.ini, Patroni overrides, Caddy imports) uses the SHA-suffix pattern:

`name: <stack>_<configname>_<sha8>`

CI computes content SHA, injects into stack YAML, prunes old configs after rollout. Existing template already has this working. Inherit.

### 6.3 Three-layer backup strategy

Per CONSTRAINTS D2 + `feedback_three_layer_backup.md`:

- **L1 HA:** Patroni leader + sync replica on rishi-4/5; async on rishi-6. Sync commit requires ≥1 replica ack. RPO 0 within cluster, RTO <60s on auto-failover.
- **L2 PITR:** WAL-G ships every WAL segment to Hetzner S3 bucket `rishi-yral-wal-archive` near-real-time. RPO ~1 min. Restore to any second in last 7 days.
- **L3 Offsite:** daily `pg_dump` to Hetzner S3 (30-day retention), weekly `pg_dump` to Backblaze B2 (3-month retention, different failure domain), monthly encrypted dump (1-year retention).
- **Verification:** weekly CI job restores yesterday's dump into throwaway Postgres, runs sanity queries, destroys it. Pages on failure. Quarterly manual DR drill.

### 6.4 Secrets handling (CONSTRAINTS D1, D7)

- **GitHub Secrets per-repo** = primary store for per-service secrets (DB DSNs, LLM keys, third-party API keys)
- **Vault (`vault.yral.com`)** = read-only for team-shared lookups already there (e.g., `YRAL_METADATA_NOTIFICATION_API_KEY`). Naitik's territory. We read, we don't write.
- **Declarative manifest** at `bootstrap-scripts-for-the-v2-docker-swarm-cluster/secrets-manifest.yaml` declares every secret every service needs (metadata only; no values). CI gate refuses deploy if required secret missing.
- **Runtime injection** via Swarm secrets, mounted at `/run/secrets/<name>`. Nothing secret in images, nothing in git. Rotation = `gh secret set` → redeploy.
- **Audit:** GitHub Actions logs secret reads per workflow. Vault logs its own. Monthly 15-min skim.

### 6.5 Monitoring + alerting

- **Prometheus** scrapes every service's `/metrics` on the Swarm-internal port. Scrape interval 15s.
- **Alertmanager** rules (start ~10, grow from incidents):
  - Service replica count < desired for >2 min
  - Service container CPU >90% sustained 5 min
  - Service container memory >85% sustained 5 min
  - Patroni leader election in last 5 min
  - Redis replica lag > 5s
  - WAL-G archive failure
  - Backup job failure
  - Disk free < 20%
  - HTTP 5xx rate > 1% over 5 min
  - LLM provider error rate > 5% over 5 min
- **Alerts route to Google Chat webhook** (same pattern current chat-ai uses, already wired; CONSTRAINTS D6)
- **Sentry** (on `sentry.rishi.yral.com` — Rishi's self-hosted on rishi-3 — CONSTRAINTS A7/C4): application errors + performance traces. Tagged `service=<name>, environment=production|staging`. **NEVER `apm.yral.com`** (team-shared; not ours).
- **Langfuse** (self-hosted on rishi-6; CONSTRAINTS D4): captures every LLM call — prompt, response, tokens, latency, cost. Per-turn trace joinable to Sentry + Prometheus via correlation ID
- **Uptime Kuma** at `status.yral.com`: `/health/ready` per service

### 6.6 CI/CD guardrails

- **Gitleaks** scans every push for accidentally-committed secrets; fails build on hit
- **Compose-limits lint:** every service must declare CPU + RAM limits; fails build if missing
- **Trivy image scan:** blocks CRITICAL/HIGH CVEs from merging to main
- **Canary deploy pattern** (inherited from existing template, adapted for monorepo): rishi-4 → health check → rishi-5 → health check → rishi-6. Failure at any step = automatic rollback to last-good image tag. Monorepo adaptation: path-scoped CI triggers only for the changed service.
- **Migration discipline:** DB migrations run BEFORE new app starts (expand-contract). New column/table is additive; code handles both old + new schema for one release; old schema removed in follow-up. **Migrations run against staging schemas first** (auto); staging failures block promote-to-prod.
- **Latency gate** (CONSTRAINTS E1): every PR runs synthetic-load smoke test against staging and compares p50/p95/p99 to `latency-baselines.md`. Regression = block merge.
- **Two-step deploy:** push to main → GitHub Actions auto-deploys staging. Automated smoke + latency gate. Green = enables manual "promote to prod" workflow button. Clicking runs canary above. Red = blocks promotion, opens GitHub issue.
- **Staging reseed:** weekly GitHub Actions job restores latest prod Patroni snapshot into `staging_*` schemas with a redaction SQL script (scrubs PII columns: message bodies, user emails, payment details → dummy data preserving structure + volume).

### 6.7 Chaos testing — Phase 0 exit criterion (CONSTRAINTS H3)

Before any real service runs on the v2 cluster:

- Drain rishi-6 (`docker node update --availability drain`). Verify hot-path replicas reschedule; Patroni leader holds; Redis Sentinel retains quorum.
- Kill rishi-4 Patroni container. Verify failover to rishi-5 within 60s.
- Fill rishi-5's disk to 80%. Verify alerts fire.
- Partition rishi-6 from rishi-4/5 for 10 min. Verify etcd quorum holds (2 of 3).
- Reboot rishi-6. Verify `yral-v2-swarm-resync.service` restores stacks within 2 min.
- Caddy Swarm-service restart. Verify zero dropped requests (routing mesh).
- WAL-G restore drill — restore yesterday's WAL into throwaway Postgres + sanity queries.
- Backblaze offsite restore drill — full restore from weekly backup to fresh server.
- Feature-flag kill-switch — flip `SERVICE_DISABLED=true`; verify 503 + graceful message.
- Prompt injection defense — submit 20 known injection payloads; verify ≥18 blocked.

Any failure → fix BEFORE proceeding. This is the price of bulletproof.

---

## 7. V2 template spec

**Key change from historical draft (2026-04-24):** we're monorepo now (CONSTRAINTS F16). The v2 template's `new-service.sh` creates a **subfolder in the monorepo**, NOT a new GitHub repo. Same 1-command UX, different git target.

### 7.1 Inherited from existing `yral-rishi-hetzner-infra-template` (keep as-is)

| Capability | Source | Why keep |
|---|---|---|
| 1-command spawn | `scripts/new-service.sh` (457 lines, adapted for monorepo subfolders) | Proven; ADHD-friendly |
| project.config single source of truth per service | `project.config` | Clean config/code separation |
| Canary deploy + auto-rollback | `.github/workflows/deploy.yml` | Matches latency-never-regresses rule |
| Caddy per-service snippets | `caddy/snippet.caddy.template` | Scales cleanly to 13+ services |
| Patroni HA PG + etcd | `patroni/`, `etcd/` | Same pattern on fresh cluster |
| Documentation standards | 5 required docs (DEEP-DIVE, READING-ORDER, CLAUDE, RUNBOOK, SECURITY) per service | CONSTRAINTS F8 |
| S3 backup + restore workflow | `.github/workflows/backup.yml`, `backup/backup.sh` | Project-isolation guard via schema prefix |
| Gitleaks + Trivy in CI | `.github/workflows/deploy.yml` | Already works |
| SHA-rotating Swarm configs | `haproxy/stack.yml` pattern | Proven fix for April 20 bug |
| Secrets pattern | `infra.get_secret()` + GitHub Secrets + Swarm secrets | CONSTRAINTS D1 |
| `strip-database.sh` | `scripts/strip-database.sh` | For genuinely stateless services |

### 7.2 Existing-template pain points FIXED in v2

| Pain | Fix in v2 template |
|---|---|
| No resource limits on Patroni/etcd/HAProxy/app | Default limits on every container; CI lint rejects compose without them |
| `pg_hba.conf` too permissive | Lock to data-plane overlay subnet only |
| UFW inactive | Day-0 bootstrap enables UFW with explicit allow-list |
| No PgBouncer | PgBouncer in front of Patroni from day 0 |
| Docker volumes for Patroni data | Bind-mount to `/data/patroni` (survives `docker system prune`) |
| No rate limiting | FastAPI per-user rate-limit middleware using Redis (no custom Caddy build) |
| No staging environment | `*.staging.v2.rishi.yral.com` wildcard; every service deploys to staging automatically |
| Restart-always unreliable on reboot | `yral-v2-swarm-resync.service` systemd unit |
| No per-service tests enforced | Template ships skeleton pytest + CI requires one passing test before deploy |
| No graceful shutdown | FastAPI lifespan hooks + SIGTERM drain |
| No Prometheus/Grafana/Loki | First-class observability stack on rishi-5 |
| Per-service Patroni too heavy | **ONE shared Patroni cluster with schema-per-service** (CONSTRAINTS F3 locked). All 13 services degrade on cluster outage — mitigated by HA + WAL PITR + offsite + chaos tests |
| No connection cap per-tenant | `ALTER ROLE ... CONNECTION LIMIT 20; statement_timeout = '30s'; idle_in_transaction_session_timeout = '60s'` at tenant bootstrap |
| No circuit breaker for external APIs | `tenacity` + `pybreaker` baked into LLM client + HTTP client wrappers |
| Caddy on-host | **Caddy as Swarm service** on rishi-4/5 per §4; only :443 exposed via Swarm ingress; TLS `internal` + Cloudflare-fronted |

### 7.3 Net-new capabilities in v2 template

| Capability | What it is | Why needed |
|---|---|---|
| `SERVICE_PROFILE` = api / worker / cron / streaming | Four-way branch in `new-service.sh`; different compose shape per profile. Streaming profile adds sticky-session Caddy, 60s graceful shutdown, stream-count health checks | v2 has workers + cron + SSE/WebSocket as first-class patterns |
| Blessed worker lib: **arq** | Async Redis queue, fits FastAPI/asyncio | Avoids Celery complexity |
| Redis client baked in | `app/redis_client.py` — one import, Sentinel-aware | 13 services all need it |
| Redis Streams helpers | `emit_event(...)`, consumer-group subscribe | Cross-service event bus |
| Langfuse middleware | Auto-traces every LLM call | Per-turn observability critical for "1000× better" |
| LLM-client abstraction | `llm_client.chat(messages, model=...)` wraps Gemini/Claude/OpenRouter/self-hosted | CONSTRAINTS E3 — LLM-agnostic |
| Feature-flag client | Postgres-table, ~200 LOC, polled every 30s, on/off + % rollout | Every new feature ships behind a flag |
| Uniform `/health` three-tier | `/health/live` + `/health/ready` + `/health/deep` | Clean semantics for Swarm + Uptime Kuma + synthetic user |
| Structured JSON logs + correlation IDs | One middleware; request-id propagates | Debugging any turn = one log search |
| Prometheus `/metrics` | `prometheus-fastapi-instrumentator` | Built-in dashboards |
| Pre-flight deploy check | Before first deploy: secrets set? Sentry DSN valid? Postgres reachable? migrations idempotent? | Prevents broken first deploys |
| MCP tool-runtime helper | Anthropic's `mcp` Python SDK + correlation-ID + Langfuse trace wrapper | Uniform tool-calling |
| Safety filter middleware | Optional pre/post request filters | Every user-facing service can enable |
| Graceful shutdown | FastAPI lifespan + SIGTERM drain | Zero-dropped-request rolling deploys |
| Circuit breakers | `pybreaker` wrappers on LLM + external HTTP | Failing upstream doesn't cascade |
| Retry w/ jittered backoff | `tenacity` on transient failures | Handles network blips without ops noise |
| Idempotency key support (default ON) | Middleware enforces `X-Idempotency-Key` on non-GET; Redis 24h TTL dedup | Mobile retries never duplicate |
| `services.yaml` auto-register | Lives in `bootstrap-scripts-for-the-v2-docker-swarm-cluster/services.yaml` at monorepo root. `new-service.sh` final step updates it. Merge triggers regen of Prometheus scrape, Caddy snippets, Uptime Kuma monitors, Grafana folders | Monorepo-adapted: was "PR to separate repo"; now just edit the file |
| Schema-per-service bootstrap | Tenant SQL template creates schema + role + GRANTs + connection cap | 13 services on one Patroni cluster, cleanly |
| pgvector ready day 1 | Migration adds `CREATE EXTENSION IF NOT EXISTS vector` once per cluster. Migration path to Qdrant kept behind same interface; trigger at ~50M vectors (Month 12+ projection) | user-memory-service needs it day 1 |
| WAL-G restore drill | Weekly CI job restores yesterday's WAL into throwaway Postgres | Backups not restored aren't backups |
| Latency-baseline enforcement | Smoke-test in CI compares to `latency-baselines.md` | CONSTRAINTS E1 |
| Staging auto-deploy | Every push to main → staging at `<svc>.staging.v2.rishi.yral.com` → smoke + latency gate → manual "promote to prod" button | Catch regressions before users see them |
| Eval harness baked in | `evals/` folder per LLM-touching service, CI runs evals on every PR, posts diff | CONSTRAINTS E-series + Langfuse built-in |
| Shadow traffic middleware | Mirror requests to candidate handler; compare responses in Langfuse | Critical for orchestrator refactors |
| Per-turn cost tracking | LLM-client wraps every provider call; logs `{user_id, influencer_id, model, tokens, cost_usd}` to Redis Stream → analytics rollup | Unit economics at 1M msg/day |
| Runaway-protection cap (NOT unit-economics) | Per-user daily ceiling (very high, e.g. ₹500) — triggers switch to cheapest model + Sentry warn. Normal users never hit | CONSTRAINTS E4 — no cost control, just runaway protection |
| PII-aware log redaction | Structured logger with allowlist (status, latency, request_id, correlation_id, service, duration_ms). Everything else redacted or hashed | Chat contents NEVER in Loki/Sentry/Langfuse payloads |
| Prompt injection defense middleware | Lightweight classifier (local + Gemini 2.0 Flash fallback) before orchestrator composes Soul File | Defense at the gate |
| Three-tier health endpoints | `/health/live` (process alive) + `/health/ready` (deps healthy) + `/health/deep` (real round-trip; expensive) | Clean semantics |
| Synthetic user heartbeat | Canary bot sends test message every 5 min via real API + auth. Langfuse tags `synthetic=true`. Alerts on latency/eval regressions | Silent regression early warning |
| Feature-flag kill-switch per service | `SERVICE_DISABLED` flag returns 503 immediately with graceful message | Emergency lever |
| Worker DLQ | arq retries 3× jittered; 3rd failure → DLQ stream `worker.dlq`. Grafana panel tracks depth; alert on >100 or >1h old | No silent data loss |
| Schema migration safety net | Every migration PR auto-runs against WAL-restored yesterday-prod snapshot + full test suite | Catches prod-killing migrations before merge |
| Per-user/per-influencer rate limiting | Middleware aware of (user_id, influencer_id, subscription_tier). Counters in Redis. Reads billing schema for active subs | Matches product model (₹9/24h per-bot unlock) |
| Soul File composition cache | Composed Soul File cached in Redis by `(influencer_id, user_segment_id, model, version)` with TTL 1h or invalidate on layer update | ~15ms/turn saved at 1M msg/day |
| JWKS-based JWT validation w/ dual-shadow rollout | Auth middleware ships JWKS resolver + strict-sig flag defaulting OFF. Dual-validate in shadow; flip ON after 7 days of <0.01% divergence | CONSTRAINTS E9 |

---

## 8. How a new service gets built (monorepo workflow)

Once the v2 template exists:

1. Rishi picks the next service per priority order (CONSTRAINTS F15).
2. `bash yral-rishi-agent-new-service-template/scripts/new-service.sh --name yral-rishi-agent-<purpose> --profile api --tier core-stateful`
3. Script does (~5 minutes):
   - Validates name ≤63 chars (Swarm limit); must start with `yral-rishi-agent-`
   - Creates new subfolder `<service-name>/` at monorepo root with template scaffolding copied in
   - Renames identifiers in the service's `project.config`
   - Generates Postgres password + Redis key for this service
   - Sets GitHub repo secrets for this service (LLM keys, DB DSN, S3 creds, Sentry DSN, Langfuse keys) via `gh secret set` — scoped to the monorepo repo
   - Opens PR against `bootstrap-scripts-for-the-v2-docker-swarm-cluster/services.yaml` adding the new entry (one commit)
   - Watches first CI run; verifies `/health/ready` responds
   - Registers in Uptime Kuma via API
   - Prints the service's staging URL and next steps
4. Merge the services.yaml PR → Prometheus scrape target regenerated, Caddy snippet regenerated, Grafana folder auto-created, Uptime Kuma monitor added.
5. Write business logic. Every PR: CI lint + tests + smoke test + latency gate. Failing any = block merge.
6. Release: `gh workflow run promote.yml --field service=<name>`. Canary pattern runs: rishi-4 first, then rishi-5, then rishi-6.
7. Post-merge 15-min retrospective per `feedback_template_first_build.md`: what did we copy-paste? what broke twice? what docs gap did we hit? Fix in the template; version bump.

---

## 9. "1000× better" → template features map

| Product goal | Template feature that enables it |
|---|---|
| First token <200 ms | SSE middleware + streaming LLM client + Langfuse time-to-first-token metric; CI latency gate holds invariant |
| Remembers forever | pgvector-ready bootstrap; user-memory-service built on template with zero infra setup |
| Sounds human | Layered Soul File client + cache; feature flags for A/B prompt variants |
| Has initiative | arq worker profile + Redis Streams triggers; proactive-scheduler builds on these |
| Multi-modal | S3 helper + media-generation profile gets 2× memory allocation by default |
| Actually does things | MCP tool-runtime client; safety middleware wraps every tool call |
| Bots feel different | LLM-client abstraction routes different bots to different models by config |
| Safe at the edges | Safety filter middleware + rate-limit + crisis-detection + prompt injection defense |
| Operationally boring | Uniform /health, metrics, logs, traces, alerts; one dashboard shape per service; one runbook template |

---

## 10. Genuinely-open items before Phase 0 kicks off

Resolved items cleaned out. The residual opens:

1. **Disk layout** (RAID0 vs RAID1) — verify on first SSH via `cat /proc/mdstat`. RAID1 preferred. RAID0 is acceptable risk given our 3-layer backup. Note in RUNBOOK.md if RAID0.
2. **Datacenter for rishi-5 and rishi-6** — verify via `hcloud server describe` or Hetzner web. If rishi-6 confirms NBG1, Patroni topology is the cross-DC variant (§5).
3. **rishi-1/2 Caddy SLA** — worth asking Saikat: what's the SLA, who gets paged, what maintenance windows? Our v2 availability is coupled. Not blocking; document as shared dependency in RUNBOOK.md.
4. **Sentry project self-service permission** on `sentry.rishi.yral.com` — confirm Rishi owns admin access to self-create 13 Sentry projects. Rishi owns the box, so likely yes.
5. **Backup offsite provider** — Backblaze B2 is the default choice; Cloudflare R2 and AWS S3 Glacier are alternatives. Rishi has deferred; resolve at Phase 9 planning.

---

## 11. Related canonical docs

- `README.md` — big product plan (vision, capabilities, roadmap)
- `CONSTRAINTS.md` — every hard rule (70 rows across 9 categories)
- `TIMELINE.md` — day-by-day phases with Rishi-on-Motorola checkpoints
- `V2_TEMPLATE_AND_CLUSTER_PLAN_HISTORICAL_2026_04_23.md` — archive of the original plan doc
- `live-chat-ai-feature-audit-v2-must-preserve-everything-we-found-here/yral-chat-ai-python-complete-endpoint-and-behavior-inventory.md` — the feature parity audit (everything v2 must preserve)
- `running-coordination-asks-plus-mobile-team-memo-and-change-log/` — what we need from Saikat + Sarvesh + Shivam
