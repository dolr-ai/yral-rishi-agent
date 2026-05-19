# ---------------------------------------------------------------------------
# idempotency.py — F10 default-on idempotency wiring for POST /v1/turn.
#
# ⭐ START HERE: this module exposes ONE async lifecycle pair —
# `init_redis()` / `close_redis()` — plus two helpers consumed by the
# run_turn handler:
#
#   - `compute_idempotency_key(user_id, idempotency_key)` → Redis key str
#   - `get_cached_response(key)` → MessageResponse JSON dict | None
#   - `cache_response(key, response_payload)` → None  (24h TTL)
#
# The FastAPI lifespan in `app/main.py` calls `init_redis()` at startup
# and `close_redis()` on SIGTERM; the run_turn handler calls the two
# read/write helpers around its happy-path stub response.
#
# WHY F10 — DEFAULT-ON IDEMPOTENCY ON EVERY NON-GET ENDPOINT
# Per CONSTRAINTS F10 verbatim: "Idempotency-key default-on on all
# non-GET endpoints; dedupes via Redis 24hr TTL. Per-endpoint opt-out
# for truly stateless." `POST /v1/turn` is the orchestrator's single
# non-GET endpoint today; it MUST honour F10 from day 1. Codex PR-#96
# review caught the original implementation accepting the
# X-Idempotency-Key header but never reading or writing Redis around it
# — that's the fix this module ships.
#
# WHY THE KEY IS USER-SCOPED
# Per the Day-2 directive's fixup guidance: scope by `user_id` so two
# different users with the SAME client-generated key never collide
# (mobile clients commonly generate keys from a content hash; without
# user-scoping a popular phrase would dedupe across users).
#
# Key shape (verbatim): `idempotency:orchestrator:run-turn:{user_id}:{idempotency_key}`
# 24h TTL per F10.
#
# WHY redis.asyncio (NOT redis-py sync)
# Per F12 the runtime stack is asyncio-native. Sync redis-py inside an
# async handler would block the event loop on every cache check — the
# composer-side latency budget per E1 is well under 100ms p95, sync
# Redis adds ~1-3ms blocking time per call which would dominate the
# pure-Python stub path.
#
# WHY MODULE-LEVEL SINGLETON (mirrors `app/db.py` pattern in soul-file-
# library)
# `redis.asyncio.Redis` connections are pooled internally; building one
# pool at app startup + reusing across requests is the documented
# pattern. The singleton is `None` before init + after close so any
# out-of-lifecycle access raises a clear error rather than silently
# using a stale handle.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

# stdlib JSON serialiser — used to encode MessageResponse payloads
# before writing to Redis + decode them back on cache hit. JSON keeps
# the wire shape byte-identical to what FastAPI would have serialised
# itself (so a replay-from-cache response is byte-equal to a fresh one).
import json

# stdlib logger — emits structured fields the H6 PII-allowlist redactor
# in `app/logging.py` knows about (idempotency_hit / reason / etc.).
# We log key METADATA (hit / miss / client_provided_key), never the
# cached payload itself.
import logging

# `Final` lets us mark module-level constants as immutable to type
# checkers; both the 24h TTL and the key-prefix string are locked.
from typing import Final

# `redis.asyncio.Redis` is the async Redis client. The async path
# matches our FastAPI / asyncio runtime stack per F12.
import redis.asyncio as redis_asyncio

# `get_settings()` reads the typed Settings singleton; we need
# `redis_url` declared in `app/config.py` to build the connection.
from app.config import get_settings


# Module-level Redis singleton — populated by `init_redis()` at app
# startup and consumed via `get_redis()`. `None` before init / after
# close so any out-of-lifecycle access fails fast.
_redis: redis_asyncio.Redis | None = None


# 24 hours in seconds. Locked per F10 verbatim ("dedupes via Redis 24hr
# TTL"). Changing requires a CONSTRAINTS amendment.
_IDEMPOTENCY_TTL_SECONDS: Final[int] = 24 * 60 * 60


# Key prefix shape — `idempotency:orchestrator:run-turn:{user_id}:{key}`.
# Stored as a format string so the per-request `compute_*` helper is the
# one place this shape can drift.
_KEY_PREFIX: Final[str] = "idempotency:orchestrator:run-turn"


_log = logging.getLogger("app.idempotency")


# ===========================================================================
# Lifecycle
# ===========================================================================


async def init_redis() -> None:
    """Open the async Redis connection. Idempotent — safe to call once.

    WHAT: builds a `redis.asyncio.Redis.from_url(...)` instance pointed
          at `settings.redis_url`; stores it in the module-level
          `_redis` variable.
    WHEN: called from the FastAPI lifespan startup hook in `app/main.py`
          BEFORE any request handler runs.
    WHY:  central init means every callsite sees the same pooled client
          + we can teardown cleanly via `close_redis()` on SIGTERM.

    Raises:
        ValueError when `redis_url` is empty (config-load already
        validated, so this is belt-and-suspenders).
    """
    global _redis

    if _redis is not None:
        # Already initialised — idempotent no-op. Helpful for tests
        # that spin the lifespan up + down multiple times.
        _log.debug("init_redis called but already initialised; skipping")
        return

    settings = get_settings()
    url = settings.redis_url

    if not url:
        raise ValueError(
            "redis_url is empty — set the REDIS_URL env var (or accept "
            "the docker-compose default) before starting the orchestrator."
        )

    # `decode_responses=True` makes the client return Python `str` from
    # GETs instead of `bytes`. Our cached payloads are JSON strings;
    # decoding at the client boundary keeps the run_turn handler code
    # `json.loads` on a real str.
    _redis = redis_asyncio.Redis.from_url(url, decode_responses=True)
    _log.info("redis client initialised", extra={"url_scheme": url.split("://", 1)[0]})


