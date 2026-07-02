"""Repository for `user_nsfw_consent` (migration 045).

One row per logged-in user who has confirmed the 18+ gate on the
spicy web brand. Amorae writes the row (server-to-server) via the
POST endpoint; the native app reads its own row (JWT-scoped) via
the GET endpoint for cross-device consent memory.

Shape mirrors the other Phase 19+ repos: one file per table-group,
raw SQL via asyncpg, no ORM.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def upsert(
    pool,
    *,
    user_id: str,
    confirmed_at: datetime | None = None,
    expires_at: datetime | None = None,
    source_ip: str | None = None,
) -> dict:
    """Insert-or-update the row for user_id.

    Idempotent per the amorae contract (§2, "any 2xx is success") —
    repeat writes for the same user overwrite confirmed_at + expires_at
    (a re-confirm slides the horizon forward) and refresh updated_at.
    source_ip is audit-only: last-write-wins is fine.

    confirmed_at defaults to NOW() when the caller doesn't supply it
    (amorae posts on "Continue (18+)" click, which IS the confirm
    moment — there's no benefit to a caller-side timestamp).
    """
    now = confirmed_at or datetime.now(timezone.utc)
    row = await pool.fetchrow(
        """
        INSERT INTO user_nsfw_consent
            (user_id, confirmed_at, expires_at, source_ip)
        VALUES ($1, $2, $3, $4::inet)
        ON CONFLICT (user_id) DO UPDATE
            SET confirmed_at = EXCLUDED.confirmed_at,
                expires_at   = EXCLUDED.expires_at,
                source_ip    = EXCLUDED.source_ip,
                updated_at   = NOW()
        RETURNING user_id, confirmed_at, expires_at
        """,
        user_id,
        now,
        expires_at,
        source_ip,
    )
    return dict(row)


async def get(pool, user_id: str) -> dict | None:
    """Return the consent row for user_id, or None if never confirmed.
    Only exposes fields the GET endpoint returns — source_ip is audit
    metadata and stays out of the user-facing shape."""
    row = await pool.fetchrow(
        """
        SELECT user_id, confirmed_at, expires_at
        FROM user_nsfw_consent
        WHERE user_id = $1
        """,
        user_id,
    )
    return dict(row) if row else None
