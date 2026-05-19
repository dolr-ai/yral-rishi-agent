# ---------------------------------------------------------------------------
# health_routes.py — /health/{live,ready,deep} probes per F9.
#
# ⭐ START HERE: three health endpoints docker / Swarm / Caddy / Uptime
# Kuma probe to know if the service is healthy. /health/live returns
# raw 200; /health/ready does a real Sentinel-aware Redis ping
# (Codex PR #97 round-4); /health/deep returns envelope-shaped 503
# until Day-5+ wires a real end-to-end round-trip (F9-honest fallback).
#
# THE THREE TIERS PER CONSTRAINTS F9:
#   GET /health/live   — process is alive (cheap; never touches deps).
#                        Returns raw {"status": "ok", "service": "..."}.
#   GET /health/ready  — required dependencies are reachable. Pings
#                        Redis via the C11 Sentinel-aware async client
#                        (when redis_sentinel_enabled=True) OR the
#                        single-primary fallback (when False + LOUD
#                        startup-warning fires). 200 envelope on
#                        success, 503 envelope on failure. 200ms
#                        per-call timeout so health probes fail fast
#                        rather than block the asyncio event loop.
#   GET /health/deep   — real end-to-end round-trip. Same F9-honest
#                        503-with-explanation fallback per Codex
#                        BLOCKER 5 round-2.
#
# WHY ASYNC + SENTINEL (Codex PR #97 round-4 BLOCKER 2 resolution)
# Round-2 wired a sync `redis.Redis(...).ping()` inside the async
# /health/ready handler. Round-3 Codex flagged the event-loop blocking
# + the C11 violation (plain URL bypasses Sentinel). Round-3 coordinator
# preference: ship a 503-fallback stub + raise DEP-006 against Session 1.
# Round-4 turned the page: coordinator + Session 4 + Session 3 verified
# Session 1's cluster bootstrap ALREADY declared the Sentinel config
# in `shared-config.yaml` — DEP-006 self-resolved. So this round wires
# the real `redis.asyncio.sentinel.Sentinel` client per C11, mirroring
# Session 4's PR #96 round-3 pattern (commit fe40fcb).
#
# WHY PER-PROBE CLIENT (not lifespan singleton)
# Session 4's orchestrator uses a lifespan-managed Sentinel singleton
# because the orchestrator has a hot-path Redis dependency on every
# request. This service (Day-2 scope) does NOT yet — Redis is only
# pinged from the health endpoint. Building the Sentinel client
# per-probe is fine: Sentinel construction is cheap (no I/O until you
# call a command); probes are rare (Swarm hits /health/ready every
# few seconds); per-probe construction avoids touching `app/main.py`'s
# lifespan in this PR. When PR #101 lands (JWKS cache) + PR #103
# (idempotency cache), THAT PR will wire a lifespan-managed singleton
# the way Session 4 does, and this file can swap its per-probe
# construction for `from app.redis_client import get_redis` in a
# 1-line follow-up.
#
# WHY 200ms TIMEOUT (not 1s / not the redis-py default)
# Health probes MUST fail fast. A blocked probe stalls the asyncio
# event loop = stalls every concurrent request = breaches E1's
# latency budget on every Redis hiccup. 200ms catches "Redis is
# slow" without inviting cascade.
#
# WHY ENVELOPE ON 503 (but raw on 200 path)
# Per F9 the 200 contract is the cheap `{"status": "ok"}` shape so
# orchestrators (docker / Swarm / Caddy) parse without understanding
# our envelope. On the 503 path, mobile + the on-call dashboard DO
# see the body, so envelope-shape it so the same tools that parse
# mobile responses parse the failure body too.
#
# WHY shared-config.yaml READ INSIDE _check_redis_reachable (not at
# module import)
# Module-import-time read would fail tests that don't have the YAML
# file in the test cwd. Per-call read is slow (~1ms file IO) but
# happens only during health probes (rare). The follow-up lifespan-
# singleton PR (when Redis becomes a hot-path dep) will cache the
# parsed config; for this PR the per-call cost is acceptable.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib asyncio — used for `asyncio.wait_for` to enforce the 200ms
# per-probe timeout around the Redis ping call.
import asyncio

