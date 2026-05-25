# ---------------------------------------------------------------------------
# redis_client.py — Sentinel-aware async Redis lifespan wiring.
#
# ⭐ START HERE: this module exposes ONE async lifecycle pair —
# `init_redis()` + `close_redis()` — ONE accessor — `get_redis()` — ONE
# readiness probe — `check_redis_reachable()` — and ONE production-
# safety gate — `verify_production_sentinel_or_die()`. The FastAPI
# lifespan in `app/main.py` wires them. The /health/ready route in
# `app/health_routes.py` calls `check_redis_reachable()` on every
# readiness probe.
#
# WHY DUAL-PATH (Sentinel vs single-primary)
# Production (per CONSTRAINTS C11): Redis runs as a 1-primary +
# 2-replicas + 3-sentinels topology on rishi-4/5/6. Clients MUST go
# through Sentinel to discover the current primary so a failover
# doesn't strand them on a demoted replica.
# Local dev / docker-compose / CI: a single redis container is fine;
# Sentinel quorum would be CI burden without a probe target. The
# `redis_sentinel_enabled` flag (Settings) picks the path; the
# Sentinel-aware client's `master_for(...)` falls back to a stable
# direct-host connection when sentinels can't reach quorum.
#
# WHY LIFESPAN-SINGLETON (NOT PER-PROBE / PER-REQUEST)
# Mirror of orchestrator's PR #136 pattern (`yral-rishi-agent-
# conversation-turn-orchestrator/app/idempotency.py:248-422`). The
# Sentinel client + the underlying TCP connection pool are non-trivial
# to construct; building one per request burns the asyncio event loop.
# Building one per probe (the older public-api pattern) was acceptable
# when Redis wasn't a hot-path dep; it isn't the right baseline for
# spawned services that DO use Redis (idempotency keys, feature flags,
# session state, etc.).
#
# WHY DEPLOYED-FAIL-CLOSED (`verify_production_sentinel_or_die`)
# Codex PR #97 round-5 ITEM 6 + Session 4's PR #96 round-4 + PR #151
# round-5 BLOCKER 1: a DEPLOYED service (production OR staging — both
# share the HA Redis Sentinel infrastructure on rishi-4/5/6 per F4 +
# C11) with `redis_sentinel_enabled=False` would silently fall back
# to single-primary Redis — a C11 violation that loses failover
# safety. The gate raises RuntimeError at app startup if the
# misconfiguration is detected. Local dev (`environment="local"`)
# and any non-deployed env can still use single-primary; the gate
# only fires for `environment in {"production", "staging"}`. The
# function NAME is `verify_production_...` for historical reasons
# (the gate originally covered only production); the contract now
# covers the full set of deployed environments per the docstring +
# body.
#
# WHY PASSWORD= ON master_for() (PR #136 fix verbatim)
# The v2 cluster's Redis primary runs with `--requirepass` enabled
# (per H3). Sentinel discovery succeeds, but the first command after
# `master_for(...)` raises `AuthenticationError: Authentication
# required.` unless we send the AUTH frame. The `password=` kwarg
# carries the REDIS_PASSWORD secret value (Swarm-mounted in
# production, empty in local-dev where the local redis container
# has no `requirepass` set). The `or None` guard normalises empty-
# string → None so redis-py skips the AUTH frame entirely on the
# local path (some redis-py versions treat password="" differently
# from password=None — None is the unambiguous "skip AUTH" signal).
#
# WHY check_redis_reachable() RUNS A REAL PING
# `_redis is not None` only proves init ran; the Sentinel client is
# lazy and doesn't open connections to the primary until you send a
# command. /health/ready needs to verify Redis is ACTUALLY reachable,
# not just that the client object exists. Running `PING` inside a
# `wait_for(timeout=0.2)` proves end-to-end: discover primary, open
# TCP, send PING, receive PONG. Failure modes (timeout, connection
# refused, auth fail, Sentinel master-discovery error) all surface
# as False → /health/ready 503. DEP-014's spawn-smoke step 5b
# specifically catches misconfigured connection strings here.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib asyncio — wait_for enforces the 200ms timeout on the
# readiness probe's PING so a slow Redis can't stall the event loop.
import asyncio

