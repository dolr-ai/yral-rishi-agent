"""Phase 0 Request Images track B — user_image_requests repository
(migration 046).

The composite PK (user_id, bot_id, request_date) IS the rate limiter:
INSERT ... ON CONFLICT DO NOTHING → a rejected insert (returning False)
means the user already tapped Request Images for this bot today.
No counter, no Redis, no rolling window; UTC calendar day per the
design decision log.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)


async def try_record(pool, user_id: str, bot_id: str, request_date: date) -> bool:
    """Attempt to record a new request. Returns True when THIS caller
    is the first tap of the day (rate-limit passed); False when the
    user already tapped this bot today.

    Caller semantics: on False → return 429. On True → proceed to the
    collage flow.

    Note: this consumes the daily quota BEFORE generation runs. The
    design's §7 "don't consume the quota on generation failure" is a
    Phase 1 refinement — the brief scoped Phase 0 to the current
    ordering (try_record → generate)."""
    row = await pool.fetchrow(
        """
        INSERT INTO user_image_requests
            (user_id, bot_id, request_date)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id, bot_id, request_date) DO NOTHING
        RETURNING user_id
        """,
        user_id,
        bot_id,
        request_date,
    )
    return row is not None
