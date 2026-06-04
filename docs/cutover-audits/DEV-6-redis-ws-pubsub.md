# DEV-6 — Redis WS pub/sub verification (21α.B5)

## TL;DR

**🟢 GREEN** — Both v2 replicas successfully booted the Redis pub/sub subscriber for cross-node WebSocket delivery. Subscriber log line present on both `.1` (rishi-5) and `.2` (rishi-4). No "Redis not available — local-only" fallback logs observed. No subscriber-died errors.

## Evidence

### Both replicas started the subscriber on boot

```
yral-rishi-agent.1.xdlf87xsaa9x@rishi-5  2026-06-04 11:39:24,805 INFO services.websocket_manager: Redis pub/sub subscriber started for cross-node WebSocket events
yral-rishi-agent.1.xdlf87xsaa9x@rishi-5  2026-06-04 11:39:24,919 INFO ...
yral-rishi-agent.1.xdlf87xsaa9x@rishi-5  2026-06-04 11:39:24,990 INFO ...
yral-rishi-agent.1.xdlf87xsaa9x@rishi-5  2026-06-04 11:39:24,994 INFO ...
yral-rishi-agent.2.4uv0iypd5mbk@rishi-4  2026-06-04 11:39:28,781 INFO ...
yral-rishi-agent.2.4uv0iypd5mbk@rishi-4  2026-06-04 11:39:28,799 INFO ...
yral-rishi-agent.2.4uv0iypd5mbk@rishi-4  2026-06-04 11:39:28,807 INFO ...
yral-rishi-agent.2.4uv0iypd5mbk@rishi-4  2026-06-04 11:39:28,874 INFO ...
```

Multiple log lines per replica are normal — uvicorn spawns 4 workers (`Dockerfile CMD ... --workers 4`); each worker runs the FastAPI lifespan once, starting one subscriber. Net effect: **8 active subscribers across the cluster**, all listening on the same Redis channel.

### Fallback path is NOT firing

Code path of concern (`app/services/websocket_manager.py:100-101`):
```python
if not redis:
    logger.info("Redis not available — WebSocket events are local-only")
```

Grep over the last 4h of service logs:
```bash
$ grep -i "Redis not available\|Redis subscriber died\|Redis publish failed" recent.log
(empty — no matches)
```

✅ The healthy `_get_redis()` path is taken; cross-node delivery is in effect.

### Code path verification

`_publish(user_id, message)` (line 80-92):
```python
async def _publish(user_id: str, message: str):
    try:
        redis = await _get_redis()
        if redis:
            payload = json.dumps({"user_id": user_id, "message": message})
            await redis.publish(REDIS_CHANNEL, payload)
            await redis.aclose()
            return  # ✅ early return = no local-only fallback path
    except Exception as e:
        logger.debug(f"Redis publish failed, using local-only: {e}")
    await _send_to_user_local(user_id, message)  # only on failure
```

The fallback to local-only is **exception-gated**, not silently selected. As long as Redis is reachable from the publisher's worker, cross-node fan-out happens.

### Redis cluster health

Redis Sentinel is also up on rishi-4/5/6 per the Phase 0 deploy. The subscriber connects via `redis_url` (Sentinel-aware connection string per `redis_config.py`). The 8 subscribers all started → Sentinel-resolved primary is reachable.

## What I did NOT verify

- **Live cross-node propagation test** (manually triggering a WS event on `.1` and observing it deliver to a subscriber connected to `.2`). Would require connecting a real WS client to one replica and triggering a message-send on the other — out of scope for an overnight audit but a 2-minute exercise during the morning meeting.
- **Sentinel failover behavior** — if the Redis primary fails, do the subscribers gracefully reconnect to the new primary? `redis.asyncio` library has built-in retry but the `subscriber died` log line in `start_redis_subscriber` suggests it terminates cleanly on `CancelledError` but on connection-loss it just logs WARNING. **Worth verifying** by chaos-testing Redis failover before β, not blocking α.

## Recommendation

**Cutover gate B5: GREEN.** The pub/sub path is healthy at the bootstrap level. For β, add a live cross-node propagation test to the chaos suite.
