"""Phase 19.1 — per-user rate limiting middleware + hot-editable config.

Why this shape
--------------
Rishi's ADHD rule (memory feedback-adhd-observability-and-security-baseline)
requires:
  1. visibility — dashboard tile + email digest line (both done in same PR)
  2. hot-editable knobs — admin endpoint, no redeploy needed

So config lives in:
  - `rate_limit_config` table (durable across restarts, see migration 025)
  - mirrored into Redis under `rate:config:<key>` so the middleware can
    read in O(1) on the request path AND PUT can update all replicas
    instantly (no rolling restart)

The middleware counts per-user (JWT.sub) and per-IP separately so an
unauthenticated abuser hammering / from one machine gets stopped even
if they don't have a user identity. We DEGRADE OPEN if Redis is
unreachable — better to serve users than block them on infra hiccup;
the limiter is a defense, not a hard requirement.

Limit window math
-----------------
Sliding-window approximation via fixed buckets. The minute-window key
includes the current UTC minute; over-counting near minute boundaries
is at most 1 minute's worth, acceptable for a defense-not-correctness
control. Postgres INCR with EXPIRE on first-write keeps memory bounded
(60s for minute, 1h for hour buckets, auto-evicted).
"""

import logging
import os
import time
from datetime import datetime, timezone

import jwt as _jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


# ─── config keys + defaults (ABSOLUTE source of truth is the DB row;
#     these are the fallbacks the middleware uses on cold-start before
#     it has loaded from DB into Redis) ─────────────────────────────────

LIMIT_KEYS = (
    "per_user_per_min",
    "per_user_per_hour",
    "per_ip_per_min",
    "per_ip_per_hour",
)

DEFAULT_LIMITS = {
    "per_user_per_min": 60,
    "per_user_per_hour": 1000,
    "per_ip_per_min": 30,
    "per_ip_per_hour": 500,
}

# Endpoints the middleware MUST NOT rate-limit — Rishi must always be
# able to load the dashboard / health, even if the system is being
# overrun. Defining as prefixes so future /admin/* paths inherit.
SKIP_PREFIXES = (
    "/health",
    "/healthz",
    "/status",
    "/admin/",  # all admin endpoints, including dashboard + config itself
    "/ws/",
)

_REDIS_CONFIG_KEY = "rate:config"
_REDIS_REJECTIONS_KEY = "rate:rejections:24h"


# ─── lazy Redis client (mirrors session_memory.py pattern) ────────────────


_redis_client = None


async def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        url = os.environ.get("REDIS_URL")
        if url:
            _redis_client = aioredis.from_url(url, decode_responses=True)
        else:
            _redis_client = aioredis.Redis(
                host=os.environ.get("REDIS_HOST", "redis-primary"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                password=os.environ.get("REDIS_PASSWORD") or None,
                decode_responses=True,
            )
        return _redis_client
    except Exception as e:
        logger.warning("rate_limiter: Redis init failed (degrading open): %s", e)
        return None


# ─── config load/save ─────────────────────────────────────────────────────


async def get_current_limits() -> dict[str, int]:
    """Read current limits — Redis first (fast), fall back to defaults
    if Redis unreachable. Admin PUT writes to BOTH Redis and DB so this
    reflects the latest set value across all replicas."""
    redis = await _get_redis()
    if redis is None:
        return dict(DEFAULT_LIMITS)
    try:
        vals = await redis.hgetall(_REDIS_CONFIG_KEY)
        if not vals:
            return dict(DEFAULT_LIMITS)
        return {k: int(v) for k, v in vals.items() if k in LIMIT_KEYS}
    except Exception as e:
        logger.warning("rate_limiter: config read failed: %s", e)
        return dict(DEFAULT_LIMITS)


async def hydrate_from_db(pool) -> None:
    """Called at startup: pull config from DB into Redis so the
    middleware can read fast. Safe to call repeatedly."""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        rows = await pool.fetch("SELECT key, value FROM rate_limit_config")
        if not rows:
            return
        await redis.hset(
            _REDIS_CONFIG_KEY, mapping={r["key"]: str(r["value"]) for r in rows}
        )
    except Exception as e:
        logger.warning("rate_limiter: hydrate failed: %s", e)


async def update_limit(pool, key: str, value: int, updated_by: str) -> None:
    """Hot-edit a single limit. Writes DB (durable) then Redis (live).
    DB-first so a Redis write failure leaves the durable state correct."""
    if key not in LIMIT_KEYS:
        raise ValueError(f"unknown limit key: {key}")
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"value must be positive int, got {value!r}")
    await pool.execute(
        """
        INSERT INTO rate_limit_config (key, value, updated_at, updated_by)
        VALUES ($1, $2, NOW(), $3)
        ON CONFLICT (key) DO UPDATE SET
            value = EXCLUDED.value,
            updated_at = NOW(),
            updated_by = EXCLUDED.updated_by
        """,
        key,
        value,
        updated_by,
    )
    redis = await _get_redis()
    if redis is not None:
        try:
            await redis.hset(_REDIS_CONFIG_KEY, key, str(value))
        except Exception as e:
            logger.warning("rate_limiter: redis sync failed (DB ok): %s", e)


