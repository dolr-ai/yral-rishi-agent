# Edge — how public traffic reaches the swarm

Until 2026-10-04 these files existed only on the servers. They are the live
versions, copied from the swarm.

| File | Runs as | Job |
|---|---|---|
| `issuer.Caddyfile` | `edge-issuer` (1 copy, owns :80) | Gets and renews certificates (HTTP-01) and stores them in Redis |
| `edge-cert-sync.sh` + `edge-cert-sync-stack.yml` | `edge-cert-sync_sync` (1 copy, on a manager) | Every 12 h copies Redis certificates into swarm secrets and rolls the edge |
| `serve.Caddyfile` | `yral-v2-edge-caddy_caddy-edge-ingress` (3 copies, owns :443) | Serves every site with the synced certificates |

**Adding a site:** add the host to `issuer.Caddyfile` and roll `edge-issuer`
onto a new config. Wait for "certificate obtained", then
`docker service update --force edge-cert-sync_sync` to sync now. Last, add
the site to `serve.Caddyfile` and roll the edge onto a new config. In that
order: the edge refuses to start if a site names a certificate file that
doesn't exist yet.

**Rolling the edge:** use stop-first. It allows one copy per server, so
start-first hangs when every server already holds one.
