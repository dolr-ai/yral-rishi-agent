# ---------------------------------------------------------------------------
# jwks_client.py — fetch + Redis-backed cache of auth.yral.com's JWKS.
#
# ⭐ START HERE: one public function, `get_signing_keys()`. Returns
# `{kid: PEM-encoded public key object}` suitable for
# `jwt.decode(..., key=keys[kid], algorithms=["RS256"])`. Cache lookup
# is Redis-shared per E9; on Redis errors the strict path fails closed
# (returns jwks_fetch_error) so legacy keeps answering.
#
# REDIS CACHE LAYOUT (per Day-4A directive):
#   key:   jwks:auth.yral.com:v1
#   value: the raw JWKS document bytes (JSON, exactly as auth.yral.com
#          returned them). NOT the parsed {kid: key_obj} dict — that
#          would require pickling RSA key objects, which is slow + a
#          security smell. Parsing happens AFTER the bytes come out of
#          Redis.
#   TTL:   3600s (1 hour) — E9 verbatim.
#
# WHY STORE THE RAW JSON (not the parsed dict)?
# Three wins:
#   1. Pickle-free — no `pickle.dumps(rsa_public_key_obj)` which would
#      be slow + a security trip wire (deserializing pickle from a
#      shared cache is a classic compromise vector).
#   2. Human-readable in Redis — `redis-cli get` shows the JSON, makes
#      debugging "why is strict failing for this user" trivial.
#   3. Multi-version-safe — if a future cryptography lib bumps changes
#      the in-memory key object's repr, the cache still works because
#      it stores the JWK spec form (universal), not the lib's
#      representation.
#
# WHY DAY-4A SWAPPED FROM IN-PROCESS TO REDIS?
# E9 verbatim: "cached in Redis 1hr TTL." Day-3 shipped in-process 6h
# on Rishi's directive; Day-4A reconciles to E9 per the coordinator
# follow-up. Trade-off: Redis-shared means ONE fetch per cluster per
# hour vs in-process per-replica 1 per 6h (3 replicas → 3/6h). Both
# tiny load; Redis-shared adds visibility (any operator can
# `redis-cli get jwks:auth.yral.com:v1` to see what's cached).
#
# WHY REDIS-DOWN MEANS STRICT FAILS CLOSED (not falls back to live JWKS
# fetch)?
# Per Day-4A directive: "On Redis unavailable: fail-closed for STRICT
# path only (return jwks_fetch_error reason) — legacy path is
# unaffected since it doesn't consult JWKS." This is conservative: if
# the cache is broken, our entire JWKS contract is broken; we'd rather
# refuse to claim strict-validated than silently bypass the layer that
# E9 mandates. Legacy still answers, so the request itself doesn't
# crash — only the shadow logging records the failure.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import json
from typing import Optional

import httpx
import redis as redis_lib
from jwt.algorithms import RSAAlgorithm

from app import redis_client as redis_client_module
from app.config import get_settings


# Late-binding accessor so test fixtures that monkey-patch
# `app.redis_client.get_redis` reach this module's lookup. (Doing
# `from app.redis_client import get_redis` would bind the name at
# import time and miss subsequent monkey-patches — classic Python
# import-binding gotcha caught in Day-4A test development.)
def _get_redis():
    return redis_client_module.get_redis()


# Cache key per the Day-4A directive. The `:v1` suffix futureproofs:
# if the cache value shape ever changes (e.g., we add metadata
# alongside the JWKS JSON), bumping to `:v2` invalidates every
# old-format entry without manual flushes.
_JWKS_CACHE_KEY = "jwks:auth.yral.com:v1"


