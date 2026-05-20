# ---------------------------------------------------------------------------
# idempotency.py — F10 idempotency-key dedup helpers (Redis-backed).
#
# ⭐ START HERE: three public functions —
#   `resolve_idempotency_key(request_idempotency_key_header) -> (key, source)`
#     normalizes the inbound X-Idempotency-Key into a stable key string
#     (uses the header if present; mints a UUID4 otherwise). `source` is
#     "client" or "server" so the call site can log adoption rate.
#   `cache_lookup(user_id, idempotency_key) -> CachedResponse | None`
#     reads the dedup entry from Redis; returns the cached envelope-
#     shaped response on hit, None on miss.
#   `cache_store(user_id, idempotency_key, status, body_bytes) -> None`
#     writes a successful response into Redis with the F10 TTL.
#
# WHY THE KEY IS SCOPED BY user_id?
# Per Day-4C directive: "Redis key:
# `idempotency:public-api:run-turn:{user_id}:{idempotency_key}` (scope
# by user_id so two users with same client-generated key don't collide)."
# Mobile clients across users can pick the same UUID4 (low-probability
# but non-zero); without user_id scoping, user-A's cached response could
# leak to user-B on collision.
#
# WHY CACHE ONLY ON SUCCESS (200) — directive says cache status too?
# The directive says cache "status + Content-Type + body for replay
# fidelity." But errors SHOULDN'T cache: if the orchestrator returned
# 503 once, a retry should attempt again (not replay the 503). The
# F10 contract is "idempotent re-execution," not "replay-the-error."
# So this module caches only on success. The cache_store contract
# preserves the status field as a hedge if a future Day-N PR decides
# to cache 4xx errors too (e.g., validation_failed is deterministic
# per input → cacheable). Right now the chat handler only calls
# cache_store on 200.
#
# WHY NOT JUST USE A FastAPI MIDDLEWARE?
# F10 says "default-on on all non-GET endpoints" which IS a middleware-
# shaped concern. BUT Day-4C scope is "wire on POST messages." A
# template-level middleware would touch ~6 non-GET endpoints at once
# (out of scope per A2.1 + the directive). I6-surfaced in PR body for
# coordinator: add follow-up DEP to Session 2 for template middleware
# OR do per-handler wiring on subsequent PRs.
#
# RELATED FILES (footer at end).
# ---------------------------------------------------------------------------

import json
import uuid
from dataclasses import dataclass
from typing import Literal, Optional

import redis as redis_lib

from app import redis_client as redis_client_module
from app.config import get_settings


# Key prefix the dedup helpers use. Versioned per the same pattern as
# the JWKS cache (jwks:auth.yral.com:v1) — bumping the version lets
# future format changes invalidate old entries without manual flushes.
_KEY_PREFIX = "idempotency:public-api:run-turn:v1"


# Identifies whether the idempotency key came from the client header
# or was minted server-side. Logged via Langfuse so we can track
# client-key adoption rate (per Day-4C directive: "log which case
# happened so 50%+ client-key adoption can be tracked").
IdempotencyKeySource = Literal["client", "server"]


@dataclass
class CachedResponse:
    """A previously-cached orchestrator response retrieved from Redis.

    WHAT: bundles `status` (int) + `body_bytes` (bytes — the raw
          orchestrator JSON response).
    WHEN: returned by cache_lookup() on cache hit.
    WHY:  the chat handler reconstructs a FastAPI Response from these
          for byte-for-byte replay fidelity.
    """

    status: int
    body_bytes: bytes


def _build_cache_key(user_id: str, idempotency_key: str) -> str:
    """Compose the Redis key per the Day-4C directive.

    WHAT: `idempotency:public-api:run-turn:v1:{user_id}:{key}`.
    WHEN: called by cache_lookup + cache_store.
    WHY:  single source of truth for the key shape; bug here would mean
          stores + lookups silently miss each other.
    """
    return f"{_KEY_PREFIX}:{user_id}:{idempotency_key}"


def _get_redis() -> redis_lib.Redis:
    """Late-binding accessor so test monkey-patches reach this module.

    WHAT: returns redis_client_module.get_redis().
    WHEN: called from cache_lookup + cache_store.
    WHY:  Python import-binding gotcha — `from app.redis_client import
          get_redis` binds the name at import time + misses test
          monkey-patches. Day-4A caught the same bug in jwks_client;
          replicating the late-binding pattern here.
    """
    return redis_client_module.get_redis()