# stdlib logging — emits the LOUD warning on the C11-fallback path
# and the per-probe debug trail. The H6 PII-allowlist redactor in
# app/logging.py keeps these fields safe.
import logging

# stdlib pathlib — locates shared-config.yaml at the service-folder
# root (two directories up from this file).
import pathlib

# fastapi — APIRouter groups the three health routes; routes return
# either raw dict (200) or JSONResponse with envelope (503).
from fastapi import APIRouter

# JSONResponse — used for the 503 envelope-shaped error body so mobile
# + the on-call dashboard can pattern-match the failure.
from fastapi.responses import JSONResponse

# redis.asyncio — the async Redis client used for the single-primary
# fallback path (laptop dev when redis_sentinel_enabled is False).
import redis.asyncio as redis_asyncio

# Sentinel client — the C11-compliant async Sentinel-aware client used
# when redis_sentinel_enabled is True. Discovers the current primary
# at connect time + reconnects on failover.
from redis.asyncio.sentinel import Sentinel

# PyYAML — reads the `redis:` section of `shared-config.yaml` so the
# Sentinel master name + sentinel hosts come from the C7 single source
# of truth Session 1's cluster bootstrap populates.
import yaml

# Error helper + status map — used for the 503 envelope body so the
# locked error-codes table governs the shape mobile sees.
from app.api.errors import HTTP_STATUS_FOR_ERROR_CODE, error_response

# Settings singleton — exposes `redis_sentinel_enabled` flag +
# `redis_url` fallback URL the health probe consults each call.
from app.config import get_settings


# Module-level logger so the LOUD C11-fallback warning + per-probe
# debug lines route through the H6-aware structured-logging pipeline.
_log = logging.getLogger(__name__)


# Tight per-call timeout for the health-probe ping. 200ms per Codex
# PR #97 round-4 BLOCKER 2 — fail fast rather than stall the event
# loop on Redis hiccups.
_HEALTH_PROBE_TIMEOUT_SECONDS = 0.2


# Router for the health endpoints. NO prefix — health probes need to
# be at `/health/*` per F9, not nested under `/api/v1/`.
health_router = APIRouter(tags=["health"])


def _load_redis_section_from_shared_config() -> dict:
    """Read the `redis:` section of `shared-config.yaml` at service root.

    WHAT: opens `shared-config.yaml` (two directories up from this
          file — i.e., `yral-rishi-agent-public-api/shared-config.yaml`),
          parses it, returns the `redis:` mapping.
    WHEN: called from `_check_redis_reachable()` on every readiness
          probe when `redis_sentinel_enabled=True`. The per-call cost
          (~1ms file IO) is acceptable because probes are rare (Swarm
          hits us every few seconds, not per request).
    WHY:  C7 says "shared values live in shared-config.yaml" — the
          Sentinel master name + the 3 sentinel host:port pairs come
          from there, NOT from env vars or hardcode.
    """
    config_path = (
        pathlib.Path(__file__).resolve().parent.parent.parent / "shared-config.yaml"
    )
    with config_path.open() as handle:
        data = yaml.safe_load(handle) or {}
    redis_section = data.get("redis", {})
    if not isinstance(redis_section, dict):
        raise RuntimeError(
            "shared-config.yaml `redis:` section is not a mapping; "
            "got: " + repr(redis_section),
        )
    return redis_section