class JwksFetchError(Exception):
    """Raised when JWKS retrieval / parse fails.

    WHAT: signals the JWKS document couldn't be retrieved from EITHER
          the Redis cache OR the upstream auth.yral.com endpoint, or
          couldn't be parsed.
    WHEN: raised by `get_signing_keys()` on Redis errors OR HTTP fetch
          failures OR JSON parse failures.
    WHY:  the strict validator catches this + reports
          `jwks_fetch_error` as the divergence reason so the shadow log
          shows the cache or auth server is unhealthy; legacy still
          answers (it doesn't consult JWKS) so the request isn't
          crashed.
    """


def _fetch_jwks_from_upstream() -> bytes:
    """Pull the raw JWKS document bytes from auth.yral.com.

    WHAT: sync HTTPS GET against settings.jwks_url; returns the
          response body bytes (the raw JWKS JSON, exactly as
          auth.yral.com served it).
    WHEN: called by `get_signing_keys()` on a Redis cache miss.
    WHY:  keeps the upstream fetch separate from the cache layer +
          the parse layer so each can be tested + mocked independently.
    """
    settings = get_settings()
    # 5-second timeout — JWKS endpoints respond in <100ms in practice;
    # 5s absorbs transient slowness without blocking the request handler.
    try:
        response = httpx.get(settings.jwks_url, timeout=5.0)
        response.raise_for_status()
        return response.content
    except httpx.HTTPError as exc:
        raise JwksFetchError(f"jwks upstream fetch failed: {exc}") from exc


def _parse_jwks_bytes(raw_bytes: bytes) -> dict[str, object]:
    """Parse JWKS JSON bytes into {kid: public_key_obj} dict.

    WHAT: json-decodes the bytes; iterates the `keys` array;
          converts each JWK to an RSA public key via
          PyJWT's RSAAlgorithm.from_jwk(); returns the dict.
    WHEN: called by `get_signing_keys()` on every call (cache hit OR
          miss) — parsing is cheap relative to a HTTP fetch + we want
          the parsed dict in memory for the strict validator's
          per-request lookup.
    WHY:  separating parse from fetch lets us cache the raw bytes (per
          the "store raw JSON" rationale in the file header) while
          handing strict the parsed dict.
    """
    try:
        document = json.loads(raw_bytes)
    except ValueError as exc:
        raise JwksFetchError(f"jwks json parse failed: {exc}") from exc

    keys_by_kid: dict[str, object] = {}
    for jwk in document.get("keys", []):
        kid = jwk.get("kid")
        if not kid:
            # Tokens reference keys by kid; without a kid, the key is
            # unreferenceable. Skip silently — matches how every other
            # JWKS-consuming library handles it.
            continue
        try:
            keys_by_kid[kid] = RSAAlgorithm.from_jwk(jwk)
        except Exception:  # noqa: BLE001 — JWK parse errors vary by lib version
            # A single broken key shouldn't poison the entire cache;
            # skip + continue. If ALL keys are broken, the strict
            # validator's per-kid lookup fails with unknown_kid downstream.
            continue

    return keys_by_kid


def _cache_get_raw() -> Optional[bytes]:
    """Read the raw JWKS bytes from Redis, or None on miss / failure.

    WHAT: GET `jwks:auth.yral.com:v1` from Redis. Returns the raw bytes
          on hit, None on miss. On Redis connection / timeout error,
          re-raises as JwksFetchError so the caller can fail strict-closed.
    WHEN: called by `get_signing_keys()` as the first cache step.
    WHY:  bytes-in / bytes-out keeps the cache layer dumb; the parse
          layer is the next step.
    """
    try:
        client = _get_redis()
        cached = client.get(_JWKS_CACHE_KEY)
        # redis-py returns None for cache miss + bytes for hit (we
        # construct the client with decode_responses=False).
        return cached
    except (redis_lib.RedisError, OSError) as exc:
        # Connection refused, timeout, auth failure, etc. — fail
        # strict-closed per the Day-4A directive. The strict validator
        # catches JwksFetchError + reports jwks_fetch_error as the
        # divergence reason; legacy still answers (it doesn't consult
        # JWKS) so the request handler doesn't crash.
        raise JwksFetchError(f"redis get failed: {exc}") from exc


