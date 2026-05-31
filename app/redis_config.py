"""Shared Redis URL resolver — file-first, env-var fallback.

Why one helper instead of 3 inline copies:
  - session_memory, rate_limiter, websocket_manager all need the same
    Redis URL. Without this, they drift (already happened — session_memory
    uses config._env, rate_limiter used os.environ.get directly).
  - The file-first pattern matches chat_ai_s3_credentials (Phase 1 ETL):
    Swarm secret is mounted at /run/secrets/<name>; container reads file
    so the value never appears in `docker service inspect`. Env var
    fallback preserves local-dev / CI ergonomics.

Activation (operator step, not code):
  docker service update --secret-add REDIS_URL yral-rishi-agent

The Swarm secret `REDIS_URL` already exists on rishi-4 with the
`redis://:<password>@redis-primary:6379` connection string baked in
(per Phase 0 Redis Sentinel setup). Once mounted, all three callers
above resolve the same URL through this helper.
"""

import os

DEFAULT_SECRET_PATH = "/run/secrets/REDIS_URL"


def get_redis_url() -> str | None:
    """Return the connection string. None means Redis isn't configured
    and callers should degrade gracefully (each call site already does)."""
    secret_file = os.environ.get("REDIS_URL_FILE", DEFAULT_SECRET_PATH)
    if os.path.exists(secret_file):
        try:
            with open(secret_file) as f:
                val = f.read().strip()
            if val:
                return val
        except Exception:
            pass
    return os.environ.get("REDIS_URL") or None