# stdlib logging — module-level logger for init / close / probe-
# failure lines; routes through app/logging.py's H6-aware pipeline.
import logging

# stdlib pathlib — locates shared-config.yaml at the service folder
# root (two directories up from this file). Used by the Sentinel
# path to read sentinel_master_name + sentinel_hosts per C7.
import pathlib

# redis.asyncio — async Redis client. `Redis.from_url` for the
# single-primary fallback path (laptop / docker-compose / CI).
import redis.asyncio as redis_asyncio

# Sentinel — async Sentinel-aware client for the C11-compliant
# production path. `master_for(...)` returns a client that
# re-resolves the current primary on every command.
from redis.asyncio.sentinel import Sentinel

# PyYAML — reads the `redis:` section of shared-config.yaml so the
# Sentinel master name + sentinel host:port pairs come from the C7
# single source of truth (populated by Session 1's cluster bootstrap).
import yaml

# Settings singleton — exposes `redis_url`, `redis_password`,
# `redis_sentinel_enabled`, `environment` (production gate input).
from app.config import get_settings


# Module-level Redis client singleton. None before init + after
# close; loud RuntimeError on accidental out-of-lifecycle access.
_redis: redis_asyncio.Redis | None = None


# Readiness probe timeout is configurable via Settings — see
# `app/config.py`'s `health_ready_probe_timeout_seconds` field.
# Codex PR #151 round-4 BLOCKER moved this out of a per-module
# `_READINESS_PROBE_TIMEOUT_SECONDS = 0.2` constant + into the shared
# Settings model per C7 ("timeouts + thresholds are
# configurable/shared, not magic constants in code"). The same field
# governs the asyncpg SELECT 1 probe in `app/database.py` so both
# /health/ready sub-probes share one budget.


_log = logging.getLogger("app.redis_client")


# ===========================================================================
# Public functions — B7 priority order: entry-point boot gate → lifespan
# startup → lifespan shutdown → accessor → readiness probe. The private
# support function `_load_redis_section_from_shared_config` lives BELOW
# these per B7's "entry points first, supporting functions after" rule
# (Codex PR #151 round-1 BLOCKER 1).
# ===========================================================================


