"""Shared-secret auth for the three server-to-server endpoints amorae
(the spicy web brand) calls into v2. Header name + secret placement
locked by the 2026-07-01 amorae↔v2 contract.

The secret is a Swarm secret placed by Session 6 at
`/run/secrets/V2_WEB_SHARED_SECRET` (file-first, mirroring the
llm_registry._resolve_api_key pattern). Env-var fallback
`V2_WEB_SHARED_SECRET` covers local dev + CI where the swarm-secret
file doesn't exist.

Auth split for the amorae contract (contract §Auth model):
  - POST /api/v1/users/nsfw-consent  → this middleware (server-to-server)
  - POST /api/v1/spicy/handoff/exchange (track 2a) → this middleware
  - GET  /api/v1/spicy/context (track 2b) → this middleware
  - GET  /api/v1/users/nsfw-consent  → JWT user (auth.get_current_user)

Constant-time comparison (`secrets.compare_digest`) so a malformed /
guessed header can't leak the real secret via a timing side-channel.
"""

import logging
import os
import secrets

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Header + file names are contract-locked — see
# docs/amorae-v2-contract-2026-07-01.md.
_HEADER = "X-Amorae-Secret"
_SECRET_PATH = "/run/secrets/V2_WEB_SHARED_SECRET"
_ENV_FALLBACK = "V2_WEB_SHARED_SECRET"


def _load_secret() -> str | None:
    """File-first, env-var fallback. None on both missing → the
    dependency raises 503 (service misconfigured), NEVER 200. Any
    other outcome would silently accept unauthenticated writes."""
    if os.path.exists(_SECRET_PATH):
        try:
            with open(_SECRET_PATH) as f:
                val = f.read().strip()
            if val:
                return val
        except OSError as e:
            logger.warning("amorae_auth: failed reading %s: %s", _SECRET_PATH, e)
    return os.environ.get(_ENV_FALLBACK) or None


def require_amorae_secret(request: Request) -> None:
    """FastAPI dependency. Rejects any request without a matching
    `X-Amorae-Secret` header. Sentry breadcrumb on rejection so a
    misconfigured amorae deploy is loud, not silent."""
    presented = request.headers.get(_HEADER)
    expected = _load_secret()

    if not expected:
        # Fail-closed: without a configured secret we must NOT accept
        # writes. 503 rather than 401 tells the operator "your v2 side
        # is misconfigured" instead of "amorae sent a bad secret".
        logger.error(
            "amorae_auth: %s not configured — rejecting request from %s",
            _SECRET_PATH,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=503, detail="shared secret not configured")

    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(
            status_code=401, detail="invalid or missing X-Amorae-Secret"
        )
