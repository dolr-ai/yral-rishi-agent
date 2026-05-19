# ---------------------------------------------------------------------------
# health_routes.py — /health/{live,ready,deep} probes per F9.
#
# ⭐ START HERE: three health endpoints docker / Swarm / Caddy / Uptime
# Kuma probe to know if the service is healthy. They DO NOT use the
# ApiResponse envelope on the happy path (per F9 the orchestrators
# expect a cheap `{"status": "ok"}` shape); the error path DOES use the
# envelope so the failure body is parseable by the same on-call dashboard
# tooling that reads mobile responses.
#
# THE THREE TIERS PER CONSTRAINTS F9:
#   GET /health/live   — process is alive (cheap; never touches deps)
#   GET /health/ready  — required dependencies are reachable. Codex
#                        PR #97 BLOCKER 5 wired the Redis check: pings
#                        the Redis the JWKS cache (PR #101) + idempotency
#                        cache (PR #103) depend on. 503 envelope on
#                        unreachable.
#   GET /health/deep   — real end-to-end round-trip. BLOCKER 5 fallback:
#                        returns 503 envelope with explanatory body
#                        until Day 5+ wires a real handler-path round-
#                        trip (e.g., a synthetic /test/whoami call).
#                        F9-honest per the blocker's "default to
#                        503-with-explanation" option.
#
# ⚠️ WHY THESE LIVE IN THE SPAWNED COPY (not the template)?
# Session 2's template ships everything ELSE per F8 but does NOT yet
# include health endpoints — Codex flagged this as a B7 / F9 gap on
# PR #94 (Session 3 Day 1). Coordinator queued DEP-005 to Session 2 to
# mirror these handlers in the template so all 13 v2 services get them
# by default. Until that template fix flows through + every service
# re-spawns or back-fills, this file is a LOCAL BRIDGE in
# yral-rishi-agent-public-api/ so my service can boot in the v2 cluster
# without failing the Swarm health-check (which would cause auto-rollback
# per I2 the first time the Day-5 deploy runs).
#
# WHY redis-py DIRECTLY (not via app/redis_client.py)?
# app/redis_client.py is a PR #101 (Day 4A) artifact. This fixup lands
# on the Day-2 branch so the file doesn't exist here yet. Using the
# `redis` library directly with a fresh client per probe is fine:
# health checks are infrequent + we WANT a fresh connection so a stale
# pool doesn't mask a real outage. After PR #101 lands + the stacked-
# branch rebase, this file can switch to redis_client.get_redis() OR
# stay as-is (the contract is identical from the prober's perspective).
#
# WHY THE ENVELOPE ON THE 503 PATH (but raw on the 200 path)?
# Per F9 the 200 contract is `{"status": "ok"}` for cheap parsing by
# orchestrators that don't speak our envelope. On the 503 path, mobile
# does see it (when Caddy returns the upstream 503 verbatim) and parses
# the envelope. So: 200 = cheap raw shape; 503 = envelope.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# fastapi — APIRouter groups the three health routes; the routes return
# either raw dict (200) or JSONResponse with envelope (503).
from fastapi import APIRouter

# JSONResponse — used for the 503 envelope-shaped error body so mobile
# + the on-call dashboard can pattern-match the failure.
from fastapi.responses import JSONResponse

# redis — directly used for the BLOCKER 5 readiness check (no wrapper
# yet on the Day-2 branch; PR #101 adds the singleton wrapper). Health
# checks WANT a fresh client per probe so a stale pool doesn't mask
# real outages.
import redis as redis_lib

# Config singleton — exposes the redis_url BLOCKER 5 forward-ported
# to Day-2 + the rest of the settings the health probes need.
from app.config import get_settings

# Error helper + status map — used for the 503 envelope body so the
# locked error-codes table governs the shape mobile sees.
from app.api.errors import HTTP_STATUS_FOR_ERROR_CODE, error_response


# Router for the health endpoints. NO prefix — health probes need to
# be at `/health/*` per F9, not nested under `/api/v1/`.
health_router = APIRouter(tags=["health"])


def _check_redis_reachable() -> bool:
    """Best-effort Redis ping for the readiness probe (BLOCKER 5).

    WHAT: builds a fresh redis-py client pointed at settings.redis_url
          with tight 1s connect + read timeouts, then calls .ping()
          which raises on any failure. Returns True only when ping
          succeeds; False on connection refused, timeout, auth fail,
          or any other Redis error.
    WHEN: called by health_ready() on every readiness probe (docker /
          Swarm / Caddy / Uptime Kuma).
    WHY:  factored out so the test suite can monkey-patch it deterministic-
          ally (test_health_routes.py mocks this to True / False to
          exercise both success + failure paths). Without isolation,
          tests would hit localhost:6379 + flake based on whether a
          dev has a real Redis running.
    """
    settings = get_settings()
    try:
        client = redis_lib.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        return bool(client.ping())
    except Exception:  # noqa: BLE001 — any Redis-side error means NOT ready
        return False


