# ---------------------------------------------------------------------------
# health_routes.py — /health/{live,ready,deep} probes per CONSTRAINTS F9.
#
# ⭐ START HERE: three health endpoints orchestrators (Docker / Swarm /
# Caddy / Uptime Kuma) probe to know if the spawned service is healthy.
# `/health/live` returns a raw 200 (process is alive; no dep touch).
# `/health/ready` performs a DUAL probe — asyncpg SELECT 1 + Redis
# PING in parallel — and returns 200 only when BOTH succeed; otherwise
# returns 503 with a reason payload detailing which dep failed.
# `/health/deep` performs the F9 third tier — a real end-to-end
# round-trip through both deps (SELECT NOW() + Redis SET/GET/DEL)
# in parallel. Same dual-probe envelope shape as /health/ready but
# with a heavier per-probe budget (1.0s vs 200ms) because deep
# checks do more work.
#
# THE THREE TIERS PER CONSTRAINTS F9:
#   GET /health/live   — process is alive. Cheap; never touches deps.
#                        Returns raw {"status": "ok"}. Suited for
#                        k8s-style liveness probes + local-dev cheap
#                        polling. Per F9, Swarm + Uptime Kuma DO NOT
#                        use /health/live — they both poll
#                        /health/ready instead (see below); /live
#                        exists as the cheap process-alive signal
#                        for orchestrators that distinguish liveness
#                        from readiness.
#   GET /health/ready  — required dependencies are reachable. Calls
#                        check_pool_reachable() + check_redis_reachable()
#                        IN PARALLEL via asyncio.gather, returns 200
#                        envelope on dual success, 503 envelope on any
#                        failure with which dep failed + why. 200ms
#                        per-probe timeout (enforced in the database +
#                        redis_client modules' check_*_reachable
#                        helpers) so health probes fail fast.
#   GET /health/deep   — full end-to-end round-trip per dep. Calls
#                        check_pool_round_trip_works() (SELECT NOW())
#                        + check_redis_round_trip_works() (SET / GET /
#                        DEL) IN PARALLEL. Same envelope shape as
#                        /health/ready. 1.0s per-probe timeout
#                        (configurable via Settings'
#                        health_deep_probe_timeout_seconds field).
#
# WHY /health/deep ADDED IN PR #151 ROUND-6 (BLOCKER 2)
# F9 requires the uniform three-tier split for EVERY service.
# Earlier template rounds shipped the two-tier (live + ready)
# baseline. Codex round-5 BLOCKER 2 caught that the third tier
# was missing. The template's default /health/deep is a /health/
# ready superset (deeper round-trip per dep) — SPAWNED SERVICES
# SHOULD OVERRIDE THIS with service-specific deeper checks (e.g.,
# LLM API connectivity, downstream service ping, full message-
# cycle round-trip, per-service-table query). The override
# pattern is: spawned service replaces this router's
# /health/deep handler with its own — same path, richer
# implementation. See app/health_routes.py docstring on
# `health_deep` for the override-recipe.
#
# WHY DUAL PROBE (NOT just live)
# CONSTRAINTS F9 + DEP-014 acceptance criterion: a spawned service
# must report ready=200 ONLY when both Postgres and Redis are
# connected. Previous template skeleton's stub /health/ready returned
# 200 unconditionally — that's the regression class DEP-014 closes,
# and the spawn-smoke gate's step 5b is the load-bearing check that
# fails the build when this probe misbehaves.
#
# WHY ASYNC PARALLEL (asyncio.gather, NOT sequential await)
# Two probes back-to-back would be 2× the per-probe budget worst
# case. `asyncio.gather` runs them concurrently so /health/ready's
# tail latency stays close to a single probe's. Both probes are
# capped at 200ms via their own asyncio.wait_for; the overall
# /health/ready response is bounded by the slower probe (~200ms
# worst case), not the sum.
#
# WHY SIMPLE {status, details} ENVELOPE (NOT the error-codes table)
# Template baseline: the simplest shape that names which dep failed
# without dragging the full mobile-facing error-codes envelope into
# every spawned service. Mobile-facing services (public-api) extend
# this to the full table when they wire their own error system.
# Operators (Uptime Kuma, Swarm logs, the on-call dashboard) can
# parse the simple form without understanding any service-specific
# code table.
#
# WHY include_in_schema=False ON BOTH ROUTES
# Health probes aren't part of the public API contract — they're
# infrastructure plumbing. Excluding them from /openapi.json keeps
# the spawned service's documented API surface clean (the
# DEP-014 spawn-smoke step 5 / step 5b probe them by URL directly,
# not via OpenAPI discovery).
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib asyncio — `gather` runs the two reachability probes in
# parallel so /health/ready's response time is bounded by the slower
# probe, not the sum.
import asyncio

# stdlib logging — module-level logger emits structured probe-failure
# lines through app/logging.py's pipeline.
import logging

# fastapi — APIRouter groups the two health routes; routes return
# either raw dict (200) or JSONResponse for the 503 envelope.
from fastapi import APIRouter