def verify_production_sentinel_or_die() -> None:
    """Refuse to start if production env runs without C11 Sentinel.

    WHAT: at app construction time, reads `settings.environment` +
          `settings.redis_sentinel_enabled`. If env is in the
          DEPLOYED-ENVIRONMENTS set (production OR staging — per F4
          + C11, both run on the shared HA Redis Sentinel
          infrastructure on rishi-4/5/6) AND sentinel_enabled is
          False, logs CRITICAL + raises RuntimeError — refusing to
          boot a deployed service that would silently fall back to
          single-primary Redis.
    WHEN: called once from `app/main.py` BEFORE the FastAPI app starts
          serving requests (right before / right after init_redis).
          Idempotent — safe to call multiple times.
    WHY:  Codex PR #97 round-5 ITEM 6 + Session 4's PR #96 round-4 +
          PR #151 round-5 BLOCKER 1 pattern. Production AND STAGING
          MUST use Sentinel for C11 compliance + failover safety.
          F4 + C11 say staging shares the HA Redis Sentinel
          infrastructure — same risk as production if sentinel is
          disabled. A misconfigured deploy that slipped through
          with the flag OFF would silently degrade to single-
          primary; this gate makes that combination LOUD +
          impossible to ship rather than a quiet C11 violation.
          Local dev (`environment="local"`) + any non-deployed env
          are still allowed to fall back — the LOUD warning in
          init_redis is the dev-time signal.

    Raises:
        RuntimeError when `environment` is in {"production",
        "staging"} + `redis_sentinel_enabled` is False. Raising
        RuntimeError (vs sys.exit) lets the FastAPI lifespan's
        try/except propagate cleanly + lets tests assert on the
        exception class without monkey-patching sys.exit. uvicorn
        still aborts startup because the lifespan startup hook
        raised.
    """
    settings = get_settings()

    # Set of environments where the v2 cluster's shared HA Redis
    # Sentinel infrastructure is the deployment target per F4 + C11.
    # Module-local (not Settings) because the set is a deployment-
    # topology fact, not a per-service-configurable knob.
    deployed_environments_requiring_sentinel = {"production", "staging"}

    if (
        settings.environment in deployed_environments_requiring_sentinel
        and not settings.redis_sentinel_enabled
    ):
        _log.critical(
            "c11_violation_deployed_environment_requires_sentinel",
            extra={
                "environment": settings.environment,
                "redis_sentinel_enabled": settings.redis_sentinel_enabled,
                "deployed_environments_requiring_sentinel": sorted(
                    deployed_environments_requiring_sentinel
                ),
                "remediation": (
                    "set REDIS_SENTINEL_ENABLED=true in the deploy's "
                    "Swarm env injection + confirm shared-config.yaml's "
                    "redis.sentinel_master_name + redis.sentinel_hosts "
                    "are populated by the cluster bootstrap"
                ),
            },
        )
        raise RuntimeError(
            f"REDIS_SENTINEL_ENABLED must be true in "
            f"environment={settings.environment} (F4/C11: shared HA "
            f"Redis Sentinel on rishi-4/5/6 is the only supported "
            f"production-grade Redis topology for "
            f"{sorted(deployed_environments_requiring_sentinel)} "
            f"environments). Refusing to start with single-primary "
            f"fallback."
        )


