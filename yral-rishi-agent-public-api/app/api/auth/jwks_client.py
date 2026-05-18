# ---------------------------------------------------------------------------
# jwks_client.py — fetch + per-replica cache of auth.yral.com's JWKS.
#
# ⭐ START HERE: the public API is one function, `get_signing_keys()`.
# It returns a dict {kid: PEM-encoded public key} suitable for handing
# to `jwt.decode(..., key=keys[kid], algorithms=["RS256"])`. First call
# fetches from settings.jwks_url; subsequent calls within
# settings.jwks_cache_ttl_seconds return the cached set; expired-cache
# triggers a refetch.
#
# WHY IN-PROCESS (NOT REDIS) CACHE FOR DAY 3?
# Per Rishi's Day-3 directive ("cache 6h, per E9"); E9 originally said
# Redis 1hr but Rishi's Day-3 instruction overrides the storage layer.
# In-process per-replica means each replica fetches independently;
# 3 replicas × 1 fetch / 6h = trivial load on auth.yral.com. Day-4
# (Redis client lands) may promote this to a shared cache; the change
# is a single function-body edit since `get_signing_keys()` is the
# entire public surface.
#
# WHY httpx (sync), NOT asyncpg?
# JWKS fetch is rare (once per replica per 6h) AND happens at request
# time when a token's `kid` doesn't match a cached key. Using sync
# `httpx.get()` keeps the cache logic simple — async fetching adds
# complexity (event-loop coordination, concurrent-fetch dedup) for
# zero benefit at this call rate. Per A2.1 — simple > clever.
#
# WHY THE JWKS RESPONSE IS PARSED AT FETCH TIME, NOT LAZILY?
# A malformed JWKS (e.g., auth.yral.com returns HTML during an outage)
# should fail-fast at fetch — not silently corrupt later validations.
# The PyJWT helpers convert each JWK to a public key object eagerly;
# any error becomes a `jwks_fetch_error` reason in the strict validator.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import time
from typing import Optional

import httpx
from jwt.algorithms import RSAAlgorithm

from app.config import get_settings


class JwksFetchError(Exception):
    """Raised when JWKS fetch / parse fails.

    WHAT: signals the JWKS document couldn't be retrieved or parsed.
    WHEN: raised by `get_signing_keys()` after exhausting retries.
    WHY:  the strict validator catches this + reports `jwks_fetch_error`
          as the divergence reason so the shadow log shows the auth
          server is unreachable; legacy still answers so the request
          isn't crashed.
    """


# Module-level cache. Per-replica per-process.
_cached_keys: Optional[dict[str, object]] = None
_cached_at: float = 0.0


def _fetch_jwks() -> dict[str, object]:
    """Pull the JWKS document + parse into {kid: public_key_object}.

    WHAT: sync HTTPS GET against settings.jwks_url; parses the response
          JSON; converts each JWK entry into a public key object via
          PyJWT's RSAAlgorithm.from_jwk().
    WHEN: called by `get_signing_keys()` when the cache is empty or
          expired.
    WHY:  centralizes the fetch + parse logic so the cache layer stays
          dumb (timestamp + dict).
    """
    settings = get_settings()
    # 5-second timeout — JWKS endpoints respond in <100ms in practice;
    # 5s is generous enough to absorb transient slowness without making
    # the request handler block indefinitely.
    try:
        response = httpx.get(settings.jwks_url, timeout=5.0)
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError catches json-decode errors (e.g., HTML 5xx page);
        # httpx.HTTPError catches network + 4xx/5xx.
        raise JwksFetchError(f"jwks fetch failed: {exc}") from exc

    keys_by_kid: dict[str, object] = {}
    for jwk in document.get("keys", []):
        kid = jwk.get("kid")
        if not kid:
            # Skip keys without a kid — a token couldn't reference them
            # anyway. (Some auth providers emit a default-kid for
            # legacy clients; we don't need that here.)
            continue
        # PyJWT's RSAAlgorithm.from_jwk accepts the JWK dict (or its
        # JSON string form) and returns an RSAPublicKey object that
        # jwt.decode() accepts as `key`.
        try:
            keys_by_kid[kid] = RSAAlgorithm.from_jwk(jwk)
        except Exception as exc:  # noqa: BLE001 — JWK parse errors vary by lib version
            # A single broken key shouldn't bring down the whole JWKS
            # cache; skip it + continue. If ALL keys are broken, the
            # eventual KeyError-by-kid in the validator becomes the
            # surfaced error.
            _ = exc

    return keys_by_kid


def get_signing_keys() -> dict[str, object]:
    """Return the cached {kid: public_key} dict, refreshing if expired.

    WHAT: returns a dict mapping JWT `kid` header values to RSA public
          key objects. Strict validator does `keys[kid]` to find the
          signing key for a given token.
    WHEN: called by StrictJwtValidator.validate() on every request when
          the strict path runs.
    WHY:  per-replica cache means each replica fetches JWKS at most
          once per `jwks_cache_ttl_seconds` (6h by default per Rishi's
          Day-3 directive).
    """
    global _cached_keys, _cached_at  # noqa: PLW0603 — module-level cache is intentional

    settings = get_settings()
    now = time.monotonic()

    # Cache miss: never fetched, OR TTL expired.
    if _cached_keys is None or (now - _cached_at) > settings.jwks_cache_ttl_seconds:
        _cached_keys = _fetch_jwks()
        _cached_at = now

    return _cached_keys


def reset_cache_for_testing() -> None:
    """Test-only helper: clear the cache so a test can force a refetch.

    WHAT: nulls the cache so the next get_signing_keys() call hits
          _fetch_jwks() again.
    WHEN: called from test fixtures that want to validate cache-miss
          + JWKS-fetch-error paths deterministically.
    WHY:  tests need a way to clear state between cases without
          tearing down the entire pytest session.
    """
    global _cached_keys, _cached_at  # noqa: PLW0603 — see above
    _cached_keys = None
    _cached_at = 0.0


# ===========================================================================
# RELATED FILES:
#   validators.py            — StrictJwtValidator calls get_signing_keys()
#   dependency.py            — wires the validators into a FastAPI dep
#   ../../config.py          — jwks_url + jwks_cache_ttl_seconds settings
#   ../../../tests/contract/test_jwt_shadow.py
#                            — monkey-patches _fetch_jwks() to control
#                              what get_signing_keys returns
# ===========================================================================