async def close_redis() -> None:
    """Close the Redis client cleanly.

    WHAT: awaits `_redis.aclose()` to flush pending commands + tear down
          the connection pool, then sets `_redis = None`.
    WHEN: called from the FastAPI lifespan shutdown hook on SIGTERM
          (Swarm rolling update, scale-down, manual stop).
    WHY:  uncleaned Redis connections persist on the server side until
          their idle timeout; clean shutdown == faster Swarm rolls.
    """
    global _redis

    if _redis is None:
        return

    await _redis.aclose()
    _redis = None
    _log.info("redis client closed")


def get_redis() -> redis_asyncio.Redis:
    """Return the initialised async Redis client.

    WHAT: returns the module-level `_redis`. Raises if init hasn't run.
    WHEN: called from `get_cached_response` + `cache_response` (any code
          path doing a cache lookup or write).
    WHY:  central accessor lets a future refactor swap implementations
          (e.g. Sentinel-aware client per C11) without touching
          callsites.
    """
    if _redis is None:
        raise RuntimeError(
            "redis client is not initialised — call `init_redis()` in the "
            "FastAPI lifespan startup hook before any request handler."
        )
    return _redis


# ===========================================================================
# Key construction
# ===========================================================================


def compute_idempotency_key(user_id: str, idempotency_key: str) -> str:
    """Return the fully-qualified Redis key for this user+key pair.

    WHAT: formats `idempotency:orchestrator:run-turn:{user_id}:{key}`.
    WHEN: called once per request by the run_turn handler.
    WHY:  one mapping point — if the key shape needs to change (e.g.
          add a region prefix), this is the only file to edit.
    """
    return f"{_KEY_PREFIX}:{user_id}:{idempotency_key}"


# ===========================================================================
# Cache read / write
# ===========================================================================


async def get_cached_response(key: str) -> dict | None:
    """Return the cached MessageResponse payload if any, else None.

    WHAT: GET the Redis key; parse the value as JSON; return the dict.
          `None` on cache miss OR on JSON-decode failure (corrupt cache
          entry — we let the next call repopulate it).
    WHEN: called at the top of the run_turn handler, before any work.
    WHY:  cache HIT = no further LLM call (Day-5+) + byte-identical
          response replay. Cache MISS = process normally + write through.
    """
    cached_str = await get_redis().get(key)
    if cached_str is None:
        return None

    try:
        return json.loads(cached_str)
    except json.JSONDecodeError:
        # Corrupt entry — treat as miss + let the next call repopulate.
        # Don't raise; falling through to fresh processing is the safe
        # behaviour. Log so we can alert if this happens in volume.
        _log.warning(
            "idempotency_cache_corrupt",
            extra={"key_suffix": key.rsplit(":", 1)[-1]},
        )
        return None


async def cache_response(key: str, response_payload: dict) -> None:
    """Write the MessageResponse payload to Redis under `key` with 24h TTL.

    WHAT: SET the Redis key to `json.dumps(response_payload)` with
          EX=86400.
    WHEN: called from the run_turn handler AFTER a successful (200)
          processing path, before returning.
    WHY:  F10 verbatim — "dedupes via Redis 24hr TTL". Same payload
          replays on every subsequent same-key call in the 24h window.

    Args:
        key: the fully-qualified Redis key (use `compute_idempotency_key`).
        response_payload: the serialisable MessageResponse dict.
    """
    # `default=str` handles any non-JSON-native type (Pydantic models'
    # `.model_dump()` already returns plain dicts but a future field
    # added without thinking might be a datetime; default=str keeps the
    # write path resilient).
    serialised = json.dumps(response_payload, default=str)

    # `EX=` sets TTL in seconds. `set` returns True on success — we
    # don't need to check because Redis errors raise.
    await get_redis().set(key, serialised, ex=_IDEMPOTENCY_TTL_SECONDS)


# ===========================================================================
# RELATED FILES:
#   config.py                 — `redis_url` setting
#   main.py                   — init_redis() / close_redis() in lifespan
#   run_turn.py               — consumer (read on entry, write on success)
#   models/turn.py            — MessageResponse the cache stores
#   ../../tests/test_run_turn.py
#                            — two-call replay test (F10 BLOCKER 1 fix
#                              gate from Codex PR-#96 review)
#   ../../../yral-rishi-agent-plan-and-discussions/multi-session-parallel-build-coordination/interface-contracts/01-internal-rpc-contracts.md
#                            — coordinator-owned contract reaffirming
#                              X-Idempotency-Key is REQUIRED day 1
# ===========================================================================