# ─── identity extraction ─────────────────────────────────────────────────


def _user_from_request(request: Request) -> str | None:
    """Pull JWT sub if present + valid-shape. We don't verify signature
    (matches the canonical /api/v1 auth) — a forged sub still gets you
    a rate-limit bucket, not auth. No JWT or malformed → return None and
    caller falls back to IP-based limiting."""
    auth = request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return None
    token = auth.split(None, 1)[1].strip()
    try:
        payload = _jwt.decode(token, options={"verify_signature": False})
        sub = payload.get("sub")
        return sub if isinstance(sub, str) and sub else None
    except Exception:
        return None


def _client_ip(request: Request) -> str:
    """Prefer X-Forwarded-For (Caddy fronts us); fall back to socket peer."""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ─── counter math ────────────────────────────────────────────────────────


def _bucket_keys(identity: str, scope: str, now: datetime) -> tuple[str, str]:
    """Return the (minute_bucket_key, hour_bucket_key) for this identity.

    Bucket key includes the current UTC time component so over-counting
    is bounded — at most 1 minute or 1 hour of requests get attributed
    to the same key, regardless of clock drift across replicas."""
    minute_part = now.strftime("%Y%m%d%H%M")
    hour_part = now.strftime("%Y%m%d%H")
    return (
        f"rate:{scope}:{identity}:min:{minute_part}",
        f"rate:{scope}:{identity}:hour:{hour_part}",
    )


async def _hit_and_check(
    redis, identity: str, scope: str, now: datetime, limits: dict[str, int]
) -> tuple[bool, str, int, int]:
    """Increment the relevant buckets and return:
      (allowed, exceeded_key, used, limit)
    where `allowed = True` if both windows are within limit, else False.
    Returns the FIRST exceeded key (minute checked before hour)."""
    min_key, hour_key = _bucket_keys(identity, scope, now)
    pipe = redis.pipeline()
    pipe.incr(min_key)
    pipe.expire(min_key, 120)  # 2× window to outlive clock skew
    pipe.incr(hour_key)
    pipe.expire(hour_key, 7200)
    results = await pipe.execute()
    min_count = int(results[0])
    hour_count = int(results[2])
    min_limit = limits[f"{scope}_per_min"]
    hour_limit = limits[f"{scope}_per_hour"]
    if min_count > min_limit:
        return False, f"{scope}_per_min", min_count, min_limit
    if hour_count > hour_limit:
        return False, f"{scope}_per_hour", hour_count, hour_limit
    return True, "", 0, 0


