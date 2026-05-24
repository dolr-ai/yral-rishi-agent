# ---------------------------------------------------------------------------
# redis_client.py — singleton getter for the redis-py client.
#
# ⭐ START HERE: one callable, `get_redis()`. Returns the cached
# `redis.Redis` instance constructed from `settings.redis_url`. First
# call constructs; subsequent calls return the cached instance.
#
# WHY A SINGLETON (not per-request)?
# redis-py creates a connection pool under the Redis object. Per-request
# construction would spin a fresh pool every time + drop it on garbage
# collection — defeats the entire pool. Singleton ensures ONE pool per
# process across the whole service.
#
# WHY THIS FILE LIVES AT app/ TOP LEVEL (not app/api/)?
# Redis is a CROSS-CUTTING concern — Day-4A uses it for JWKS cache (in
# app/api/auth/); Day-4C uses it for F10 idempotency dedup; future
# features (feature flags per F11, event stream per H10) will use it
# too. Co-locating with the existing top-level middleware modules
# (sentry_middleware.py, langfuse_middleware.py, etc.) matches the
# template's "infrastructure pieces are siblings of app/main.py"
# convention. Per the template's `app/api/` rule, the api/ subpackage
# is HTTP-surface code only.
#
# WHY NOT shared-library-code-used-by-every-v2-service/ ?
# That folder exists but is coordinator-owned (per session ownership)
# + currently contains only a placeholder README — no shared redis
# client wrapper yet. Per the Day-4A directive: "Use the redis client
# already declared in shared-library if it's there; otherwise add
# `redis[hiredis]>=5`." It's not there → we use redis-py directly.
# When shared-library grows a real RedisClient class, this file
# becomes a thin adapter or gets removed.
#
# WHY decode_responses=False?
# Default redis-py returns bytes (not str) from `get()`. We want bytes
# in this codebase because (a) the JWKS cache stores JSON bytes (b)
# Day-4C's idempotency cache stores raw HTTP response bytes for
# byte-for-byte replay fidelity. Callers that need str do `.decode()`
# explicitly — keeps the boundary explicit.
#
# WHY socket_connect_timeout AND socket_timeout?
# - socket_connect_timeout: cap on initial TCP-connect handshake. 2s
#   chosen so a totally-down Redis fails the strict-path JWKS read
#   fast (and the legacy validator answers without delay per E9).
# - socket_timeout: cap on individual read/write after the connection
#   is up. 2s same reasoning.
# Without these, a hung Redis box would block every request thread.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

from functools import lru_cache

import redis

from app.config import get_settings


@lru_cache(maxsize=1)
def get_redis() -> redis.Redis:
    """Return the cached redis.Redis singleton.

    WHAT: builds a redis.Redis instance pointing at settings.redis_url
          on first call; returns the same instance thereafter.
    WHEN: called by code paths that need Redis access — currently the
          JWKS cache in app/api/auth/jwks_client.py; Day-4C will add
          the idempotency dedup callsite.
    WHY:  one connection pool per process; cheap to obtain in any
          callsite; no per-request construction overhead.
    """
    settings = get_settings()
    # `from_url` parses the URL + applies the kwargs. The connection
    # pool is created lazily on first command, not at construction
    # time — so this call is cheap even when Redis is unreachable.
    #
    # `password=` carries the AUTH credential sent in response to the
    # primary's `--requirepass` AUTH challenge. The v2 cluster's
    # Redis primary requires AUTH on every connection (per H3 +
    # 2026-05-22 incident-response rotation); without this keyword
    # argument the first command raises
    # `redis.exceptions.AuthenticationError: Authentication required.`
    # Empty default keeps local development working — redis-py
    # treats password=None as "no AUTH frame", matching the
    # unauthenticated docker-compose Redis.
    #
    # PASSWORDLESS-URL CONTRACT (Codex PR #137 round-7 BLOCKER 1 +
    # round-9 BLOCKER 3 fix): `settings.redis_url` MUST NOT embed
    # credentials in the `user:pass@host` portion. The redis-py URL
    # parser would take URL-embedded credentials over this
    # `password=` argument, silently bypassing the `REDIS_PASSWORD`
    # Swarm-secret rotation pattern. `app/config.py`'s
    # `_reject_password_in_redis_url` validator rejects credential-
    # bearing URLs at Settings construction time so the violation
    # surfaces at boot rather than at the first Redis call —
    # gated behind the `enforce_passwordless_redis_url` feature
    # flag (defaults FALSE so this PR is safe to merge before
    # Session 1's secret rotation; Session 1 flips the flag TRUE in
    # a follow-up after PR #150 + rotation land). `REDIS_PASSWORD`
    # is the sole AUTH source.
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
        password=settings.redis_password or None,
    )


def reset_for_testing() -> None:
    """Test-only helper: clear the cached client (if lru_cache present).

    WHAT: clears the lru_cache so the next get_redis() call constructs
          a fresh client (e.g., after monkey-patching settings.redis_url
          in a fixture). hasattr-guards `cache_clear` so the helper is
          a no-op when called AFTER a monkey-patch has already replaced
          `get_redis` with a non-lru-cached substitute (e.g., a lambda
          returning a FakeRedis instance) — common in test fixtures
          that flip get_redis BEFORE clearing the prior cache.
    WHEN: tests rarely need this — most monkey-patch get_redis directly
          via app.redis_client.get_redis. Provided for symmetry with
          jwks_client.reset_cache_for_testing().
    WHY:  test isolation; nothing more.
    """
    if hasattr(get_redis, "cache_clear"):
        get_redis.cache_clear()


# ===========================================================================
# RELATED FILES:
#   config.py                — redis_url setting + the rest of the singleton config
#   api/auth/jwks_client.py  — Day-4A's first consumer (Redis-backed JWKS cache per E9)
#   api/chat_routes.py       — Day-4C's planned second consumer (F10 idempotency dedup)
# ===========================================================================