# JSONResponse — used for the 503 envelope-shaped body so operators
# (Uptime Kuma + Swarm logs + on-call dashboard) can pattern-match
# the failure payload.
from fastapi.responses import JSONResponse

# Pool readiness probe — runs SELECT 1 inside a 200ms wait_for.
# Pool deep probe — runs SELECT NOW() round-trip inside a 1.0s
# wait_for. Both return True on success, False on any failure.
# See `app/database.py` for the full WHY.
from app.database import check_pool_reachable, check_pool_round_trip_works

# Redis readiness probe — sends PING inside a 200ms wait_for.
# Redis deep probe — does a SET / GET / DEL round-trip inside a
# 1.0s wait_for. Both return True on success, False on any
# failure. See `app/redis_client.py` for the full WHY.
from app.redis_client import (
    check_redis_reachable,
    check_redis_round_trip_works,
)


# Module-level logger. Probe-failure structured logs surface here
# (timeout class, error class) so operators can triage from logs
# without re-running the probe manually.
_log = logging.getLogger("app.health_routes")


# Router for the health endpoints. NO prefix — F9 mandates health
# probes at `/health/*` (not under `/api/v1/`) so orchestrators can
# probe without service-version routing.
health_router = APIRouter(tags=["health"])


@health_router.get("/health/live", include_in_schema=False)
async def health_live() -> dict[str, str]:
    """Liveness probe — process is alive. Cheap; never touches deps.

    WHAT: returns `{"status": "ok"}` with HTTP 200, every time.
    WHEN: hit by k8s-style liveness probes (when v2 services run
          under k8s rather than Swarm) + local-dev cheap polling
          (curl loop during debugging). Per F9, Swarm + Uptime Kuma
          DO NOT use /health/live — they both poll /health/ready
          instead (which still proves the process is up AND has
          working deps). /live exists as the dedicated cheap
          process-alive signal for orchestrators that distinguish
          liveness from readiness.
    WHY:  separates "process is running" (live) from "process can
          serve" (ready). A service whose deps just went down should
          still be live — restarting it doesn't help if the dep is
          the actual problem; the orchestrator should keep it
          running and instead bring back the dep. /live is the
          process-existence signal that's safe to consult during
          dep-outage incidents without misleading the orchestrator
          into a restart cycle.
    """
    return {"status": "ok"}


@health_router.get(
    "/health/ready",
    include_in_schema=False,
    # `response_model=None` is REQUIRED because this handler returns
    # EITHER a plain dict (200 success) OR a JSONResponse (503
    # failure). FastAPI's default behavior is to build a response
    # model from the return-type annotation, but `dict | JSONResponse`
    # isn't a valid Pydantic field type (the JSONResponse half is a
    # Starlette Response subclass, not a serializable model). Without
    # this kwarg, FastAPI raises `Invalid args for response field`
    # at module-import time — the spawned service refuses to boot.
    response_model=None,
)
async def health_ready() -> dict | JSONResponse:
    """Readiness probe — Postgres + Redis are reachable.

    WHAT: runs check_pool_reachable() + check_redis_reachable() in
          parallel via asyncio.gather. Both return bool. Both must
          be True for HTTP 200 + {"status": "ok"} body. Any False
          returns HTTP 503 + {"status": "not_ready", "details": {
          "postgres": "ok"|"failed", "redis": "ok"|"failed"}} body
          so the operator sees WHICH dep is down without having to
          probe each one individually.
    WHEN: hit by Swarm's compose-level readiness check + Caddy's
          upstream-health probe + Uptime Kuma's deeper check.
          Also hit by `scripts/tests/test_spawn_smoke.sh` step 5b
          (DEP-014's load-bearing acceptance criterion) to verify
          spawned services' Postgres/Redis wiring is correct.
    WHY:  F9 readiness contract — "200 only when the service can
          actually serve requests." The spawned service can't serve
          if either dep is down OR misconfigured (wrong DB URL,
          wrong Redis password, etc.). The dual probe catches BOTH
          "dep is unreachable" AND "dep is misconfigured" — that's
          DEP-014's spawn-smoke step 5b regression class.
    """
    # Run both probes IN PARALLEL — total time bounded by the
    # slower probe (~200ms worst case), NOT the sum (~400ms). Both
    # probes have their own 200ms timeout via asyncio.wait_for in
    # their respective modules; check_*_reachable always returns
    # bool, never raises, so gather can't propagate exceptions
    # here.
    postgres_reachable, redis_reachable = await asyncio.gather(
        check_pool_reachable(),
        check_redis_reachable(),
    )

    if postgres_reachable and redis_reachable:
        # Dual success — F9 compliant 200 response. No details block
        # in the 200 case because there's nothing to report; the
        # operator only cares about details on failure.
        return {"status": "ok"}

    # ----------------------------------------------------------------
    # At least one dep is down — 503 with the details payload so the
    # operator sees WHICH dep failed without probing each one
    # individually.
    # ----------------------------------------------------------------
    details = {
        "postgres": "ok" if postgres_reachable else "failed",
        "redis": "ok" if redis_reachable else "failed",
    }

    # Structured log mirrors the response body so log searches by
    # `health_ready_failed` surface the same failure context an
    # operator hitting /health/ready manually would see.
    _log.warning(
        "health_ready_failed",
        extra={"details": details},
    )

    # Status 503 (Service Unavailable) is the F9 contract for
    # readiness failure. JSONResponse lets us control the status
    # code while still returning a JSON body (returning a dict from
    # the handler auto-200s, which would defeat the purpose).
    return JSONResponse(
        status_code=503,
        content={
            "status": "not_ready",
            "details": details,
        },
    )


