# ---------------------------------------------------------------------------
# health_routes.py — /health/{live,ready,deep} probes per F9.
#
# ⭐ START HERE: three health endpoints docker / Swarm / Caddy / Uptime
# Kuma probe to know if the service is healthy. /health/live returns
# raw 200; /health/ready + /health/deep return envelope-shaped 503
# until real dependency checks are wired (BLOCKER 2 + BLOCKER 5
# F9-honest fallback).
#
# THE THREE TIERS PER CONSTRAINTS F9:
#   GET /health/live   — process is alive (cheap; never touches deps).
#                        Returns raw {"status": "ok", "service": "..."}.
#   GET /health/ready  — required dependencies are reachable.
#                        Codex PR #97 round-3 BLOCKER 2 + coordinator
#                        preference: returns envelope-shaped 503 with
#                        "redis_check_not_yet_implemented" explanation
#                        until Session 1 ships Sentinel config (DEP-006).
#                        Switches to a real async Sentinel-aware Redis
#                        ping the moment shared-config declares the
#                        `redis_sentinel_service_name` + `redis_sentinel_hosts`
#                        fields.
#   GET /health/deep   — real end-to-end round-trip. Same F9-honest
#                        503-with-explanation fallback per Codex
#                        BLOCKER 5 + round-3 BLOCKER 2 — better to
#                        loudly signal "not implemented" than to
#                        falsely report healthy.
#
# WHY 503 INSTEAD OF A SYNC redis.ping()?
# Round-2 BLOCKER 5 wired a synchronous redis.Redis(...).ping() into
# the async readiness handler. Codex round-3 BLOCKER 2 flagged two
# problems in one:
#   (a) sync .ping() inside an async handler blocks the asyncio event
#       loop until Redis replies (up to 1s timeout) — every concurrent
#       request stalls behind it, breaching E1's latency budget on
#       outages.
#   (b) the plain redis:// URL bypassed C11 Sentinel-aware discovery.
# Coordinator preference (per the round-3 directive): take the 503
# fallback now, ship the real async-Sentinel check in a follow-up PR
# once Session 1's cluster-bootstrap declares the Sentinel hosts in
# shared-config.yaml. The coordinator's exact words: "Easier to land
# cleanly, easier to revert."
#
# WHY ENVELOPE ON 503 (but raw on 200 path)?
# Per F9 the 200 contract is the cheap `{"status": "ok"}` shape so
# orchestrators (docker / Swarm / Caddy) can parse without
# understanding our envelope. On the 503 path, mobile + the on-call
# dashboard DO see the body, so envelope-shape it so the same tools
# that parse mobile responses parse the failure body too.
#
# ⚠️ THE 503 ON /health/ready BLOCKS SWARM ROLLING-UPDATE.
# Caddy `health_uri /health/ready` (per C10) + Swarm rolling-update
# (per I2) treat 503 as "upstream down." Until DEP-006 lands + the
# real async check ships, deploying THIS service to the v2 cluster
# will fail the health gate + auto-rollback. That's intentionally
# F9-honest per coordinator preference — we'd rather block the
# deploy than ship a misleading 200. Day-5 cluster deploy is gated
# on the DEP-006 resolution path.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# fastapi — APIRouter groups the three health routes; the routes return
# either raw dict (200) or JSONResponse with envelope (503).
from fastapi import APIRouter

# JSONResponse — used for the 503 envelope-shaped error body so mobile
# + the on-call dashboard can pattern-match the failure.
from fastapi.responses import JSONResponse

# Error helper + status map — used for the 503 envelope body so the
# locked error-codes table governs the shape mobile sees.
from app.api.errors import HTTP_STATUS_FOR_ERROR_CODE, error_response


# Router for the health endpoints. NO prefix — health probes need to
# be at `/health/*` per F9, not nested under `/api/v1/`.
health_router = APIRouter(tags=["health"])