async def init_redis() -> None:
    """Open the async Redis client (Sentinel-aware or single-primary).

    WHAT: builds either a Sentinel-aware client (Sentinel discovers
          the current primary at command time + reconnects on
          failover) OR a single-primary client from `redis_url`.
          Stores it in module-level `_redis`. Idempotent.
    WHEN: called from the FastAPI lifespan startup hook in `app/main.py`
          BEFORE any request handler runs.
    WHY:  central init means every callsite sees the same pooled
          client + clean teardown via `close_redis()` on SIGTERM.
          The flag-gated dual-path keeps laptop dev working
          (single-primary) while production exercises the C11
          Sentinel path.

    Raises:
        RuntimeError when sentinel-enabled but shared-config.yaml's
        `redis:` section is missing the master name or hosts.
        RuntimeError when sentinel-DISABLED but `redis_url` is empty.
    """
    global _redis

    if _redis is not None:
        # Idempotent no-op — helpful for tests that exercise lifespan
        # repeatedly.
        _log.debug("init_redis called but already initialised; skipping")
        return

    settings = get_settings()

    if settings.redis_sentinel_enabled:
        # ---------------------------------------------------------------
        # C11-compliant Sentinel path (production)
        # ---------------------------------------------------------------
        redis_section = _load_redis_section_from_shared_config()
        master_name = redis_section.get("sentinel_master_name", "")
        raw_hosts = redis_section.get("sentinel_hosts", [])

        if not master_name or not raw_hosts:
            raise RuntimeError(
                "redis_sentinel_enabled=True but shared-config.yaml's "
                "`redis.sentinel_master_name` or `redis.sentinel_hosts` "
                "is empty. Populate from Session 1's cluster bootstrap "
                "before flipping the flag on."
            )

        # Sentinel expects [(host, port), ...] tuples; the YAML stores
        # each entry as `{host: ..., port: ...}` for readability.
        sentinel_targets = [
            (entry["host"], int(entry["port"]))
            for entry in raw_hosts
        ]

        # Tight socket timeout on Sentinel discovery itself — a slow
        # Sentinel shouldn't block startup forever.
        sentinel_client = Sentinel(
            sentinel_targets,
            socket_timeout=0.5,
            decode_responses=True,
        )

        # `master_for(...)` returns a Redis client that re-resolves
        # the current primary on every command (no stale-primary bug
        # after failover).
        #
        # `password=` carries the AUTH credential sent in response to
        # the primary's `--requirepass` AUTH challenge — see file
        # header WHY block. `or None` normalises empty-string →
        # None so local-dev (no password) skips the AUTH frame.
        _redis = sentinel_client.master_for(
            master_name,
            decode_responses=True,
            password=settings.redis_password or None,
        )
        _log.info(
            "redis_client_initialised_via_sentinel",
            extra={
                "master_name": master_name,
                "sentinel_count": len(sentinel_targets),
            },
        )
        return

    # ---------------------------------------------------------------
    # Single-primary fallback path (laptop dev / docker-compose / CI)
    # ---------------------------------------------------------------
    url = settings.redis_url
    if not url:
        raise RuntimeError(
            "redis_url is empty AND redis_sentinel_enabled is False — "
            "set REDIS_URL or flip redis_sentinel_enabled=True."
        )

    # `from_url` parses the scheme/host/port/password from the URL.
    # If the URL embeds `:password@`, redis-py sends AUTH automatically.
    # If REDIS_PASSWORD is configured separately, pass it explicitly so
    # the operator can use a clean URL + secret-managed password
    # rather than embedding the password in the URL (better for the
    # /health/ready and logs).
    _redis = redis_asyncio.Redis.from_url(
        url,
        decode_responses=True,
        password=settings.redis_password or None,
    )

    # LOUD warning so the C11 gap is visible in startup logs whenever
    # this fallback path is taken. CI / prod MUST run with the
    # Sentinel flag ON; this warning is the dev-time signal.
    _log.warning(
        "c11_violation_single_primary_redis_no_sentinel",
        extra={
            "url_scheme": url.split("://", 1)[0],
            "remediation": (
                "set redis_sentinel_enabled=True + ensure shared-config.yaml "
                "redis.sentinel_master_name + sentinel_hosts are populated "
                "from the cluster bootstrap"
            ),
        },
    )


async def close_redis() -> None:
    """Close the async Redis client cleanly.

    WHAT: awaits `_redis.aclose()` to flush pending commands + tear
          down the connection pool, then sets `_redis = None`.
    WHEN: called from the FastAPI lifespan shutdown hook on SIGTERM.
    WHY:  uncleaned Redis connections persist on the server side
          until their idle timeout; clean shutdown == faster Swarm
          rolling updates.
    """
    global _redis

    if _redis is None:
        return

    await _redis.aclose()
    _redis = None
    _log.info("redis_client_closed")


def get_redis() -> redis_asyncio.Redis:
    """Return the initialised async Redis client.

    WHAT: returns the module-level `_redis`. Raises if init hasn't run.
    WHEN: called from every code path that reads or writes Redis.
    WHY:  central accessor — a future refactor that swaps the client
          implementation (e.g., ACL-aware per-service client) only
          touches `init_redis` + `get_redis`. Loud RuntimeError on
          misuse beats a silent NoneType crash.
    """
    if _redis is None:
        raise RuntimeError(
            "redis client not initialised — was init_redis() called "
            "from app lifespan startup? Tests that bypass lifespan must "
            "call init_redis() in setup or monkeypatch the singleton."
        )
    return _redis