@health_router.get(
    "/health/deep",
    include_in_schema=False,
    # Same dual-return-type response-model gotcha as /health/ready
    # — `dict | JSONResponse` is not a valid Pydantic field type,
    # so we tell FastAPI to skip response-model generation.
    response_model=None,
)
async def health_deep() -> dict | JSONResponse:
    """Deep probe — Postgres + Redis full end-to-end round-trip.

    WHAT: runs check_pool_round_trip_works() (asyncpg SELECT NOW())
          + check_redis_round_trip_works() (Redis SET / GET / DEL)
          in parallel via asyncio.gather. Both return bool. Both
          must be True for HTTP 200 + {"status": "ok"} body. Any
          False returns HTTP 503 + {"status": "deep_check_failed",
          "details": {"postgres": "ok"|"failed", "redis": "ok"|
          "failed"}} body so the operator sees WHICH dep failed
          its deep round-trip.
    WHEN: hit by Uptime Kuma's deeper probe + the on-call dashboard
          + during incident triage (operators run `curl /health/deep`
          manually to differentiate "dep reachable but write-path
          broken" from "dep just down"). NOT hit on every Swarm
          healthcheck — those use /health/live + /health/ready
          which are cheaper.
    WHY:  F9 mandates the three-tier split for every service.
          /health/ready proves connectivity (PING / SELECT 1);
          /health/deep proves END-TO-END (real write + real read,
          asserting the data round-trips). Catches regression
          classes /health/ready misses: Sentinel split-brain
          mistakenly pointing at a replica, asyncpg version
          regressions in result decoding, Redis key-eviction
          during the probe, etc.

    OVERRIDE RECIPE (for spawned services that need richer deep
    checks):
        Replace this route's handler in the spawned service.
        Same path (`/health/deep`), same `response_model=None` +
        `include_in_schema=False` decorator args, same envelope
        shape (200 `{status: ok}` / 503 `{status: deep_check_failed,
        details: {...}}`). Inside, do whatever service-specific
        deep check matters — examples:

          - public-api: full JWT round-trip via the JWKS cache
          - orchestrator: end-to-end /v1/turn round-trip in stub mode
          - soul-file-library: a per-row read+write against a
            test row in the service's own schema
          - LLM-consuming services: a tiny `gemini.generate_content
            ("ping")` round-trip to confirm API connectivity

        The template's default below is a /health/ready superset:
        same deps, just heavier round-trips. Replace per service
        when the domain-specific checks are wired.
    """
    # Same asyncio.gather pattern as /health/ready — parallel
    # probes bounded by the slower one, not the sum.
    postgres_round_trip_ok, redis_round_trip_ok = await asyncio.gather(
        check_pool_round_trip_works(),
        check_redis_round_trip_works(),
    )

    if postgres_round_trip_ok and redis_round_trip_ok:
        return {"status": "ok"}

    # 503 envelope mirrors /health/ready's shape with a distinct
    # `status` token (`deep_check_failed` vs `not_ready`) so the
    # operator can tell from the body which probe tier surfaced
    # the failure without checking the URL path.
    details = {
        "postgres": "ok" if postgres_round_trip_ok else "failed",
        "redis": "ok" if redis_round_trip_ok else "failed",
    }

    _log.warning(
        "health_deep_failed",
        extra={"details": details},
    )

    return JSONResponse(
        status_code=503,
        content={
            "status": "deep_check_failed",
            "details": details,
        },
    )


# ===========================================================================
# RELATED FILES:
#   main.py                       — mounts `health_router` on the FastAPI
#                                   app; calls verify_production_sentinel_or_die
#                                   + init_pool + init_redis at lifespan
#                                   startup so this probe has something to
#                                   probe AGAINST.
#   database.py                   — exposes check_pool_reachable() (SELECT 1
#                                   inside 200ms wait_for)
#   redis_client.py               — exposes check_redis_reachable() (PING
#                                   inside 200ms wait_for)
#   docker-compose.yml            — local-dev: spawned service + postgres
#                                   + pgbouncer + redis; service depends_on
#                                   the deps' healthchecks so /health/ready
#                                   returns 200 once the compose stack is
#                                   fully up.
#   ../scripts/tests/test_spawn_smoke.sh
#                                 — DEP-014's spawn-smoke step 5b probes
#                                   /health/ready returning 200 to verify
#                                   spawned services' Postgres/Redis
#                                   wiring (load-bearing gate per the
#                                   DEP-014 acceptance criterion).
# ===========================================================================