def resolve_idempotency_key(
    request_header_value: Optional[str],
) -> tuple[str, IdempotencyKeySource]:
    """Normalize the client's X-Idempotency-Key into a stable string.

    WHAT: if the header is present + non-empty, returns
          (header_value, "client"); otherwise mints UUID4 + returns
          (new_uuid, "server").
    WHEN: called by the chat handler before consulting the cache.
    WHY:  centralized "key or mint" logic so the cache layer never
          sees an empty / None key.
    """
    if request_header_value and request_header_value.strip():
        return request_header_value.strip(), "client"
    return str(uuid.uuid4()), "server"


def cache_lookup(user_id: str, idempotency_key: str) -> Optional[CachedResponse]:
    """Look up the cached response for (user_id, idempotency_key).

    WHAT: reads `idempotency:public-api:run-turn:v1:{user_id}:{key}`
          from Redis. On hit, json-decodes the stored envelope back into
          a CachedResponse. On miss, returns None.
    WHEN: called by the chat handler on every message-send turn, BEFORE
          the orchestrator call.
    WHY:  the F10 dedup primitive — same key twice means same response
          out, without re-executing the LLM turn.
    """
    try:
        client = _get_redis()
        raw = client.get(_build_cache_key(user_id, idempotency_key))
    except (redis_lib.RedisError, OSError):
        # Redis errors during idempotency lookup → treat as cache miss.
        # The orchestrator call proceeds; cache_store will retry the
        # write (which will fail again, but the request still succeeds
        # from the client's perspective).
        return None

    if raw is None:
        return None

    try:
        decoded = json.loads(raw)
        return CachedResponse(
            status=int(decoded["status"]),
            body_bytes=decoded["body_b64"].encode("latin-1"),
        )
    except (ValueError, KeyError, TypeError):
        # Corrupted cache entry — treat as a miss + let the orchestrator
        # call proceed. Future cache_store overwrites the bad entry.
        return None


def cache_store(
    user_id: str,
    idempotency_key: str,
    status: int,
    body_bytes: bytes,
) -> None:
    """Write a response into the dedup cache.

    WHAT: SETs `idempotency:public-api:run-turn:v1:{user_id}:{key}` to
          a JSON envelope containing the status + body, with TTL =
          settings.idempotency_dedup_ttl_seconds (default 24h per F10).
    WHEN: called by the chat handler AFTER a successful orchestrator
          response (status 200) — see file header for why errors don't
          cache today.
    WHY:  populates the cache for client retries of the same idempotency
          key within the 24h window.
    """
    settings = get_settings()
    # latin-1 round-trips arbitrary bytes through str without re-encoding,
    # so the body_bytes survive json serialization byte-for-byte. (UTF-8
    # would mangle non-UTF-8 bytes; base64 would be cleaner but doubles
    # the cache size — latin-1 is 1:1.)
    envelope = json.dumps(
        {
            "status": status,
            "body_b64": body_bytes.decode("latin-1"),
        }
    )
    try:
        client = _get_redis()
        client.set(
            _build_cache_key(user_id, idempotency_key),
            envelope,
            ex=settings.idempotency_dedup_ttl_seconds,
        )
    except (redis_lib.RedisError, OSError):
        # Write failure → log + carry on. The current request still
        # returns the orchestrator's response to the caller; only
        # client retries with the same key would miss the cache.
        # Acceptable degradation per Day-4C scope (no Sentry alerting
        # added here — Day-N follow-up).
        return


# ===========================================================================
# RELATED FILES:
#   chat_routes.py           — send_message handler: calls
#                              resolve_idempotency_key → cache_lookup →
#                              orchestrator → cache_store
#   ../redis_client.py       — get_redis() singleton (late-bound here)
#   ../config.py             — idempotency_dedup_ttl_seconds setting
#   ../orchestrator_client.py — what fires when cache_lookup misses
#   yral-rishi-agent-plan-and-discussions/CONSTRAINTS.md
#                            — F10 (idempotency-key default-on, Redis 24h TTL)
# ===========================================================================