@health_router.get(
    "/health/live",
    summary="Is the process alive? (cheap, never touches deps)",
)
async def health_live() -> dict[str, str]:
    """Liveness probe.

    WHAT: returns {"status": "ok", "service": "yral-rishi-agent-public-api"}
          as long as the FastAPI app is up. NEVER touches deps so an
          out-of-DB / out-of-Redis service still survives liveness.
    WHEN: docker / Swarm hit this to know whether the container PID is
          still responsive — if NOT, Swarm restarts the container.
    WHY:  the cheapest possible "the process exists" signal. Service
          identity in the response helps the on-call grep across nodes
          for "which container is this" when scaling >1 replica.
    """
    return {"status": "ok", "service": "yral-rishi-agent-public-api"}


@health_router.get(
    "/health/ready",
    summary="Are required dependencies reachable?",
)
async def health_ready():  # noqa: ANN201 — return type varies (200 dict vs 503 JSONResponse)
    """Readiness probe — checks Redis is reachable (BLOCKER 5).

    WHAT: pings Redis via _check_redis_reachable(); on success returns
          {"status": "ok", "deps": {"redis": "ok"}}; on failure returns
          envelope-shaped 503 with error="service_unavailable".
    WHEN: Swarm uses this to gate rolling-update health (per I2 +
          deploy.yml smoke gates). Caddy `health_uri /health/ready`
          (per C10) reads this to decide upstream-up vs upstream-down.
          Uptime Kuma probes this for availability dashboards (per D5).
    WHY:  before BLOCKER 5 this returned 200 unconditionally — a
          broken Redis would NOT trip rolling-update health, masking
          real outages. Now a Redis outage flips this 503 + Swarm
          rolls back; correctness over performance.

    Future deps (Postgres pool when Day-N wires asyncpg, orchestrator
    when Day-4 wires httpx) layer in by extending _check_* helpers +
    aggregating the boolean results.
    """
    if not _check_redis_reachable():
        body = error_response(
            "service_unavailable",
            "Redis dependency unreachable; service NOT ready to serve traffic.",
        ).model_dump()
        return JSONResponse(
            status_code=HTTP_STATUS_FOR_ERROR_CODE["service_unavailable"],
            content=body,
        )
    return {"status": "ok", "deps": {"redis": "ok"}}


@health_router.get(
    "/health/deep",
    summary="Real end-to-end round-trip (NOT implemented — 503 per BLOCKER 5)",
)
async def health_deep() -> JSONResponse:
    """Deep probe — BLOCKER 5 503-with-explanation fallback.

    WHAT: returns envelope-shaped 503 with error="service_unavailable"
          + msg explaining the deep check isn't wired yet. Day-5+ will
          replace this with a real round-trip (e.g., a no-auth synthetic
          call against /test/whoami or a stamp through the full chat
          handler path).
    WHEN: the H9 synthetic-user heartbeat hits this every 5 min on prod
          to catch silent dependency degradation.
    WHY:  per the BLOCKER 5 fallback: "default to 503-with-explanation
          if you don't want to wire the real round-trip in this fixup;
          that's still F9-honest." Returning 200 unconditionally (the
          Day-2 original) was misleading — the on-call dashboard
          treated everything as healthy. 503 here loudly signals the
          gap until Day-5+ wires the real check.
    """
    body = error_response(
        "service_unavailable",
        (
            "Deep health check not yet implemented — Day-5+ will wire a real "
            "end-to-end round-trip through one handler path. Returning 503 "
            "per F9-honest BLOCKER 5 default so the on-call dashboard "
            "doesn't see a misleading 200."
        ),
    ).model_dump()
    return JSONResponse(
        status_code=HTTP_STATUS_FOR_ERROR_CODE["service_unavailable"],
        content=body,
    )


# ===========================================================================
# RELATED FILES:
#   ../main.py               — wires health_router into the FastAPI app
#   ../config.py             — defines redis_url (BLOCKER 5 forward-port)
#   ../api/errors.py         — error_response + HTTP_STATUS_FOR_ERROR_CODE
#   ../../tests/contract/test_health_routes.py
#                            — asserts the 200 dict / 503 envelope shapes;
#                              monkey-patches _check_redis_reachable for
#                              deterministic test behavior
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — lists the three /health endpoints
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                            — F9 (three-tier health split),
#                              I2 (canary deploy + auto-rollback on health failure),
#                              C10 (Caddy health_uri probe),
#                              C11 (Redis Sentinel HA),
#                              D5 (Uptime Kuma probes /health/ready)
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md
#                            — DEP-005 raised to Session 2: mirror these
#                              handlers in the template so every spawned
#                              service gets them
# ===========================================================================