async def check_redis_reachable() -> bool:
    """Probe whether Redis is actually reachable via the client.

    WHAT: sends `PING` through the initialised client + waits for
          `PONG`. Wrapped in `asyncio.wait_for(timeout=0.2)`.
          Returns True on `PONG`, False on ANY failure (init not
          run, timeout, connection refused, AUTH error, Sentinel
          master-discovery failure).
    WHEN: invoked by `/health/ready` in `app/health_routes.py` on
          every readiness probe.
    WHY:  /health/ready uses this boolean to choose between the 200
          and 503 branches per F9. Failure paths log but don't
          raise — health probes must always answer.

    The probe is intentionally an end-to-end check (send PING →
    receive PONG), not just a `_redis is not None` check, because
    the client is lazy: init succeeds even if Redis is unreachable;
    the first command is what fails. A misconfigured REDIS_URL,
    wrong REDIS_PASSWORD, or unreachable Sentinel quorum is EXACTLY
    the regression class DEP-014's spawn-smoke step 5b catches.
    """
    if _redis is None:
        _log.warning("health_ready_redis_not_initialised")
        return False

    # Read the shared per-service timeout from Settings (C7 — no
    # magic constants; one source of truth in app/config.py's
    # `health_ready_probe_timeout_seconds` field). get_settings() is
    # lru_cached so this call is effectively free per process.
    settings = get_settings()
    probe_timeout_seconds = settings.health_ready_probe_timeout_seconds

    try:
        # `ping()` returns True (or the bytes b"PONG" depending on
        # decode_responses; both are truthy). We treat any truthy
        # response as success.
        ping_result = await asyncio.wait_for(
            _redis.ping(),
            timeout=probe_timeout_seconds,
        )
        return bool(ping_result)
    except asyncio.TimeoutError:
        _log.warning(
            "health_ready_redis_probe_timed_out",
            extra={"timeout_seconds": probe_timeout_seconds},
        )
        return False
    except Exception as exc:  # noqa: BLE001 — health probes never raise
        # Catch-all — same rationale as database.check_pool_reachable.
        _log.warning(
            "health_ready_redis_probe_failed",
            extra={"error_class": type(exc).__name__, "error_message": str(exc)},
        )
        return False


async def check_redis_round_trip_works() -> bool:
    """Deep-probe Redis via a SET / GET / DEL round-trip.

    WHAT: writes an ephemeral key with a 5-second TTL, reads it
          back, asserts the round-tripped value matches, deletes
          it. Wrapped in `asyncio.wait_for(timeout=health_deep_
          probe_timeout_seconds)`. Returns True on full-cycle
          success, False on ANY failure (init not run, timeout,
          mismatch, AUTH error, Sentinel master-discovery failure
          during write/read).
    WHEN: invoked by `/health/deep` in `app/health_routes.py` on
          every deep-probe request.
    WHY:  /health/deep is the F9 third tier — "real end-to-end
          round-trip" (vs /health/ready's "is the dep reachable").
          SET+GET+DEL exercises the WRITE path + READ path + DELETE
          path, all of which can fail independently (e.g. Sentinel
          discovered a replica as the primary mistakenly; the
          write succeeds against the replica but reads return
          stale; the delete operates on a different shard than the
          write; etc.). A `PING` probe wouldn't catch these.

          The ephemeral key uses a 5s TTL as a defense-in-depth
          guard: if DELETE fails silently, the key garbage-collects
          itself rather than leaking into the keyspace.

          Spawned services SHOULD override this with their own
          deep-probe (e.g. a pub/sub round-trip, a Redis Streams
          XADD+XREAD round-trip, or a per-service-prefix key
          round-trip) when their domain-specific Redis usage is
          wired. The template's default is the minimum non-trivial
          round-trip — replace per service when richer checks land.
    """
    if _redis is None:
        _log.warning("health_deep_redis_not_initialised")
        return False

    settings = get_settings()
    probe_timeout_seconds = settings.health_deep_probe_timeout_seconds

    # Ephemeral key + value. The key namespace `health-deep-probe:`
    # makes it obvious in `redis-cli KEYS` output what produced the
    # key. The unique-suffix `:probe` keeps the schema flat — no
    # date / process-id in the key, since the 5s TTL and the
    # immediate DELETE both garbage-collect cleanly.
    probe_key = "health-deep-probe:probe"
    probe_value = "ok"

    try:
        async def _round_trip_write_read_delete():
            # SET with EX (TTL in seconds) so a DELETE failure
            # below doesn't leak the key into the keyspace forever.
            await _redis.set(probe_key, probe_value, ex=5)
            roundtripped_value = await _redis.get(probe_key)
            await _redis.delete(probe_key)
            return roundtripped_value

        roundtripped_value = await asyncio.wait_for(
            _round_trip_write_read_delete(),
            timeout=probe_timeout_seconds,
        )
        # `decode_responses=True` (set in init_redis) means GET
        # returns a Python str (not bytes). The roundtripped value
        # must exactly match what we wrote — a mismatch would
        # surface a real Redis-consistency bug (Sentinel split-
        # brain, key-eviction during the probe, etc.).
        return roundtripped_value == probe_value
    except asyncio.TimeoutError:
        _log.warning(
            "health_deep_redis_round_trip_timed_out",
            extra={"timeout_seconds": probe_timeout_seconds},
        )
        return False
    except Exception as exc:  # noqa: BLE001 — health probes never raise
        _log.warning(
            "health_deep_redis_round_trip_failed",
            extra={"error_class": type(exc).__name__, "error_message": str(exc)},
        )
        return False