def _check_redis_reachable() -> bool:
    """Placeholder for the Sentinel-aware Redis check (DEP-006).

    WHAT: returns False unconditionally. The Sentinel-aware async
          implementation lands in a follow-up PR once Session 1's
          cluster-bootstrap declares the Sentinel hosts in
          shared-config.yaml's `redis_sentinel_service_name` +
          `redis_sentinel_hosts` fields. Until then, /health/ready
          returns 503 (F9-honest "not implemented yet" per coordinator
          preference).
    WHEN: invoked by health_ready() on every readiness probe.
    WHY:  factored out so the test suite can monkey-patch it to True
          for the eventual "happy path when real check lands" test +
          to False explicitly for the current "always 503" path. Keeps
          the test surface ready for the follow-up async-Sentinel PR
          without needing further test-infrastructure changes.
    """
    # Intentional stub — the real Sentinel-aware async check is
    # deferred per the Codex round-3 BLOCKER 2 + coordinator
    # preference. See file header.
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
    summary=(
        "Are required dependencies reachable? "
        "(503 until DEP-006 lands Sentinel-aware async check)"
    ),
)
async def health_ready() -> JSONResponse:
    """Readiness probe — 503 envelope until DEP-006 ships (BLOCKER 2).

    WHAT: calls _check_redis_reachable() (currently a False-returning
          stub); on the False branch returns envelope-shaped 503 with
          msg explaining why; on the eventual True branch returns
          raw `{"status": "ok", "dependencies": {"redis": "ok"}}`.
    WHEN: Swarm uses this to gate rolling-update health (per I2 +
          deploy.yml smoke gates). Caddy `health_uri /health/ready`
          (per C10) reads this to decide upstream-up vs upstream-down.
          Uptime Kuma probes this for availability dashboards (per D5).
    WHY:  Codex round-3 BLOCKER 2 + coordinator preference: ship the
          F9-honest 503 now (clean, revertable) + wire the real
          async-Sentinel check in a follow-up PR once Session 1's
          cluster-bootstrap declares the Sentinel hosts. Day-5 cluster
          deploy is gated on DEP-006 resolution.
    """
    if _check_redis_reachable():
        # Future-ready: when the real Sentinel-aware async check lands
        # and returns True, this is the happy-path response shape with
        # `dependencies` (English-spelled per Codex round-3 BLOCKER 3,
        # was `deps`).
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "dependencies": {"redis": "ok"}},
        )

    # Current default: 503 envelope with the F9-honest "not yet
    # implemented" explanation. The `data.dependencies` map names
    # each dep + its check state so future deps (Postgres, orchestrator)
    # can layer in without changing the envelope shape.
    body = error_response(
        "service_unavailable",
        (
            "Redis Sentinel-aware async readiness check not yet wired; "
            "awaiting DEP-006 (Session 1 to declare "
            "redis_sentinel_service_name + redis_sentinel_hosts in "
            "shared-config.yaml). /health/ready returns 503 until then "
            "per F9-honest fallback + coordinator preference."
        ),
        data={
            "dependencies": {
                "redis": "not_yet_implemented",
            },
        },
    ).model_dump()
    return JSONResponse(
        status_code=HTTP_STATUS_FOR_ERROR_CODE["service_unavailable"],
        content=body,
    )


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
#   ../api/errors.py         — error_response + HTTP_STATUS_FOR_ERROR_CODE
#   ../../tests/contract/test_health_routes.py
#                            — asserts the 200 dict / 503 envelope shapes;
#                              monkey-patches _check_redis_reachable to
#                              flip between F9-honest 503 + future happy-path
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — lists the three /health endpoints
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                            — F9 (three-tier health split),
#                              I2 (canary deploy + auto-rollback on health failure),
#                              C10 (Caddy health_uri probe),
#                              C11 (Redis Sentinel HA — informs the Sentinel-aware
#                                   async check the follow-up PR will wire),
#                              D5 (Uptime Kuma probes /health/ready)
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md
#                            — DEP-005 (template `/health/*` mirror) +
#                              DEP-006 (Session 1 Sentinel config in
#                              shared-config.yaml — the prerequisite for
#                              the real async Sentinel-aware readiness check)
# ===========================================================================