def verify_production_sentinel_or_die() -> None:
    """Refuse to start if production env runs without C11 Sentinel (R5 ITEM 6).

    WHAT: at app construction time, reads `settings.environment` +
          `settings.redis_sentinel_enabled`. If env=="production" AND
          sentinel_enabled is False, logs CRITICAL +
          `sys.exit(1)` — refusing to boot a production service that
          would silently fall back to single-primary Redis.
    WHEN: called once from `app/main.py` after the routers are wired
          but before the FastAPI app starts serving. Idempotent — safe
          to call multiple times (logs + exits on every call when the
          violation holds).
    WHY:  Codex PR #97 round-5 ITEM 6 — mirrors Session 4's PR #96
          round-4 pattern. Production MUST use Sentinel for C11
          compliance + failover safety. A misconfigured deploy that
          slipped through with the flag OFF would silently degrade
          to single-primary; this check makes that combination LOUD
          + impossible to ship rather than a quiet C11 violation.
          Local dev (`environment="local"`) is still allowed to fall
          back to single-primary — the C11-fallback LOUD warning in
          `_check_redis_reachable` is the dev-time signal there.

    Raises:
        SystemExit(1) when env=="production" + sentinel_enabled=False.
    """
    import sys

    settings = get_settings()
    if settings.environment == "production" and not settings.redis_sentinel_enabled:
        _log.critical(
            (
                "C11 violation: production environment requires Redis "
                "Sentinel; set REDIS_SENTINEL_ENABLED=true OR fix "
                "shared-config.yaml."
            ),
            extra={
                "environment": settings.environment,
                "redis_sentinel_enabled": settings.redis_sentinel_enabled,
                "remediation": (
                    "set REDIS_SENTINEL_ENABLED=true in the production "
                    "Swarm env injection + confirm shared-config.yaml's "
                    "redis.sentinel_master_name + redis.sentinel_hosts "
                    "are populated by the cluster bootstrap"
                ),
            },
        )
        sys.exit(1)