async def _record_rejection(
    redis, identity: str, scope: str, key_exceeded: str
) -> None:
    """Append to a daily 24h sorted-set of recent rejections so the
    /admin/rate-limits/status endpoint can show what's been rejected.
    Sorted set with score = unix-ts auto-trims via ZREMRANGEBYSCORE."""
    try:
        now_ts = int(time.time())
        cutoff = now_ts - 86400
        member = f"{scope}:{identity}:{key_exceeded}:{now_ts}"
        pipe = redis.pipeline()
        pipe.zadd(_REDIS_REJECTIONS_KEY, {member: now_ts})
        pipe.zremrangebyscore(_REDIS_REJECTIONS_KEY, 0, cutoff)
        pipe.expire(_REDIS_REJECTIONS_KEY, 90000)
        await pipe.execute()
    except Exception:
        pass  # logging the rejection is best-effort


# ─── middleware ──────────────────────────────────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Counts every request by JWT.sub when available, else by IP.

    Skips entirely for SKIP_PREFIXES (health, admin, websocket). Skips
    silently if Redis is unreachable — defense-not-correctness."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        for prefix in SKIP_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        redis = await _get_redis()
        if redis is None:
            return await call_next(request)

        limits = await get_current_limits()
        now = datetime.now(timezone.utc)

        user = _user_from_request(request)
        if user is not None:
            allowed, key_exceeded, used, limit = await _hit_and_check(
                redis, user, "per_user", now, limits
            )
            if not allowed:
                await _record_rejection(redis, user, "per_user", key_exceeded)
                return _too_many_response(key_exceeded, used, limit)
        else:
            ip = _client_ip(request)
            allowed, key_exceeded, used, limit = await _hit_and_check(
                redis, ip, "per_ip", now, limits
            )
            if not allowed:
                await _record_rejection(redis, ip, "per_ip", key_exceeded)
                return _too_many_response(key_exceeded, used, limit)

        return await call_next(request)


def _too_many_response(key_exceeded: str, used: int, limit: int) -> JSONResponse:
    """Standard 429 + Retry-After header. The header value is the
    remaining seconds in the offending window — minute keys get 60,
    hour keys get 3600."""
    retry_after = 60 if key_exceeded.endswith("_per_min") else 3600
    return JSONResponse(
        status_code=429,
        content={
            "detail": "rate limit exceeded",
            "limit_key": key_exceeded,
            "used": used,
            "limit": limit,
            "retry_after_seconds": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


# ─── observability helpers (dashboard tile + admin status endpoint) ──────


async def get_status() -> dict:
    """For /admin/rate-limits/status + dashboard tile. Cheap — one
    ZCARD + one HGETALL."""
    redis = await _get_redis()
    if redis is None:
        return {
            "redis_available": False,
            "current_limits": dict(DEFAULT_LIMITS),
            "rejections_24h": 0,
            "recent_rejections": [],
        }
    try:
        limits = await get_current_limits()
        rejections_24h = int(await redis.zcard(_REDIS_REJECTIONS_KEY))
        # Top 10 most recent rejections for the drill-in
        recent_raw = await redis.zrevrange(_REDIS_REJECTIONS_KEY, 0, 9, withscores=True)
        recent = []
        for member, score in recent_raw:
            # member shape: scope:identity:key:ts
            parts = member.split(":", 3)
            if len(parts) >= 3:
                recent.append(
                    {
                        "scope": parts[0],
                        "identity": parts[1],
                        "limit_key": parts[2],
                        "rejected_at": datetime.fromtimestamp(
                            int(score), tz=timezone.utc
                        ).isoformat(),
                    }
                )
        return {
            "redis_available": True,
            "current_limits": limits,
            "rejections_24h": rejections_24h,
            "recent_rejections": recent,
        }
    except Exception as e:
        logger.warning("rate_limiter: get_status failed: %s", e)
        return {
            "redis_available": False,
            "current_limits": dict(DEFAULT_LIMITS),
            "rejections_24h": 0,
            "recent_rejections": [],
            "error": str(e),
        }
