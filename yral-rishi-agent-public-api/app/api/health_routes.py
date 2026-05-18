# ---------------------------------------------------------------------------
# health_routes.py — /health/{live,ready,deep} probes per F9.
#
# ⭐ START HERE: three minimal health endpoints docker / Swarm / uptime-
# kuma probe to know if the service is healthy. They DO NOT use the
# ApiResponse envelope (per F9 the orchestrators expect a simple
# `{"status": "ok"}` shape so they can parse cheaply without a strict
# schema).
#
# THE THREE TIERS PER CONSTRAINTS F9:
#   GET /health/live   — process is alive (cheap; never touches deps)
#   GET /health/ready  — dependencies are healthy (Swarm uses this to
#                        gate rolling-update health; Uptime Kuma probes
#                        this for service availability)
#   GET /health/deep   — real end-to-end round-trip (expensive; the
#                        synthetic-user heartbeat per H9 uses this)
#
# ⚠️ WHY THESE LIVE IN THE SPAWNED COPY (not the template)?
# Session 2's template ships everything ELSE per F8 but does NOT yet
# include health endpoints — Codex flagged this as a B7 / F9 gap on
# PR #94 (Session 3 Day 1). Coordinator queued a DEP to Session 2 to
# mirror these handlers in the template so all 13 v2 services get them
# by default. Until that template fix flows through + every service
# re-spawns or back-fills, this file is a LOCAL BRIDGE in
# yral-rishi-agent-public-api/ so my service can boot in the v2 cluster
# without failing the Swarm health-check (which would cause auto-rollback
# per I2 the first time the Day-5 deploy runs).
#
# WHEN SESSION 2 SHIPS THE TEMPLATE MIRROR: this file stays as a local
# override IF the template's version diverges; or gets deleted in favor
# of the template's version (per A1 relaxed — superseded artifact, 7-step
# safety check trivially passes since the import path stays identical).
#
# WHY THE READY / DEEP STUBS RETURN 200 TODAY (no real dep checks)?
# Day 2 doesn't have postgres or redis client wiring yet (Day 4+).
# Returning 200 NOW means the Swarm rolling-update health check passes
# during the Day 5 cluster deploy + the rishi-1/2 Caddy reverse-proxy
# `health_uri /health/ready` (per C10) succeeds. Once asyncpg + redis
# clients land, the ready / deep handlers will ACTUALLY check the deps
# and return 503 on failure — the API surface stays unchanged.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from fastapi import APIRouter

# Router for the health endpoints. NO prefix — health probes need to
# be at `/health/*` per F9, not nested under `/api/v1/`.
health_router = APIRouter(tags=["health"])


@health_router.get(
    "/health/live",
    summary="Is the process alive? (cheap, never touches deps)",
)
async def health_live() -> dict[str, str]:
    """Liveness probe.

    WHAT: returns {"status": "ok"} as long as the FastAPI app is up.
    WHEN: docker / Swarm hit this to know whether the container PID is
          still responsive — if NOT, Swarm restarts the container.
    WHY:  the cheapest possible "the process exists" signal. No deps.
    """
    return {"status": "ok"}


@health_router.get(
    "/health/ready",
    summary="Are dependencies healthy enough to serve requests?",
)
async def health_ready() -> dict[str, str]:
    """Readiness probe.

    WHAT: returns {"status": "ok"} when the service is ready to take
          traffic. At Day 2 this is unconditionally ok (no deps wired).
          Day 4+ this will return 503 if the postgres pool or redis
          client fail to connect.
    WHEN: Swarm uses this to gate rolling-update health (per I2 +
          deploy.yml smoke gates). Uptime Kuma probes this for
          availability dashboards (per D5).
    WHY:  the difference between live + ready is that a container can
          be alive but not yet ready (deps still initializing on cold
          start); ready means safe to send traffic.
    """
    return {"status": "ok"}


@health_router.get(
    "/health/deep",
    summary="Real end-to-end round-trip check (expensive)",
)
async def health_deep() -> dict[str, str]:
    """Deep probe.

    WHAT: returns {"status": "ok", "note": "deep check not yet
          implemented — Day-5+ wires this to real round-trip"} at Day 2.
          Day-5+ will do a real query against postgres + redis +
          (optionally) the orchestrator RPC.
    WHEN: the H9 synthetic-user heartbeat hits this every 5 min on prod
          to catch silent dependency degradation.
    WHY:  ready is "the deps look connected"; deep is "an actual
          end-to-end query worked." Catches silent regressions.
    """
    return {
        "status": "ok",
        "note": (
            "deep check not yet implemented — Day-5+ wires this to real round-trip"
        ),
    }


# ===========================================================================
# RELATED FILES:
#   ../main.py               — wires health_router into the FastAPI app
#   ../../tests/contract/test_health_routes.py
#                            — asserts the {"status": "ok"} shape
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — lists the three /health endpoints
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                            — F9 (three-tier health split),
#                              I2 (canary deploy + auto-rollback on health failure),
#                              D5 (Uptime Kuma probes /health/ready)
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md
#                            — DEP raised to Session 2: mirror these handlers
#                              in the template so every spawned service gets them
# ===========================================================================
