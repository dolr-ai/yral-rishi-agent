# Runbook — Unify rishi-1..6 into one Swarm, fold Sentry in, delete the dead stacks

**Status:** IN PROGRESS (started 2026-08-02). Supersedes the old
`decommission-rishi-1-2.md` (we now *keep* all 6 nodes as swarm capacity, not wipe 1/2).
**Goal:** one Docker Swarm across all 6 nodes, one global edge Caddy, Sentry on the
overlay, blanket `*.rishi.yral.com → all 6` — then remove chat-ai / counter-db /
infra-template. Zero downtime for v2 / amorae / the other live surfaces.

---

## How outside requests are handled (target architecture)

```
Browser/app → Cloudflare (*.rishi.yral.com → all 6 IPs) → :443 on any node
  → [routing mesh] → Edge Caddy (global, every node) → terminates TLS, routes by hostname
  → [overlay network] → target service container on ANY node → response back
```
- **Routing mesh:** every node accepts `:443` and forwards to the edge Caddy.
- **Overlay:** services reachable by name from any node (this is what makes Sentry work
  fleet-wide). Edge is on `yral-v2-public-web`.
- **Client IP:** ingress mode SNATs the source IP; the real client IP arrives via
  Cloudflare's `CF-Connecting-IP` header, which Caddy forwards. (Switch edge to
  `mode: host` later only if TCP-level client IP is ever needed.)

## End state
- **Managers:** rishi-4/5/6 (keep 3 for quorum). **Workers:** rishi-1/2/3.
- **Edge Caddy:** global on all 6, serves every `*.rishi.yral.com` host + `amorae.ai`.
- **Sentry:** compose stack stays on rishi-3, `nginx` attached to the swarm overlay.
- **Deleted:** chat-ai (+db), counter-db, rishi-hetzner-infra-template (+db),
  chat-ai-metabase, the old L1 Caddy.

## Verified facts (why safe)
- chat-ai DEAD since 2026-07-15 (0 msgs; 100% traffic on v2, 687/hr). Stopping it is safe.
- Swarm edge already serves agent/langfuse-agent/analytics/metabase (200 on rishi-4).
  Gaps to close before the wildcard: **sentry** (localhost-bound on rishi-3) and
  **amorae.ai** TLS (grey-cloud, needs DNS-01). marketing already 503 (dead).
- Edge Caddy: `:443` ingress mode, replicated 2, on overlays incl. `yral-v2-public-web`.

---

## Phase 0 — Prereqs (before edge changes)
- [ ] **caddy-cf-dns image → GHCR** (local-only on 1/2/3 today). On rishi-3:
      `docker tag caddy-cf-dns:2-alpine-2026-07-10 ghcr.io/dolr-ai/caddy-cf-dns:2-alpine-2026-07-10 && docker push …`
      (fallback: `docker save | ssh <node> docker load` onto 4/5/6).
- [ ] **CF token → swarm secret** (reuse the L1 one from `/home/deploy/caddy/.env`):
      `… CF_DNS_API_TOKEN … | docker secret create cf_dns_api_token -`
- [ ] **Cloudflare SSL/TLS mode = Full** (not strict) — Saikat confirms.
- [ ] **Snapshots / notes:** `chat_ai_db` pg_dump archive; screenshot current CF records.
- [ ] **Lower CF TTL** to 60s on affected records ahead of the wildcard flip.

## Phase 1 — rishi-3 joins the swarm (worker)   ◀ START HERE (reversible)
```bash
# on a manager (rishi-4):
docker swarm join-token worker
# on rishi-3 (deploy user):
docker swarm join --token <worker-token> <manager-ip>:2377
```
- **Verify:** `docker node ls` shows rishi-3 Ready; existing services untouched;
  v2 `/health` 200; Sentry still healthy on rishi-3.
- **Rollback:** `docker node rm rishi-3` (drain first).
- **Ports needed between nodes:** 2377/tcp, 7946/tcp+udp, 4789/udp.

## Phase 2 — Fold Sentry onto the overlay
- Make the public overlay attachable if needed; attach Sentry's nginx:
  `docker network connect yral-v2-public-web sentry-self-hosted-nginx-1` (on rishi-3).
- Add to the edge Caddyfile (CF-proxied, `tls internal` fine):
  ```
  sentry.rishi.yral.com {
      tls internal
      reverse_proxy sentry-self-hosted-nginx-1:80 { header_up X-Forwarded-Proto {scheme} }
  }
  ```
- Roll the edge config (docker config is immutable → new version + `--config-add/rm`).
- **Verify:** from every node, `curl --resolve sentry.rishi.yral.com:443:<node-ip> -k …` 200;
  Sentry keeps ingesting v2 events.

## Phase 3 — amorae.ai DNS-01 on the edge (Option A)
- Edge image → `ghcr.io/dolr-ai/caddy-cf-dns:2-alpine-2026-07-10`; attach `cf_dns_api_token`
  secret; change amorae block `tls internal` → `tls { dns cloudflare {env.CF_DNS_API_TOKEN} }`.
- **Gate:** `openssl s_client` to each node for amorae.ai must show a **Let's Encrypt**
  issuer, not "Caddy Local Authority". If not, STOP.

## Phase 4 — rishi-1/2 join the swarm (workers)
- `docker swarm join …` on rishi-1 and rishi-2. Legacy containers keep running.
- Keep managers = 4/5/6 only.

## Phase 5 — Global edge + take `:443` on all 6
- Set edge Caddy to **global**: `docker service update --mode ...` (or redeploy stack
  with `mode: global`).
- **Free `:443` on 1/2/3:** stop the L1 Caddy container on each (`docker stop caddy` on
  1/2/3) — the routing mesh immediately serves `:443` there. chat-ai edge dies with it
  (dead anyway).
- **Verify:** every host (agent, langfuse-agent, analytics, metabase, sentry, amorae)
  returns correctly hitting **each of the 6 node IPs** via `curl --resolve`.

## Phase 6 — Cloudflare blanket wildcard
- Saikat: `*.rishi.yral.com → rishi-1/2/3/4/5/6`. (amorae.ai handled separately, Phase 3.)
- **Verify** all hosts through real DNS; v2 still writing messages; app sees real client
  IP via `CF-Connecting-IP`. **Soak 24–48h**; rollback = pull 1/2/3 IPs from the record.

## Phase 7 — Delete what's not needed (reversible → destructive)
Stop first, verify, then remove. On rishi-1/2/3 (and 2 for metabase):
```bash
docker stop yral-chat-ai yral-chat-ai-metabase caddy   # [confirm names per node]
docker stack rm chat-ai-db counter-db rishi-hetzner-infra-template-db   # [confirm stack names]
```
- **Keep:** Sentry (overlay), all 6 as clean swarm nodes.
- Reclaim volumes only after a final soak. `docker system prune` per node last.

## Rollback / abort
| Phase | Rollback |
|---|---|
| 1/4 (joins) | `docker node rm <node>` |
| 2 (sentry) | revert edge config; detach overlay |
| 3 (amorae) | revert edge image+config to plain caddy; no DNS touched |
| 5 (global/:443) | restart L1 Caddy on the node; revert edge to replicated |
| 6 (wildcard) | CF record back to prior IPs (60s TTL) |
| 7 (deletes) | redeploy the stopped stack from its compose |
**Abort if:** amorae not a real LE cert; any live host drops below 100%; v2 stops
writing; Sentry stops ingesting; two things break same day.

### Open values to fill
GHCR push creds on rishi-3 · CF token env-vs-file mechanism (match L1) · exact stack/
container names on 1/2/3 · who runs the Cloudflare changes.