async def _check_redis_reachable() -> bool:
    """Ping Redis (Sentinel-aware or single-primary fallback).

    WHAT: builds either a Sentinel-aware async client (when
          `redis_sentinel_enabled=True` + shared-config has the
          Sentinel master + hosts) OR a single-primary async client
          from `redis_url` (laptop dev / docker-compose / CI). Calls
          .ping() wrapped in asyncio.wait_for(timeout=0.2) so a slow
          / down Redis returns False fast without stalling the event
          loop. Returns True on successful ping, False on any failure
          (timeout, connection refused, auth fail, Sentinel master-
          discovery error, etc.).
    WHEN: invoked by `health_ready()` on every readiness probe.
    WHY:  /health/ready uses this boolean to choose between the
          200-envelope and 503-envelope branches. Failure paths log
          but don't raise — health probes must always answer.

    On the fallback path (redis_sentinel_enabled=False), emits a
    LOUD startup warning `c11_violation_single_primary_redis_no_sentinel`
    on every probe so the C11 gap is visible in logs (per Codex
    BLOCKER 2 + Session 4's mirrored pattern).
    """
    settings = get_settings()

    if settings.redis_sentinel_enabled:
        # C11-compliant Sentinel path.
        try:
            redis_section = _load_redis_section_from_shared_config()
        except Exception as exc:  # noqa: BLE001 — never crash the probe
            _log.warning(
                "health_ready_sentinel_config_load_failed",
                extra={"error_class": type(exc).__name__, "error_message": str(exc)},
            )
            return False

        master_name = redis_section.get("sentinel_master_name", "")
        raw_hosts = redis_section.get("sentinel_hosts", [])
        if not master_name or not raw_hosts:
            _log.warning(
                "health_ready_sentinel_config_incomplete",
                extra={
                    "master_name_present": bool(master_name),
                    "hosts_count": len(raw_hosts) if isinstance(raw_hosts, list) else 0,
                },
            )
            return False

        # Sentinel expects [(host, port), ...] tuples; the YAML stores
        # each entry as {host: ..., port: ...} for readability.
        try:
            sentinel_targets = [
                (entry["host"], int(entry["port"])) for entry in raw_hosts
            ]
        except (KeyError, ValueError, TypeError) as exc:
            _log.warning(
                "health_ready_sentinel_hosts_malformed",
                extra={"error_class": type(exc).__name__, "error_message": str(exc)},
            )
            return False

        sentinel_client = Sentinel(
            sentinel_targets,
            socket_timeout=_HEALTH_PROBE_TIMEOUT_SECONDS,
        )
        try:
            # `master_for` returns a Redis client that re-resolves the
            # current primary on every command (no stale-primary bug
            # after Sentinel failover).
            primary_client = sentinel_client.master_for(
                master_name,
                socket_timeout=_HEALTH_PROBE_TIMEOUT_SECONDS,
            )
            await asyncio.wait_for(
                primary_client.ping(), timeout=_HEALTH_PROBE_TIMEOUT_SECONDS,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — any error means NOT reachable
            _log.warning(
                "health_ready_sentinel_ping_failed",
                extra={"error_class": type(exc).__name__, "error_message": str(exc)},
            )
            return False

    # Single-primary fallback path (laptop dev / docker-compose / CI).
    # LOUD warning so the C11 gap is visible whenever this branch fires.
    # Per-probe (not init-time) because there's no lifespan init here;
    # the noise is acceptable since probes are rare + the warning is
    # the operator-side signal to flip the flag.
    _log.warning(
        "c11_violation_single_primary_redis_no_sentinel",
        extra={
            "url_scheme": settings.redis_url.split("://", 1)[0]
            if "://" in settings.redis_url
            else "unknown",
            "remediation": (
                "set redis_sentinel_enabled=True + ensure shared-config.yaml "
                "redis.sentinel_master_name + sentinel_hosts are populated"
            ),
        },
    )

    try:
        client = redis_asyncio.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=_HEALTH_PROBE_TIMEOUT_SECONDS,
            socket_timeout=_HEALTH_PROBE_TIMEOUT_SECONDS,
        )
        await asyncio.wait_for(
            client.ping(), timeout=_HEALTH_PROBE_TIMEOUT_SECONDS,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — any error means NOT reachable
        _log.warning(
            "health_ready_single_primary_ping_failed",
            extra={"error_class": type(exc).__name__, "error_message": str(exc)},
        )
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
    summary="Are required dependencies reachable? (real C11 Sentinel-aware Redis ping)",
)
async def health_ready() -> JSONResponse:
    """Readiness probe — real Sentinel-aware Redis ping (BLOCKER 2 R4).

    WHAT: awaits `_check_redis_reachable()`; on True returns 200 +
          raw `{"status": "ok", "dependencies": {"redis": "ok"}}`;
          on False returns 503 + envelope-shaped error body with
          `dependencies.redis = "unreachable"`.
    WHEN: Swarm uses this to gate rolling-update health (per I2 +
          deploy.yml smoke gates). Caddy `health_uri /health/ready`
          (per C10) reads this to decide upstream-up vs upstream-down.
          Uptime Kuma probes this for availability dashboards (per D5).
    WHY:  Codex PR #97 round-4 BLOCKER 2 resolution — the round-3
          F9-honest 503 stub is replaced with the real async-Sentinel-
          aware check now that Session 1's cluster bootstrap declared
          the Sentinel config (master_name + sentinel_hosts) in
          shared-config.yaml. Day-5 cluster deploy can now pass the
          I2 health gate.
    """
    if await _check_redis_reachable():
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "dependencies": {"redis": "ok"}},
        )

    body = error_response(
        "service_unavailable",
        (
            "Redis dependency unreachable; service NOT ready to serve "
            "traffic. Check the service logs for the Sentinel-ping "
            "warning lines (health_ready_sentinel_ping_failed or "
            "health_ready_single_primary_ping_failed) to see the "
            "specific failure mode."
        ),
        data={
            "status": "unavailable",
            "dependencies": {"redis": "unreachable"},
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
#   ../config.py             — defines redis_sentinel_enabled + redis_url
#   ../api/errors.py         — error_response + HTTP_STATUS_FOR_ERROR_CODE
#   ../../shared-config.yaml — `redis:` section (sentinel_master_name +
#                              sentinel_hosts) read by
#                              `_load_redis_section_from_shared_config()`
#   ../../tests/contract/test_health_routes.py
#                            — asserts the 200 dict / 503 envelope shapes;
#                              monkey-patches `_check_redis_reachable` for
#                              the 200 path + `redis_asyncio.Redis.from_url`
#                              for the 503 path
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/00-api-contract.md
#                            — lists the three /health endpoints
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                            — F9 (three-tier health split),
#                              I2 (canary deploy + auto-rollback on health failure),
#                              C10 (Caddy health_uri probe),
#                              C11 (Redis Sentinel HA),
#                              D5 (Uptime Kuma probes /health/ready)
#   yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/cross-session-dependencies.md
#                            — DEP-005 (template `/health/*` mirror) +
#                              DEP-006 (NOW RESOLVED — Session 1's
#                              cluster bootstrap already declared the
#                              Sentinel config; round-3 raised this
#                              DEP based on a stale read)
# ===========================================================================