def _cache_set_raw(raw_bytes: bytes) -> None:
    """Write the raw JWKS bytes to Redis with the configured TTL.

    WHAT: SET `jwks:auth.yral.com:v1` to `raw_bytes` with EX =
          settings.jwks_cache_ttl_seconds (default 3600 per E9).
    WHEN: called by `get_signing_keys()` on cache-miss after a
          successful upstream fetch.
    WHY:  populates the cache for the rest of the cluster + the next
          hour's requests.
    """
    try:
        client = _get_redis()
        settings = get_settings()
        client.set(_JWKS_CACHE_KEY, raw_bytes, ex=settings.jwks_cache_ttl_seconds)
    except (redis_lib.RedisError, OSError) as exc:
        # On cache-write failure: we successfully fetched the upstream
        # JWKS, so the CURRENT request can still strict-validate. But
        # future requests will hit the upstream again (cache will keep
        # missing). Per Day-4A's "fail-closed for STRICT path only" —
        # we raise so the strict path on the CURRENT request reports
        # jwks_fetch_error too, matching the cache-get semantics. Without
        # this, the current request would strict-pass + the next hour's
        # requests would hammer auth.yral.com — both bad.
        raise JwksFetchError(f"redis set failed: {exc}") from exc


def get_signing_keys() -> dict[str, object]:
    """Return the cached {kid: public_key} dict, fetching on miss.

    WHAT: tries Redis GET first; on hit, parses bytes + returns the
          dict. On miss, fetches from upstream auth.yral.com, sets the
          cache, parses + returns. On Redis error at either step or
          on upstream fetch error: raises JwksFetchError so strict
          fails closed per E9.
    WHEN: called by StrictJwtValidator.validate() on every request
          when the strict path runs.
    WHY:  single public entry point; the cache layer details are
          private to this module.
    """
    cached_bytes = _cache_get_raw()

    if cached_bytes is not None:
        # Cache hit — parse + return. Parsing failure here is
        # legitimately "the cached JSON is broken," which we treat as
        # a fetch error (the cache is poisoned; let the next request
        # re-fetch upstream after expiry).
        return _parse_jwks_bytes(cached_bytes)

    # Cache miss — fetch upstream + cache + parse.
    raw_bytes = _fetch_jwks_from_upstream()
    _cache_set_raw(raw_bytes)
    return _parse_jwks_bytes(raw_bytes)


def reset_cache_for_testing() -> None:
    """Test-only helper: delete the cached JWKS so the next call
    forces a re-fetch.

    WHAT: DELETE `jwks:auth.yral.com:v1` from Redis. Tolerates Redis
          errors silently — tests that mock Redis as unavailable don't
          want the reset itself to throw.
    WHEN: called from test fixtures before / after each case so cache
          state from a prior test doesn't leak.
    WHY:  test isolation; deterministic cache-state per test.
    """
    try:
        client = _get_redis()
        client.delete(_JWKS_CACHE_KEY)
    except Exception:  # noqa: BLE001 — test-only helper must not throw
        # If Redis is unavailable / mocked-as-error, the cache is
        # effectively reset by virtue of the test's mock. Nothing to do.
        pass


# ===========================================================================
# RELATED FILES:
#   validators.py            — StrictJwtValidator calls get_signing_keys()
#   dependency.py            — wires the validators into a FastAPI dep
#   ../../redis_client.py    — get_redis() singleton this module uses
#   ../../config.py          — jwks_url + jwks_cache_ttl_seconds + redis_url
#   ../../../tests/contract/test_jwt_shadow.py
#                            — monkey-patches _fetch_jwks_from_upstream()
#                              + get_redis() to control cache + fetch state
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                            — E9 (JWKS cache in Redis 1hr TTL)
# ===========================================================================
