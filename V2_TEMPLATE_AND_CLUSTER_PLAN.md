# Plan: V2 template + rishi-4/5/6 cluster — bulletproof, 1000× chat-ai

> Status: **DRAFT FOR REFINEMENT.** Nothing gets implemented until Rishi gives explicit "start building v2 template" approval. This doc is the shared artifact we refine together, turn by turn.

---

## 1. Context (short)

- V2 greenfield plan (`i-spoke-to-chatgpt-eager-squirrel.md`) is approved. 13 services, Docker Swarm, template-first. This plan is the **template + cluster-architecture layer** underneath it.
- Saikat is allocating 3 new servers (to become rishi-4/5/6). Spec confirmed today — see §2.
- **Existing `yral-rishi-hetzner-infra-template` is NEVER modified** (per memory + no-delete covenant). We build a **new** template — call it `yral-rishi-chat-ai-v2-new-service-template` — that *forks* from the existing one and adds v2 learnings.
- Goal: every v2 service spawned from the new template is **airtight** (no single failure hurts another service, no silent errors, rollbacks automatic, observability uniform) and the product is **1000× better** than current Python chat-ai on the dimensions Rishi cares about (see §7).

---

## 1.5 🚨 NON-NEGOTIABLE: plain-English naming, everywhere (reinforced 2026-04-23)

Rishi is a non-programmer with ADHD. He must be able to read any name in this codebase and understand what it does **without asking anyone**. This rule trumps brevity, cleverness, convention, or conciseness. It applies to every name in every file you will ever create.

### Where the rule applies (not exhaustive — when in doubt, prefer English)

- Directory names, file names, module names
- Python variables, functions, classes, methods, parameters — including loop variables when they're non-trivial
- Database table names, column names, schema names, index names, constraint names
- Environment variable names
- Docker Swarm stack names, service names, secret names, config names, network names, volume names
- GitHub repo names, branch names, commit message prefixes, PR titles, issue titles
- Docker image tags and registry paths
- GitHub Actions job names, step names, workflow file names
- Caddy snippet file names, Prometheus metric names, Grafana dashboard + panel names, Langfuse trace names, Sentry project names, Uptime Kuma monitor names
- Alert rule names, runbook titles, terraform resource names (if ever used)

### The rule

**A name must read as English. A non-programmer should guess the purpose correctly on first read.** Verbose > clever. Explicit > implicit. "Long and obvious" beats "short and cryptic" every time.

### Concrete examples (applies uniformly across the whole template and every v2 service)

| ❌ Bad | ✅ Good | Why bad is bad |
|---|---|---|
| `usr_auth_mw` | `user_authentication_middleware` | `usr` and `mw` aren't English |
| `fetch_sf` | `fetch_soul_file_for_influencer` | `sf` is jargon |
| `get_conv` | `get_conversation_by_id` | `conv` is ambiguous |
| `svc_a` | `public_api_service` | role name beats letter label |
| `CLUSTER_1` | `rishi_v2_three_node_swarm_cluster` | numbered labels hide the content |
| `do_it()` | `send_message_to_llm_and_stream_response()` | verb + object + qualifier |
| table `msgs` | table `conversation_messages` | table should name its content |
| column `ts` | column `created_at_timestamp_utc` | `ts` is not obvious |
| env var `KEY` | env var `GEMINI_API_KEY` | generic name tells you nothing |
| stack `chat-v2` | stack `yral-rishi-chat-ai-v2-public-api` | full name, already locked in |
| log field `lat` | log field `request_latency_milliseconds` | latency in what unit? |
| function `proc()` | function `process_incoming_chat_message()` | what does "proc" mean? |

### Allowed abbreviations (universal, well-known, unambiguous)

`id`, `url`, `api`, `http`, `json`, `sql`, `utc`, `tls`, `dns`, `ssl`, `css`, `html`, `uuid`, `ip`. Nothing else. If in doubt, spell it out. `db` is not on the list — use `database`. `cfg` is not on the list — use `config`. `svc` is not on the list — use `service`.

### Enforcement — template-level, CI-enforced from day 1

This is the single most important thing the template ships with. Bake it in deep:

1. **Python lint** (via ruff + flake8 + custom hooks in the template's `.github/workflows/deploy.yml`):
   - Reject any function, class, or top-level variable whose name is <4 chars or matches a block-list of common abbreviations (`mw`, `cfg`, `svc`, `req`, `res`, `ctx`, `usr`, `conv`, `msg`, `conf`, `proc`, `tmp`, `obj`, `val`, `var`, `num`, `cnt`, `idx`, ...).
   - Block-list is overridable with a `# noqa: naming` comment only when there's a strong reason (loop index `i` in a tight numeric loop, etc.). Every override is reviewed.
2. **SQL lint** in CI: every new table name must be ≥2 English words OR on a tiny allow-list (`users`, `conversations`, `messages`, `influencers`). Column names must be ≥1 full word — no `ts`, `dt`, `amt`, `qty`, etc.
3. **Environment variable lint**: every env var must have ≥2 words separated by `_`. (`PORT` → `SERVICE_PORT`; `KEY` → `GEMINI_API_KEY`.)
4. **Docker/Swarm name lint**: service + stack + secret names must match `yral-rishi-chat-ai-v2-<purpose-in-english-with-hyphens>` regex.
5. **File/directory name lint**: kebab-case, lowercase, English, ≥2 words for top-level dirs.
6. **Pull-request template** asks: "Do all new names in this PR read as English to a non-programmer?" Reviewer must tick yes.
7. **Documentation**: the template's `CLAUDE.md` opens with this rule in the first paragraph. Every service spawned from the template inherits that `CLAUDE.md`. Future engineers (and future-you) read it first.

### What this costs

A small amount of typing. One more lint step in CI (~5 seconds). Occasional friction when someone wants to write `db_conn` and has to write `database_connection`. That's it. The payoff is that Rishi (and any future non-programmer reviewer) can read any file and actually understand it.

**This rule is not subject to refinement. It's a constraint all other decisions operate under.**

---

## 1.7 🚨 NON-NEGOTIABLE: Swarm-only networking — no host ports (added 2026-04-23, per Saikat)

Saikat's directive: **manage everything through Swarm and containers. Do not expose any ports on the host except the single public HTTPS ingress.** No service writes to `/etc/`, no service binds to a host port, no service reaches another service by "host IP + port." Every inter-service path is a Swarm overlay network + service name.

### What this changes from my earlier draft

Earlier I assumed Caddy lives **on-host** on rishi-4/5 (same pattern as the existing template). That flips. **Caddy on rishi-4/5 becomes a Swarm service** deployed with `mode: replicated, replicas: 2` + `placement constraint node_role == edge`. The *only* port exposed to the host on the whole new cluster is **443 on the edge nodes**, owned by the Caddy Swarm service via ingress mode. Nothing else touches the host network.

### Concrete rules for the template

1. **No `ports:` directive in any compose file except the edge Caddy stack.** Reviewed in CI lint — `yq` check fails the build if any other stack exports a host port.
2. **All inter-service traffic is overlay.** Service A talks to service B by DNS name `yral-rishi-chat-ai-v2-service-b` (Swarm adds it to the overlay DNS).
3. **Postgres, Redis, Langfuse, Prometheus, Grafana, Loki — all reachable only on overlay.** No port published. Caddy (the only edge) proxies their UIs via a subdomain if external access is needed (grafana.rishi.yral.com, langfuse.rishi.yral.com).
4. **Three distinct overlay networks** (per §5): `yral-v2-public-web` (Caddy ↔ public-facing services), `yral-v2-internal` (service ↔ service), `yral-v2-data-plane` (services ↔ Postgres/Redis/Langfuse). A compromised business-logic service cannot directly reach Postgres without going through PgBouncer's overlay VIP.
5. **UFW rules simplify dramatically.** Host firewall only needs: 443 on edge nodes (rishi-4/5), 22 from bastion/laptop IPs, Swarm ports 2377/7946/4789 between the 3 nodes. That's it. No per-service port-opening churn.
6. **rishi-1/2 Caddy forwards to the Swarm ingress** on rishi-4 or rishi-5 at port 443. Swarm's routing mesh takes it from there — any node can receive the traffic and forward to the right replica. No IP-to-port mapping to maintain in rishi-1/2 snippets beyond "upstream = rishi-4-ipv4:443 rishi-5-ipv4:443".

### Why this is a win

- **Smaller attack surface.** One public port (443). Nothing else exists outside Swarm.
- **Rolling updates trivial.** Swarm ingress mesh handles them. No port conflicts when old + new replicas coexist.
- **No "where does this service run" questions.** Swarm DNS answers that at runtime.
- **Easier node churn.** Add a node → Swarm ingress picks it up. Remove a node → ingress drops it. No port config anywhere cares.
- **Cleaner security model.** Overlay traffic is encrypted (we enable `--opt encrypted` on all three networks). Nothing travels in cleartext.

### Cost

- Caddy on-host was simpler for ACME TLS ("just a service"); as a Swarm service, the ACME certs need a persistent volume pinned to one node (or use DNS-01 challenge against Cloudflare API — cleanest). One extra thing to wire up.
- Swarm routing-mesh adds one IPVS hop (~0.2 ms). Negligible.

---

## 1.6 🚨 NON-NEGOTIABLE: dynamic cluster topology — no hardcoded IPs, ever (added 2026-04-23)

Rishi's rule: nowhere in the template, any service, any Caddy snippet, any CI workflow, any Prometheus config, any alert, any SSH command, any script may a literal IP address appear. Every IP comes from **one config file + secrets**, so tomorrow if Saikat moves us to different servers or adds rishi-7, we change one file, not 200.

### How the pattern works

Single file `cluster.hosts.yaml` in `yral-rishi-chat-ai-v2-cluster-bootstrap` describes the *shape* (host names, roles, Swarm roles, placement labels, which SSH user, which key). **Actual IPv4 values live only in GitHub Secrets**, never in git. A render step at deploy time (`scripts/render-cluster-config.py`) merges the two into a runtime config that every other script reads.

```yaml
# cluster.hosts.yaml (shape only — lives in git, no IPs here)
cluster_name: yral-rishi-chat-ai-v2-cluster
datacenter_name: hetzner-falkenstein

proxy_edge_hosts:                       # rishi-1/2 — we don't own them, we SSH in
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

swarm_hosts:                            # rishi-4/5/6 — our new cluster
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

**Actual IPv4 addresses live as GitHub Secrets** (`RISHI_1_PUBLIC_IPV4`, `RISHI_2_PUBLIC_IPV4`, `RISHI_4_PUBLIC_IPV4`, `RISHI_5_PUBLIC_IPV4`, `RISHI_6_PUBLIC_IPV4`). Rotation = update one secret + redeploy.

### Everything downstream reads from this file

Never from a hardcoded IP, never from a host-specific file. Concrete list of downstream consumers:

| What | How it consumes the config |
|---|---|
| SSH config generation | `scripts/generate-ssh-config.sh` renders `~/.ssh/config` entries so `ssh rishi-4` just works |
| Swarm node join commands | `scripts/swarm-init.sh` reads `swarm_hosts` list; runs `docker swarm join` on each |
| Swarm node label application | `scripts/apply-node-labels.sh` reads `placement_labels` |
| Caddy snippet generation on rishi-1/2 | `scripts/generate-caddy-snippets.sh` reads `swarm_hosts` → renders `reverse_proxy ${RISHI_4_PUBLIC_IPV4}:443 ${RISHI_5_PUBLIC_IPV4}:443` with the secret values at CI time, SCPs into `caddy_snippets_directory` |
| Prometheus scrape targets | `scripts/generate-prometheus-targets.sh` emits `prometheus-targets.json` |
| Uptime Kuma monitors | `scripts/sync-uptime-kuma-monitors.py` — host-name-based, not IP |
| Systemd `yral-v2-swarm-resync.service` | Reads a local `/etc/yral-v2/cluster-config.json` written at provision time |
| Backup runner target list | `backup.sh` reads the list of swarm hosts |
| CI deploy workflow matrix | `matrix.host` is generated from `swarm_hosts[*].host_name` |

### Adding / removing / moving a node

The most important affordance: we can change hardware without touching any service code.

- **Saikat moves us to different IPs:** update the 5 GitHub Secrets. Re-run `render + sync`. Done. Zero code change.
- **Saikat gives us rishi-7:** add one entry to `cluster.hosts.yaml`. Add `RISHI_7_PUBLIC_IPV4` secret. Run `scripts/bootstrap-new-node.sh rishi-7`. CI re-renders everything.
- **Need to remove a node:** remove the entry. Run `scripts/drain-and-remove-node.sh`. CI re-renders.
- **Need to move the entire cluster to a different datacenter:** change `datacenter_name` + IPs. Redeploy stacks. Everything else picks up the new layout.

### Enforcement

- **CI lint rejects any literal IPv4 or IPv6 address in any file checked into git** (block-list regex in the pre-commit + CI steps). Override requires `# allow-literal-ip` comment with justification (e.g., external endpoint we call — but even those should be hostnames not IPs where possible).
- **PR template** asks: "Any literal IPs in this PR?"
- **All new services spawned from the template inherit this rule via CI config.**

This pattern costs ~1 day of template work and buys us permanent flexibility. It also makes "I've been moved to a new box, set up my Hetzner server" a 5-minute operation instead of a grep-and-replace nightmare.

---

## 2. Hardware reality — adjust the plan accordingly

Saikat's actual spec per node (×3):

| Thing | Value | Implication |
|---|---|---|
| CPU | Intel Core i7-6700 @ 3.4 GHz (4 cores / 8 threads, 2015 desktop-class) | **CPU is the real constraint**, not RAM. 12 physical cores across cluster. LLM-heavy workloads are mostly I/O-bound on external API calls, so this is workable — but replica math must be CPU-aware. |
| RAM | 62.6 GB | Very generous (~188 GB cluster total). No RAM pressure expected at Month-6 load. |
| OS | Ubuntu 24.04.4 LTS | Same as rishi-1/2/3. Known good. |
| Likely disk | 2× 512 GB NVMe (Hetzner EX61-NVMe class) | Assume software RAID1 → ~450 GB usable per box. Confirm with Saikat. |
| Network | IPv6 shown (`2a01:4f8:172:349a::2`); IPv4 to confirm | Need IPv4 addresses from Saikat for Cloudflare. If IPv6-only origins, Cloudflare can bridge — works either way, but we need the actual IPs. |
| No ECC memory | desktop CPU | Bit-flip risk exists but is negligible at this scale. Mitigated by Patroni sync replication + WAL PITR + offsite backups (which we're doing anyway). Just worth knowing. |

**This is not a weak cluster for our workload.** Chat-ai hot path spends most of its time waiting on Gemini. 12 cores is plenty for the orchestration layer plus 13 services plus observability plus Langfuse, as long as we don't pack replicas blindly.

---

## 3. Access model — root → rishi-deploy handoff (day 0–3)

Saikat gives time-limited root SSH. After that, we operate as `rishi-deploy`. Plan:

**Access key (locked in 2026-04-23):** `~/.ssh/rishi-hetzner-ci-key` — same key used for rishi-1/2/3 today. One key for the whole fleet. Public half goes into `authorized_keys` on every new node.

**Day 0 (while Saikat's root window is open — confirmed ~1 week):**
- SSH in as root on all 3 boxes.
- Create unix user `rishi-deploy` with uid/gid matching the existing rishi-1/2/3 convention.
- Copy `~/.ssh/rishi-hetzner-ci-key.pub` (the CI key we already use on rishi-1/2/3) into `/home/rishi-deploy/.ssh/authorized_keys`.
- Add two personal keys: Rishi's laptop + a backup key.
- `rishi-deploy` gets a **narrow** sudoers rule, not blanket `ALL`. Only:
  - `docker` (full — needed for Swarm ops)
  - `systemctl restart|status|reload` for specific units (caddy, docker, our resync service)
  - `journalctl`
  - `apt update` + `apt upgrade` (for unattended-upgrades cover)
- Add `rishi-deploy` to the `docker` group (so most docker ops need no sudo at all).
- Disable root password auth; keep root key auth as emergency break-glass.
- Install: `docker-ce`, `docker-compose-plugin`, `ufw`, `fail2ban`, `unattended-upgrades`, `chrony` (time sync — Patroni cares), `htop`, `ncdu`.
- Configure `ufw`: allow `:22` from known IPs only, allow `:80/:443` on rishi-4 and rishi-5 only, allow Swarm ports `2377/7946/4789` on the private subnet (see §4). Default deny.
- Enable `unattended-upgrades` for security patches only (not full dist-upgrades).

**Day 1–3 (still during root window, but operating as rishi-deploy):**
- All work happens as `rishi-deploy` via SSH key. Root is not used.
- Swarm init on rishi-4 (`docker swarm init`), join rishi-5 and rishi-6 as managers.
- Apply node labels.
- Install the `yral-v2-swarm-resync.service` systemd unit (see §6).
- Stand up L1 stateful core (Patroni + etcd + Redis + PgBouncer + HAProxy).

**Day 4+ (Saikat revokes root):**
- We never need root for day-to-day ops. All of the above commands run under the sudoers allowlist.
- If something requires root (rare — e.g., kernel upgrade, UFW rule change, creating a new systemd unit), we ping Saikat with a specific request. Cost: ~30 min of waiting. Acceptable.

**Break-glass plan:** if SSH access to a box is lost (key removed by accident, UFW misconfig), Saikat has Hetzner console access and can recover. Our fault model assumes one box at a time can go down — cluster has 3-manager Swarm quorum, so losing one is fine.

---

## 4. DNS / Cloudflare — routing via rishi-1/2 Caddy (updated 2026-04-23 per Saikat)

**Saikat's decision (confirmed 2026-04-23):** Cloudflare DNS stays as-is. `*.rishi.yral.com` keeps pointing to rishi-1 and rishi-2 only. To get traffic to rishi-4/5/6, we **add routing rules in the Caddy config on rishi-1/2** that forward specific subdomains to rishi-4/5 over the network. We do not add any new Cloudflare records.

**How this works in plain terms:**

Cloudflare keeps saying "anyone asking for `*.rishi.yral.com` — send them to rishi-1 or rishi-2." Saikat doesn't change that. When a request for, say, `chat-ai-v2.rishi.yral.com` lands on rishi-1, Caddy on rishi-1 looks at the config and sees "for this specific subdomain, don't serve locally — forward the whole request to rishi-4 port 443." rishi-1 becomes a **reverse proxy** for our v2 subdomains.

```
User → Cloudflare → rishi-1 Caddy → (forwards) → rishi-4 Caddy → service container
                 OR rishi-2 Caddy → (forwards) → rishi-5 Caddy → service container
```

**What this means for us:**

- **No new DNS records needed.** Every v2 service is reachable at `<name>.rishi.yral.com` (not `.v2.rishi.yral.com` — we're under the existing wildcard).
- **Cutover at end of Phase 4** is one Caddy config change on rishi-1/2: point `chat.yral.com` at rishi-4/5 instead of the current Python chat-ai. No Cloudflare touch at all.
- **rishi-1/2 Caddy becomes our edge.** Our own Caddy on rishi-4/5 still exists but only serves the routing-mesh hop from rishi-1/2 Caddy to the right app container.
- **v2 availability is coupled to rishi-1/2 Caddy uptime.** If rishi-1/2 has an outage, so does v2. Acceptable for now — it's the same coupling the current Python chat-ai already has.
- **Latency cost:** one extra TCP hop from rishi-1/2 to rishi-4/5. Same datacenter = ~1 ms. Negligible for non-WebSocket traffic. For SSE streaming, the overhead is per-connection-open (~1 ms), not per-token.

**Caddy config on rishi-1/2 (confirmed 2026-04-23):**
- SSH in as user `deploy` on rishi-1/2 using `~/.ssh/rishi-hetzner-ci-key`.
- Caddy config snippets already live on these boxes (the existing chat-ai deploy does this). Filesystem location to be confirmed on first SSH — likely `/home/deploy/caddy/conf.d/*.caddy` (matches existing template pattern).
- We add a new snippet per v2 service. Each snippet does `reverse_proxy` to rishi-4/5 upstream IPs — but those IPs are injected from the GitHub-Secret-backed cluster config (§1.6), never hardcoded in the snippet file.
- `caddy reload` after drop-in. Zero-downtime reload is standard Caddy behaviour.

**Private networking decision (my call, per Rishi 2026-04-23): public network + TLS for now; revisit at Month 6 if needed.**
- Caddy on rishi-1 forwards to rishi-4's public IP over HTTPS on port 443. Traffic is TLS-encrypted end-to-end.
- Same-datacenter (Falkenstein) = ~1 ms latency hop. Negligible.
- Avoids Saikat-provisioning-a-vSwitch dependency; starts faster.
- If at Month 6 we want private L2 (vSwitch) for defense-in-depth or to save egress, we can add it without changing any application code (only `cluster.hosts.yaml` + the CI caddy-snippet-generator output).

**Still needed from Saikat:**
1. **IPv4 addresses of rishi-4/5/6** (user confirmed IPv4-only; no IPv6 needed).
2. **Datacenter confirmation** — Rishi's expectation is all Falkenstein; we verify on first SSH (Hetzner puts DC in `/etc/hetzner-provision`).
3. **Disk layout** — user unsure; deferred (see §10). Not blocking bootstrap.

---

## 5. Node role layout (final, with real hardware)

```
rishi-4 — edge + state primary         rishi-5 — edge mirror + observability    rishi-6 — heavy + quorum
(4c/8t, 62 GB, ~450 GB NVMe)          (4c/8t, 62 GB, ~450 GB NVMe)            (4c/8t, 62 GB, ~450 GB NVMe)
─────────────────────────────          ─────────────────────────────────        ──────────────────────────────
Swarm manager                          Swarm manager                            Swarm manager (3rd for quorum)
Caddy Swarm svc replica (:443)         Caddy Swarm svc replica (:443)           No public ports
Patroni leader (usual)                 Patroni sync replica (Falkenstein)       Patroni async replica (possibly NBG1)
etcd 1                                 etcd 2                                   etcd 3
PgBouncer                              PgBouncer                                —
Redis primary                          Redis replica                            Redis Sentinel (3rd)
WAL-G shipper                          Prometheus + Grafana + Loki + Alloy      Langfuse (web + worker + PG + CH)
Hot-path services (3-replica)          Hot-path services (3-replica)            Hot-path services (3-replica)
Core services (2-replica)              Core services (2-replica)                Heavy services (media-gen, skill-runtime)
                                       Uptime Kuma                              arq worker pool
                                                                                Background services (scheduler, advisor)
                                                                                Backup runner
```

**Replica tiers** (cluster-wide count):

| Tier | Services | Replicas | Where |
|---|---|---|---|
| Hot path | public-api, orchestrator | 3 | everywhere |
| Core stateful | soul-file-library, user-memory, influencer-profile, safety-and-moderation | 2 | rishi-4 + rishi-5 |
| Supporting | events-analytics, creator-studio, payments | 2 | one edge + rishi-6 |
| Heavy | media-generation-and-vault, agent-skill-runtime | 2 | rishi-6 + one edge |
| Background | proactive-scheduler, meta-advisor | 1 | rishi-6 |

**Per-replica defaults** (overridable per service in `project.config`):

| Tier | CPU limit | RAM limit |
|---|---|---|
| Hot path | 1.0 | 768 MB |
| Core stateful | 0.5 | 384 MB |
| Supporting | 0.5 | 384 MB |
| Heavy | 1.5 | 1.5 GB |
| Background | 0.25 | 256 MB |

**Patroni topology if rishi-6 is cross-DC (per §10 update 2026-04-23):** if reverse-DNS confirms rishi-6 is in Nuremberg, not Falkenstein, we adjust:
- Patroni **leader** on rishi-4 (Falkenstein)
- Patroni **sync replica** on rishi-5 (Falkenstein — co-DC with leader, commits wait only ~1 ms)
- Patroni **async replica** on rishi-6 (Nuremberg — streams WAL continuously, but writes don't wait for it)
- etcd quorum still 3 of 3 across both DCs (etcd tolerates 5–8 ms cross-DC latency fine)
- **Benefit:** writes don't pay cross-DC latency; disaster resilience across DCs for read capacity.
- **Risk if Falkenstein loses power:** rishi-4 + rishi-5 both down, rishi-6 has data (async lag < 1 s typical) but promoting it would lose any in-flight sync-committed transactions. Patroni handles this automatically; acceptable for our data model.

**Staging environment (locked in):** every service has a staging deploy alongside prod from day 1. Design:
- **Shared expensive infra**: one Patroni cluster, one Redis, one Langfuse. Staging data is separated by namespace:
  - Postgres: prod uses schema `chat_ai_v2_*`, staging uses `staging_chat_ai_v2_*`.
  - Redis: prod keys prefixed `prod:`, staging keys prefixed `staging:`.
  - Langfuse: `environment=production` vs `environment=staging` tag on every trace.
  - Sentry: same project, `environment` tag distinguishes.
- **Duplicated app containers**: prod has tiered replicas (hot 3×, core 2×, etc.); staging has **1 replica per service** at **50% of prod resource limits**.
- **DNS**: add `*.staging.v2.rishi.yral.com` wildcard alongside `*.v2.rishi.yral.com`. Both point to rishi-4/5 Caddy.
- **Backups**: prod schemas backed up by all three layers; staging schemas not backed up. Staging is reseeded from a sanitised prod snapshot weekly (WAL-G restore into staging schemas with a script that redacts PII columns).
- **CI flow**: push to main → auto-deploy to staging → automated smoke test + latency gate → **manual "promote"** button in GitHub Actions → canary deploy to prod.

**Headroom math (steady state, per node, with staging):**
- Each node: ~2 GB OS/Docker + ~3 GB Patroni + ~3 GB Redis (primary only on r4) + ~1 GB etcd/PgBouncer/HAProxy/Caddy + observability/Langfuse on r5/r6 (~4/8 GB) + prod app replicas (~6–8 GB) + staging app replicas (~1.5–2 GB)
- Total per node: **~20–27 GB used** out of 62 GB → **~55% headroom**. CPU: staging adds ~1 vCPU per node → still ~45% headroom at steady state.
- Enough headroom for rolling deploys (old + new replicas coexist briefly), Month-6 traffic growth, and a full node failure (remaining 2 carry the load).

---

## 6. Cluster management — the "bulletproof" bits

These are the ops disciplines that make the cluster not bite us at 3 AM. Every one of these goes into the v2 template or the cluster bootstrap. No service deploys before they're all in place.

### 6.1 Reboot resilience (direct response to the April 19 incident in memory)

Memory `reference_docker_restart_policy_edge_case.md` says `restart: always` failed non-deterministically on both rishi-1 and rishi-2 after the same reboot. Pattern: two bit-for-bit identical hosts, same failure. We cannot trust Docker's restart policy to survive reboots.

**Mitigation:** `yral-v2-swarm-resync.service` — systemd oneshot that runs *after* `docker.service` is up. It iterates every `.yml` in `/opt/yral-v2/stacks/` and runs `docker stack deploy -c <file> <stackname> --with-registry-auth`. Idempotent; no-ops if already running. Restores full cluster state regardless of Docker's behaviour.

**Verification:** reboot rishi-6 during Phase 0. Every stack must come back within 2 minutes without manual intervention. This is a Phase 0 exit criterion.

### 6.2 Swarm config immutability (per memory `reference_template_haproxy_cfg_bug.md`)

Every Swarm config (haproxy.cfg, redis.conf, pgbouncer.ini, Patroni overrides, Caddy imports) uses the SHA-suffix pattern: `name: <stack>_<configname>_<sha8>`. CI computes the SHA of the file content, injects it into the stack YAML, and prunes old configs after rollout succeeds. This pattern is proven — existing template already has it working. Inherit it.

### 6.3 Three-layer backup (per memory `feedback_three_layer_backup.md`)

- **L1 HA:** Patroni leader + sync replica on rishi-4/5; async on rishi-6. Sync commit means ≥1 replica ack required. RPO = 0 within cluster, RTO < 60 s for auto-failover.
- **L2 PITR:** WAL-G ships every WAL segment to Hetzner S3 bucket `rishi-yral-wal-archive` in near-real-time. RPO ≈ 1 min. Enables restore to any second in the last 7 days.
- **L3 Offsite:** daily `pg_dump` to Hetzner S3 (30-day retention), weekly `pg_dump` to Backblaze B2 (3-month retention, different provider = different failure domain), monthly encrypted dump (1-year retention).
- **Verification:** weekly CI job restores yesterday's dump into a throwaway Postgres, runs sanity queries, destroys it. Pages on failure. Quarterly manual DR drill — full restore from Backblaze to a fresh server, time-to-recover measured.

### 6.4 Secrets (per memory `feedback_secrets_github_primary_vault_shared.md`)

- **GitHub Secrets per-repo** is the primary store for per-service secrets (DB DSNs, LLM keys, third-party API keys).
- **Vault (`vault.yral.com`) is read-only** for team-shared lookups already there (e.g., `YRAL_METADATA_NOTIFICATION_API_KEY`). We don't put new things in Vault — that's Naitik's domain.
- **Runtime injection via Swarm secrets**, mounted as files at `/run/secrets/<name>`. Nothing secret in images, nothing in git. Rotation = `gh secret set` → redeploy → new containers read new values.
- **Audit:** GitHub Actions logs show which workflow read which secret; Vault logs its own access. Monthly 15-min skim.

### 6.5 Monitoring & alerting

- **Prometheus** scrapes every service's `/metrics` on the Swarm-internal port. Scrape interval 15 s.
- **Alertmanager** rules (start with ~10 rules, grow from pain):
  - Service replica count < desired for >2 min
  - Service container CPU >90% sustained 5 min
  - Service container memory >85% sustained 5 min
  - Patroni leader election in last 5 min
  - Redis replica lag > 5 s
  - WAL-G archive failure
  - Backup job failure
  - Disk free < 20%
  - HTTP 5xx rate > 1% over 5 min
  - LLM provider error rate > 5% over 5 min
- **Alerts route to Google Chat webhook** (same mechanism current chat-ai uses for notifications — you already have it wired).
- **Sentry** catches application errors + performance traces. Tagged with `service=<name>`. Reuses `apm.yral.com`.
- **Langfuse** captures every LLM call: prompt, response, tokens, latency, cost. Per-turn trace joinable to Sentry + Prometheus via correlation ID.
- **Uptime Kuma** at `status.yral.com` hits every service's `/health`. Reuses existing.

### 6.6 CI/CD guardrails

- Gitleaks scans every push for accidentally-committed secrets. Fails the build on hit.
- Compose-limits lint: every service must declare CPU and RAM limits. Fails the build if missing.
- Trivy image scan: block CRITICAL/HIGH CVEs from merging to main.
- Canary deploy pattern (inherited from existing template): rishi-4 first → health check → rishi-5 → health check → rishi-6. Failure at any step = automatic rollback to last-good image tag.
- Migration discipline: DB migrations run **before** new app starts (expand-contract pattern, already in existing template). New column/table is additive; the code handles both old and new schema for one release, then the old schema is removed in a follow-up release. **Migrations run against staging schemas first** (auto, on every push); staging failures block promote-to-prod.
- **Latency gate** (new, per v2 §2.8): every PR runs a synthetic-load smoke test against the staging deploy and compares p50/p95/p99 to the latency-baselines file. Regression = block merge.
- **Two-step deploy:** push to main → GitHub Actions auto-deploys staging (no human in the loop). Automated smoke + latency gate runs against staging. Green result enables the manual "promote to prod" workflow button. Clicking it runs the canary pattern above. Red result blocks promotion and opens a GitHub issue.
- **Staging reseed:** weekly GitHub Actions job restores the latest prod Patroni snapshot into the `staging_*` schemas with a redaction SQL script that scrubs PII columns (message bodies, user emails, payment details → replaced with dummy data while preserving structure and volume). Ensures staging has realistic-shape data without leaking user info.

### 6.7 Chaos testing (Phase 0 exit criterion)

Before any real service runs on this cluster:
- Kill rishi-6 (simulated — `docker node update --availability drain`). Verify hot-path replicas reschedule, Patroni leader holds, Redis sentinel still has quorum.
- Kill rishi-4 Patroni container. Verify failover to rishi-5 within 60 s.
- Fill rishi-5's disk to 80%. Verify alerts fire.
- Partition rishi-6 from rishi-4/5 for 10 min. Verify etcd quorum holds (2 of 3).
- Restore. Verify async replica catches up within 5 min.

If any test fails, we fix before proceeding. This is the price of "bulletproof."

---

## 7. The v2 template — what goes in, learning by learning

The v2 template is a **fresh repo** (`dolr-ai/yral-rishi-chat-ai-v2-new-service-template`) that *forks from* the existing `yral-rishi-hetzner-infra-template` and *evolves*. Below is the explicit list of everything we inherit unchanged, every pain point from the existing template we fix in v2, and every net-new capability we add. This table is the spec for the v2 template. **Every row is reviewable — we refine this in conversation.**

### 7.1 Inherited from existing template (keep as-is; they work)

| Capability | Existing template file / pattern | Why keep |
|---|---|---|
| 1-command spawn | `scripts/new-service.sh` (457 lines) | Proven; ADHD-friendly mental model |
| project.config single source of truth | `project.config` | Clean separation of config from code |
| Canary deploy + auto-rollback | `.github/workflows/deploy.yml` | Matches latency-never-regresses rule |
| Caddy per-service snippets | `caddy/snippet.caddy.template` | Scales cleanly to 13+ services |
| Patroni HA PG + etcd + HAProxy | `patroni/`, `etcd/`, `haproxy/` | Same pattern on fresh cluster |
| Documentation standards (5 docs) | DEEP-DIVE, READING-ORDER, CLAUDE, RUNBOOK, SECURITY | Memory: non-negotiable |
| S3 backup + restore workflow | `.github/workflows/backup.yml`, `backup/backup.sh` | Project-isolation guard is good |
| Gitleaks + Trivy in CI | `.github/workflows/deploy.yml` | Already works |
| SHA-rotating Swarm configs | `haproxy/stack.yml` pattern | Memory: proven fix for April 20 bug |
| Secrets pattern | `infra.get_secret()` + GitHub Secrets + Swarm secrets | Memory: exact pattern to mirror |
| `strip-database.sh` | `scripts/strip-database.sh` | Still useful for genuinely stateless services |

### 7.2 Existing-template pain points we FIX in v2

| Pain (source) | Fix in v2 template |
|---|---|
| No resource limits on Patroni/etcd/HAProxy/app | Default limits on every container; CI lint rejects compose without them |
| `pg_hba.conf` too permissive (`0.0.0.0/0`) | Lock to db-internal overlay subnet only |
| UFW inactive | Bootstrap enables UFW with explicit allow-list |
| No PgBouncer | PgBouncer in front of Patroni from day 0 |
| Docker volumes for Patroni data | Bind-mount to `/data/patroni` (survives `docker system prune`) |
| No rate limiting | FastAPI per-user rate-limit middleware using Redis for distributed counting (no custom Caddy build; keeps Caddy stock) |
| No staging environment | Add `staging.v2.rishi.yral.com` wildcard; every service deploys to staging automatically |
| Restart-always unreliable on reboot | `yral-v2-swarm-resync.service` systemd unit |
| No per-service tests enforced | Template ships with skeleton pytest + require one test passes before deploy |
| No graceful shutdown | FastAPI lifespan hooks + SIGTERM handler draining in-flight requests |
| No Prometheus/Grafana/Loki | Added as a first-class stack on rishi-5 |
| Per-service Patroni is too heavy | **V2 uses one shared Patroni cluster with schema-per-service.** Consciously accepted 2026-04-23: all 13 services degrade simultaneously on a cluster outage. Mitigated by Patroni HA (3 nodes, sync replica) + WAL PITR + weekly Backblaze + chaos-tested failover. This is the standard pattern at this scale. |
| No connection cap per-tenant | `ALTER ROLE ... CONNECTION LIMIT 20; statement_timeout = '30s'; idle_in_transaction_session_timeout = '60s'` at tenant bootstrap |
| Single shared S3 bucket for all backups | Retain shared `rishi-yral` bucket; per-project prefix convention; tenant isolation enforced in `backup.sh` (decided 2026-04-23 — simpler than separate buckets) |
| No circuit breaker for external APIs | Bake `tenacity` + `pybreaker` into LLM client + HTTP client wrappers |
| Caddy on-host (existing template pattern) | **Caddy as Swarm service** on rishi-4/5 per §1.7; only port 443 exposed via Swarm ingress; TLS via Cloudflare DNS-01 ACME (cert stored in Swarm secret, not host volume) |

### 7.3 Net-new capabilities (because v2 needs them)

| Capability | What it is | Why needed |
|---|---|---|
| `SERVICE_PROFILE` = api / worker / cron / **streaming** | Four-way branch in `new-service.sh`; different compose shape per profile. `streaming` profile adds sticky-session Caddy config, extends graceful shutdown to 60 s (long SSE/WebSocket connections), and different health-check semantics (active stream count, not just `/health`) | v2 has workers (arq on Redis), cron (GitHub Actions + one-shot Swarm service), and SSE/WebSocket streaming (public-api) as first-class patterns, not afterthoughts |
| Blessed worker lib: **arq** (confirmed 2026-04-23) | async Redis queue, fits FastAPI/asyncio, ~1500 LOC | Avoids Celery complexity; one library to learn |
| Redis client baked in | `app/redis_client.py` — one import | 13 services all need it |
| Redis Streams helpers | `emit_event(...)`, consumer-group subscribe | Cross-service event bus per v2 §2.6 |
| Langfuse middleware | Auto-traces every LLM call; one import | Per-turn observability is critical for "1000× better" |
| LLM-client abstraction | `llm_client.chat(messages, model=...)` wraps Gemini / Claude / GPT / OpenRouter / self-hosted | Memory: LLM-agnostic is first-class goal |
| Feature-flag client (confirmed 2026-04-23) | Custom Postgres-table, polled every 30s; ~200 LOC; supports on/off + % rollout; upgrade to Unleash later if experimentation gets serious | Every new capability ships behind a flag (1% → 10% → 100%) |
| Uniform `/health` schema | `{status, version, uptime_seconds, dependencies: [...]}` | Uptime Kuma auto-understands; every service same shape |
| Structured JSON logs + correlation IDs | One middleware; request-id propagates through all downstream calls | Debugging any turn is one log search |
| Prometheus `/metrics` | `prometheus-fastapi-instrumentator` | Built-in dashboards work |
| Pre-flight deploy check | Script runs before first deploy: all secrets set? Sentry DSN valid? Postgres reachable? migrations idempotent? | Prevents broken first deploys |
| MCP tool-runtime helper (confirmed 2026-04-23) | Anthropic's official `mcp` Python SDK as the client; thin template helper wraps it with correlation-ID + Langfuse tracing | All services that invoke tools talk to it uniformly; stays in sync with MCP spec as it evolves |
| Safety filter middleware | Optional pre/post request filters | Every user-facing service can flip it on via config |
| Graceful shutdown | FastAPI lifespan + SIGTERM handler draining in-flight | Zero-dropped-request rolling deploys |
| Circuit breakers | `pybreaker` wrappers on LLM client + third-party HTTP | Failing upstream doesn't cascade |
| Retry with jittered backoff | `tenacity` on transient failures | Handles network blips without ops noise |
| Idempotency key support (confirmed 2026-04-23 — default-on) | Middleware enforces `X-Idempotency-Key` header on all non-GET endpoints; dedupes via Redis 24 h TTL. Per-endpoint opt-out for truly stateless writes (e.g., analytics event ingress) | Mobile retries on flaky networks never create duplicates; safer default than opt-in |
| `services.yaml` auto-register (confirmed 2026-04-23) | Lives in new `dolr-ai/yral-rishi-chat-ai-v2-cluster-bootstrap` repo. `new-service.sh` final step opens a PR against that repo. Merge triggers regeneration of Prometheus scrape config, Caddy snippets on rishi-4/5, Uptime Kuma monitors, Grafana folders | Clean separation: strategy in plan repo, infra ops in bootstrap repo, code in service repos. Every service registered in one place |
| Schema-per-service bootstrap | Tenant SQL template creates schema + role + GRANTs + connection cap | 13 services on one Patroni cluster, cleanly |
| pgvector ready (confirmed 2026-04-23 — day 1) | Migration adds `CREATE EXTENSION IF NOT EXISTS vector` once per cluster. Migration path to dedicated Qdrant kept behind same interface; trigger at ~50 M vectors (Month 12+ projection per v2 §2.7.5) | user-memory-service needs it day 1; simpler to operate inside Patroni than a separate Qdrant service until scale demands it |
| WAL-G restore drill | Weekly CI job restores yesterday's WAL into throwaway Postgres | Backups that aren't restored aren't backups |
| Latency-baseline enforcement | Smoke-test job in CI; compares to `latency-baselines.md` | Latency never regresses rule |
| Staging auto-deploy (locked in — see §5 + §6.6) | Every push to main auto-deploys to staging at `<svc>.staging.v2.rishi.yral.com`; smoke test + latency gate run automatically; manual "promote" button in GitHub Actions triggers canary deploy to prod | Catch regressions and broken schema migrations before users do; rehearse cross-service integration changes against real infra |
| Eval harness baked in (added 2026-04-23 from v2 plan §2.1 "eval-driven") | Template ships with `evals/` folder: held-out prompt set, promptfoo-style runner, CI job executes evals on every PR to LLM-touching services and posts diff to the PR. No prompt change ships without eval delta review. | v2 plan's explicit commitment: "Every change tested offline against held-out prompts before shipping. No more 'tweak and pray.'" |
| Shadow traffic middleware (added 2026-04-23 from v2 plan §2.1) | Optional middleware that mirrors incoming requests to a second handler (the candidate) while serving real responses from the primary. Responses compared offline, diff surfaced in Langfuse. | Critical for v1 → v2 cutover: every new orchestrator change runs shadow against prod chat-ai for N days before promotion. Also useful for model A/B |
| Per-turn cost tracking middleware (added 2026-04-23 from v2 plan §2.1) | LLM-client wraps every provider call; logs `{user_id, influencer_id, model, input_tokens, output_tokens, estimated_cost_usd}` → Redis Stream `cost.turn` → analytics service rolls up per-user-per-day spend | Unit economics at 1M msg/day (v2 §2.7.5) require knowing per-user cost live. Gate feature rollouts on cost delta |
| Cost-cap circuit breaker (added 2026-04-23) | Per-user and per-influencer daily cost cap (configurable via feature flags). When exceeded: (a) log Sentry warning, (b) switch LLM routing to a cheaper model, (c) if already on cheapest, refuse with a graceful "resting the bot" message | Prevents a single user costing $10/day. Essential at self-hosted-LLM transition point (v2 §2.5 target Month 6+) |
| PII-aware log redaction middleware (added 2026-04-23) | Structured logger with an explicit allow-list of fields safe to log (status, latency, request_id, correlation_id, service_name, duration_ms). Everything else — message bodies, user names, email, phone — is either redacted to `<REDACTED:kind>` or hashed. Sensitive columns in Postgres tagged at schema level | User chats are private by product promise. Never leak message bodies to Loki, Sentry breadcrumbs, or Langfuse traces. Compliance + trust baseline |
| Prompt injection defense middleware (added 2026-04-23 from v2 plan §2.1 "safe at edges") | Every user message runs through a lightweight classifier (local + Gemini 2.0 Flash fallback) before orchestrator composes the Soul File. Detected injections: block the turn, log to Sentry with `type=prompt_injection`, respond with safe fallback | Hostile users will try to extract system prompts or redirect the bot. Defense at the gate is cheaper than fix-on-fire |
| Three-tier health endpoint split (added 2026-04-23) | `/health/live` — process alive (200 always if running); `/health/ready` — dependencies healthy (503 if Patroni/Redis/Langfuse unreachable); `/health/deep` — executes a real round-trip (expensive, only called by synthetic user or manual check) | Distinguishes "the container is up" from "the service can actually serve." Clean semantics for rolling-deploy gating, Swarm health checks, and load balancer decisions |
| Synthetic user heartbeat (added 2026-04-23) | One containerized "canary bot" per environment that sends a test message to `public-api` every 5 minutes via the real API + real auth. Traces in Langfuse tagged `synthetic=true`. Alert if any turn fails, latency > 2× baseline, or response degrades on eval metrics | Cheap early warning: silent regressions (subtle prompt drift, slow model, broken tool call) show up within 5 min instead of from users |
| Feature flag kill-switch (added 2026-04-23) | Per-service magic flag `SERVICE_DISABLED` that, when flipped on, returns 503 immediately from all endpoints with a configurable "come back soon" message. Can be flipped from Grafana annotation or one-line SQL update | Emergency lever when a service is actively hurting users (bad deploy, upstream LLM outage, data breach). Breaks the glass, buys 10 minutes to think |
| Dead letter queue for workers (added 2026-04-23) | arq workers retry 3× with jittered backoff; after 3rd failure, job goes to DLQ stream `worker.dlq` with full context. Grafana panel tracks DLQ depth; alert fires if >100 or older than 1 h | Silent data loss is the worst failure mode (memory extraction never happens, analytics rollup silently drops rows). DLQ gives us the artifact to debug + replay |
| Schema migration safety net (added 2026-04-23) | Every migration PR auto-runs against a fresh restore of yesterday's prod data (WAL-G restore into throwaway schema) + full test suite. Block merge on failure | Catches "this migration kills a prod table" before users see it. Uses L2 backup infra already planned |
| Per-user/per-influencer rate limiting (added 2026-04-23) | Rate-limit middleware aware of (user_id, influencer_id, subscription_tier) — not just IP. Reads from billing schema to know active subscriptions. Counters in Redis | Matches product model (₹9/24h unlock, per-influencer 50-message limit per v2 plan). Stops rate-limit being a purely infra concern |
| Soul File composition cache (added 2026-04-23 from v2 plan §2.1) | Template helper: composed Soul File (global + archetype + influencer + user_segment layers) is cached in Redis by `(influencer_id, user_segment_id, model, version)` tuple with TTL = 1h or explicit invalidate on any layer update | Composition is ~15 ms per turn; caching shaves it. Matters at 1M msg/day. Also makes A/B of Soul File variants cheap |

---

## 8. How a new service gets built (the workflow, end to end)

Once v2 template is live, the flow is:

1. Rishi picks the next service from the 13 (e.g., `yral-rishi-chat-ai-v2-soul-file-library`).
2. `bash scripts/new-service.sh --name yral-rishi-chat-ai-v2-soul-file-library --profile api --tier core-stateful`
3. Script does (≈5 minutes total):
   - Validates name under 63 chars; <39 chars after prefix.
   - Clones template at latest tag into `~/Claude Projects/<name>`.
   - Renames identifiers in `project.config`.
   - Generates Swarm secrets, Postgres password, Redis key.
   - Creates GitHub repo `dolr-ai/<name>` public.
   - Sets GitHub secrets (DB DSN, LLM keys, S3 creds, Sentry DSN, Langfuse keys, SSH key).
   - Git init + push.
   - Opens PR against plan repo's `services.yaml` adding the new entry.
   - Watches first CI run; verifies `/health` responds.
   - Registers in Uptime Kuma via API.
   - Prints the service's staging URL and next steps.
4. Rishi merges the `services.yaml` PR → Prometheus scrape, Caddy snippet, Grafana folder auto-created.
5. Rishi writes the actual business logic over the next few days. Every PR: CI lint + tests + smoke test + latency gate. Failing any = block merge.
6. Release: promote from staging to prod via `gh workflow run promote.yml`. Canary pattern runs automatically: rishi-4 first, then rishi-5, then rishi-6.
7. Post-merge 15-min retrospective (memory: `feedback_template_first_build.md`): what did we copy-paste? What broke twice? What doc gap did we hit? Fixes go into the template. Version bumps.

---

## 9. "1000× better" — what this actually means, as template features

The product claims (streaming, memory, proactive, multi-modal, etc.) are the user-facing wins. The template's job is to make each of them **cheap to build, safe to ship, fast to serve**. Concrete mapping:

| Product goal | Template feature that enables it |
|---|---|
| First token <200 ms | SSE middleware + streaming LLM client + Langfuse time-to-first-token metric; CI latency gate holds this invariant |
| Remembers forever | pgvector-ready bootstrap; user-memory-service is built on the template with zero infra setup |
| Sounds human | Layered Soul File client + caching; feature flags for A/B prompt variants |
| Has initiative | arq worker profile + Redis Streams triggers; proactive-scheduler builds on these |
| Multi-modal | S3 helper + media-generation service profile gets 2× memory allocation by default |
| Actually does things | MCP tool-runtime client; safety middleware wraps every tool call |
| Bots feel different | LLM-client abstraction lets different bots route to different models by config |
| Safe at the edges | Safety filter middleware + rate-limit + Caddy rate-limit + crisis-detection middleware |
| Operationally boring | Uniform /health, metrics, logs, traces, alerts; one dashboard shape per service; one runbook template |

---

## 10. Open questions we need answered before implementation

**Resolved 2026-04-23:**
- ~~Cloudflare DNS access~~ → Saikat keeps DNS unchanged; we route via rishi-1/2 Caddy. §4.
- ~~Staging env~~ → full staging day 1. §5, §6.6.
- ~~Safety/mod tier~~ → stays at core tier (2 replicas on edges).
- ~~IPv4 / IPv6~~ → IPv4-only. No IPv6 in any script or config.
- **Datacenter — partially confirmed, rishi-6 likely cross-DC (Nuremberg).** Inferred by Hetzner IP-range inspection (2026-04-23; full IP values live only in the `reference_saikat_server_allocation.md` memory + GitHub Secrets):
  - rishi-1, rishi-4 → same `138.201.x.x` pool → **Falkenstein (FSN1)**, confirmed.
  - rishi-2, rishi-3 → `136.243.x.x` pool → **Falkenstein**, confirmed.
  - rishi-5 → `88.99.x.x` pool → **Falkenstein OR Nuremberg**, ambiguous from range alone.
  - rishi-6 → `162.55.x.x` pool → **likely Nuremberg (NBG1)** based on newer Hetzner range allocation pattern.
  - **Verification methods:** `hcloud server describe <name>` via Hetzner API (cleanest), or reverse DNS (turned out to be inconclusive — all three return the generic `static.*.clients.your-server.de`).
  - **Impact if rishi-6 IS Nuremberg:** ~5–8 ms round-trip latency Falkenstein ↔ Nuremberg. Not catastrophic, but reshapes the Patroni topology (see §5 update below). Saikat may have deliberately spread us across DCs for disaster resilience — worth confirming intent.
- ~~Private networking~~ → public network + TLS for Phase 0, revisit at Month 6. §4.
- ~~Root access window~~ → ~1 week on rishi-4/5/6. Can get rishi-1/2/3 root if needed for any private-networking setup.
- ~~Caddy config management on rishi-1/2~~ → SSH as `deploy` with `rishi-hetzner-ci-key`; drop snippet into `/home/deploy/caddy/conf.d/`. §4.
- ~~`rishi-deploy` user convention~~ → use the same `rishi-hetzner-ci-key` as rishi-1/2/3. §3.

**Resolved 2026-04-23 (late):**
- ~~IPv4 addresses of rishi-4/5/6~~ → **Saikat provisioned all 3 boxes 2026-04-23 evening.** IPs received from user (also rishi-1/2/3 confirmed). Per §1.6 the actual values live only in GitHub Secrets + a local memory file — never in this plan document or any committed file. Secrets to set once implementation approval is given:
  - `RISHI_1_PUBLIC_IPV4`, `RISHI_2_PUBLIC_IPV4`, `RISHI_3_PUBLIC_IPV4`, `RISHI_4_PUBLIC_IPV4`, `RISHI_5_PUBLIC_IPV4`, `RISHI_6_PUBLIC_IPV4`
- Rishi-1/2/3 IPs also available (memory already had rishi-1/2/3 per `reference_saikat_server_allocation.md`). Will be refreshed in a new memory file to include the full 6-node fleet.

**Still open — not blocking, can defer:**

3. **Disk layout on rishi-4/5/6** — user unsure; pointed at Beszel. Not blocking for Phase 0 because both RAID0 and RAID1 work with our three-layer backup (L1 HA + L2 WAL PITR + L3 off-site). RAID1 is preferred (losing one disk ≠ losing one node). We verify when we have root access on day 0 via `cat /proc/mdstat`. If RAID0, we document it as an extra-risk factor in RUNBOOK.md.
4. **Uptime of rishi-1/2 as our edge proxy** — our v2 availability is now coupled to rishi-1/2 uptime. Worth asking Saikat at some point: what's the SLA, who gets paged, what maintenance windows? Not blocking, but we document as a shared dependency.
5. **Sentry, Langfuse, Vault access** — we need to create 13 new Sentry projects (one per v2 service); confirm Naitik/Saikat are fine with us self-service creating them at apm.yral.com. Same for Uptime Kuma monitor creation. Vault is read-only lookup, no new secrets added.

**Still open — internal decisions (Rishi):**

6. **Backup off-site pair** — Hetzner S3 (primary) + Backblaze B2 (weekly off-site) vs. other. ~€50/mo total at projected Month-12 volume.
7. **Rollout trigger for implementing** — what's your bar for "plan is solid"? A completed Saikat memo, a walk-through of Phase 0 day-by-day, or ready now?

---

## 11. What "refinement" looks like from here

Every turn going forward, we can:
- **Add or change a row** in §7.1/§7.2/§7.3 (template capability list).
- **Re-tier a service** in §5.
- **Add or change a guardrail** in §6.
- **Answer an open question** in §10 and move it into the confirmed plan above.
- **Remove something** you don't think we need (subtract is as important as add).

When you're ready to start building: say "approve v2 template build" and I'll drop out of plan mode and start the new repo. Until then, this file is the living plan.

---

## 12. Files that will eventually be touched (for reference, not now)

**New repos to create (after approval):**
- `dolr-ai/yral-rishi-chat-ai-v2-new-service-template` — the template itself.
- `dolr-ai/yral-rishi-chat-ai-v2-cluster-bootstrap` — node bootstrap scripts, systemd units, UFW rules, `services.yaml` + auto-sync action. One repo per cluster, not per service.

**Local working dirs:**
- `~/Claude Projects/yral-rishi-chat-ai-v2-new-service-template`
- `~/Claude Projects/yral-rishi-chat-ai-v2-cluster-bootstrap`

**Untouched (per covenant):**
- `yral-rishi-hetzner-infra-template` — predecessor, frozen.
- `yral-chat-ai` — current prod, frozen.
- rishi-1/2/3 — frozen.
