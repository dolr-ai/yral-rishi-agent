# Redis Sentinel failover drill — runbook

**Phase**: 21αβ.H5
**Status**: prepared 2026-06-13, **execution gated on Rishi green-light post rollout-stable for 24h**

## Why this drill exists

The V2 WebSocket inbox routes cross-replica messages through Redis pub/sub. If the primary dies and Sentinel doesn't cleanly promote, every message in flight is silently dropped + every reconnecting subscriber lands in a void. We've never tested promotion under load.

This drill proves:
- Sentinel detects the primary loss + promotes a replica in <30s
- Pub/sub keeps working immediately on the new primary
- The killed node rejoins cleanly as a replica (cluster stays 3 healthy nodes)
- WebSocket subscribers reconnect within the asyncio retry window

## Pre-flight (operator)

- [ ] Rollout has been stable for 24h (no Sentry pages on WebSocket reconnect)
- [ ] No active alpha-soak chat session (check #yral-alpha for the last 15 min)
- [ ] You have SSH access to whichever host currently holds the Redis primary
  - Find it: `redis-cli -h <sentinel-host> -p 26379 SENTINEL get-master-addr-by-name mymaster`
- [ ] You know which node is the Sentinel "leader" (any of the 3 Sentinels can serve queries; the leader handles promotion). Find via: `redis-cli -h <sentinel-host> -p 26379 SENTINEL is-master-down-by-addr <primary-ip> <primary-port> 0 ""`
- [ ] Off-hours: 02:00-05:00 UTC ideal

## Execution paths

### Path A — GitHub Actions workflow (recommended)

1. Open the [Redis Sentinel drill workflow](https://github.com/dolr-ai/yral-rishi-agent/actions/workflows/redis-sentinel-failover-drill.yml).
2. Click **Run workflow**.
3. Inputs:
   - `target_host_label`: the host currently running the Redis primary container
   - `reason`: free-text. Format: "H5 drill — <date> — <operator initials>"
   - `i_understand`: type `RUN REDIS DRILL` exactly
4. Click **Run workflow**. The workflow SSHs to the target host, runs the script, captures the report.

### Path B — SSH + manual

```sh
ssh rishi-deploy@<primary-host-ip>

# Resolve the primary's container ID
docker ps --filter "label=com.docker.swarm.service.name=yral-v2-redis"

# Run the drill (the script handles primary identification + cleanup)
chmod +x /tmp/redis_sentinel_failover_drill.sh
bash /tmp/redis_sentinel_failover_drill.sh
```

## What the script does — step-by-step (Phase A, automated)

1. **Snapshot pre-drill state**: identify Sentinel-known primary via `SENTINEL get-master-addr-by-name mymaster`, snapshot the failover epoch.
2. **Pre-drill tracer**: SUBSCRIBE to a unique `drill:<ts>:<pid>` channel from a sidecar redis-cli; PUBLISH a tracer; verify it arrives (proves Sentinel cluster is healthy BEFORE the drill).
3. **Kill the primary**: `docker stop` on the Redis container matching the primary's port.
4. **Watch for promotion**: poll `SENTINEL get-master-addr-by-name mymaster` until the IP:port changes. Timeout 60s.
5. **Post-promotion tracer**: SUBSCRIBE to the NEW primary; PUBLISH another tracer; verify it arrives (proves pub/sub recovered cleanly).
6. **Restart the killed node**: `docker start`. Watch `SENTINEL replicas mymaster` until the killed node appears in the replica list. Timeout 120s.
7. **Write report** to `/tmp/redis-sentinel-drill-report-<ts>.txt`.

## Phase B — WebSocket end-to-end smoke (operator-driven)

This proves the user-visible behavior. Requires 3 browser tabs + chat API calls.

1. Open 3 browser tabs to `https://agent.rishi.yral.com/api/v1/chat/ws/inbox/<test-user-id>` (use 3 different test principals). Each tab should establish a WebSocket connection — verify in DevTools' Network → WS tab.
2. From a 4th tab/terminal, POST a chat message via the standard chat-send endpoint that triggers an inbox broadcast (e.g. peer-to-peer chat, or a Coach session).
3. Verify all 3 subscribers receive the broadcast within 1-2s.
4. **Run Phase A drill** (script) in parallel.
5. After the script reports promotion, POST another chat message that triggers an inbox broadcast.
6. Verify all 3 subscribers STILL receive it (may take 2-5s as their WebSocket clients reconnect to the new primary via asyncio retry).
7. Record any tab that fails to receive — that's an app-side reconnect bug.

## Exit codes

| Code | Meaning | Operator action |
|---|---|---|
| 0 | PASS — promotion + pub/sub recovery + rejoin all clean | Record timestamp; flip 21αβ.H5 → ✅ |
| 1 | Prereqs missing (redis-cli, docker, jq) | Install missing tool; rerun |
| 2 | Pre-drill tracer never received (Sentinel cluster broken BEFORE drill) | DO NOT proceed with kill — fix Sentinel first |
| 3 | `docker stop` on primary container failed | Wrong target host (no Redis container locally), or Docker daemon unhealthy |
| 4 | Sentinel never promoted within 60s | Check Sentinel logs on all 3 sentinels — quorum may be lost. See "Recovery" |
| 5 | Post-promotion tracer never arrived (CRITICAL) | Pub/sub did NOT recover — file a P0 and check application's `redis_config.get_redis_url` + Sentinel discovery in `app/services/websocket_manager.py` |
| 6 | Killed node did not rejoin as replica within 120s | Check container health; may need `docker restart` + manual `SLAVEOF <new-primary>` |

## Recovery

### Sentinel never promoted (exit 4)

```sh
# Inspect all 3 sentinels' view
for h in redis-sentinel-1 redis-sentinel-2 redis-sentinel-3; do
    echo "=== $h ==="
    redis-cli -h $h -p 26379 SENTINEL masters
done

# If 2/3 sentinels agree on the primary, manually force failover:
redis-cli -h redis-sentinel-1 -p 26379 SENTINEL FAILOVER mymaster
```

### Pub/sub did NOT recover (exit 5) — CRITICAL

This is the regression signal we built this drill to catch. The application's WebSocket manager reconnects to Sentinel after a primary change, but if the reconnect logic is broken, every inbox message is lost.

Capture full Sentinel logs + app logs immediately + file a P0. Don't rerun the drill until the root cause is identified.

### Killed node did not rejoin (exit 6)

```sh
# Force-restart the container
docker restart $PRIMARY_CID

# If Sentinel still doesn't see it as a replica:
redis-cli -h <killed-node-ip> -p 6379 SLAVEOF <new-primary-host> <new-primary-port>

# Then verify
redis-cli -h redis-sentinel-1 -p 26379 SENTINEL replicas mymaster
```

## Post-drill checklist

- [ ] Drill report attached to workflow run
- [ ] All 3 Redis nodes healthy (1 new primary + 2 replicas including the previously-killed node)
- [ ] No alpha-soak Sentry alerts fired during the drill
- [ ] WebSocket Phase B smoke completed (3 tabs all received post-promotion message)
- [ ] DAILY-LOG.md entry added
- [ ] PROGRESS.md row 21αβ.H5 flipped ⏳ → ✅

## What this drill does NOT cover

- **Split-brain** — needs network partition simulation; out of scope
- **Sentinel quorum loss** — killing 2/3 sentinels at once; separate drill
- **WebSocket client-side reconnect logic** — covered by Phase B smoke (operator-driven). If Phase A passes but Phase B fails, that's an app bug, not a Sentinel bug.
- **Cross-region failover** — V2 is single-region today

## Related

- `scripts/redis_sentinel_failover_drill.sh` — the drill itself
- `.github/workflows/redis-sentinel-failover-drill.yml` — workflow_dispatch entry point
- `app/services/websocket_manager.py` — application-side Sentinel client + reconnect logic
- `app/redis_config.py` — Swarm-secret + env URL resolution
- Phase 0 cluster setup: `docker-compose/redis-cluster.yml`