# ===========================================================================
# Private supporting functions — placed AFTER the public surface per B7
# priority order (Codex PR #151 round-1 BLOCKER 1). Python's
# function-definition-at-module-load semantics make this ordering safe:
# `init_redis()` calls `_load_redis_section_from_shared_config()` at
# RUNTIME (inside the Sentinel branch), by which point the module is
# fully loaded + the support function is bound. Lexical order in the
# file is independent of call order.
# ===========================================================================


def _load_redis_section_from_shared_config() -> dict:
    """Read the `redis:` section of `shared-config.yaml` at service root.

    WHAT: opens `shared-config.yaml` (two directories up from this
          file — i.e., the spawned service's own copy of the file),
          parses it, returns the `redis:` mapping.
    WHEN: called from `init_redis()` when the Sentinel path is taken.
    WHY:  C7 says "shared values live in shared-config.yaml" — the
          Sentinel master name + the sentinel host:port pairs come
          from there, NOT from env vars duplicated across services.
          Session 1 populates the values at cluster-bootstrap time.

    Raises:
        RuntimeError if the file is missing or the `redis:` section
        is not a mapping (i.e., it's a list or scalar — syntax error
        in the YAML).
    """
    config_path = (
        pathlib.Path(__file__).resolve().parent.parent / "shared-config.yaml"
    )
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    redis_section = data.get("redis", {})
    if not isinstance(redis_section, dict):
        raise RuntimeError(
            "shared-config.yaml `redis:` section is not a mapping; "
            "got: " + repr(redis_section),
        )
    return redis_section


# ===========================================================================
# RELATED FILES:
#   main.py                       — lifespan startup:
#                                     verify_production_sentinel_or_die();
#                                     await init_redis()
#                                   lifespan shutdown:
#                                     await close_redis()
#   health_routes.py              — /health/ready calls check_redis_reachable()
#   database.py                   — sibling module: same lifespan-singleton
#                                   pattern for asyncpg
#   config.py                     — Settings model exposes redis_url,
#                                   redis_password, redis_sentinel_enabled,
#                                   environment
#   shared-config.yaml            — `redis:` section read on Sentinel path
#                                   (sentinel_master_name + sentinel_hosts)
#   secrets.yaml.template         — declares REDIS_PASSWORD secret
#   docker-compose.yml            — local-dev: single redis container,
#                                   redis_sentinel_enabled=false default
#   pyproject.toml                — declares redis dependency
# ===========================================================================
