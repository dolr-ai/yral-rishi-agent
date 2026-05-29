"""Integrity verifier — Phase 3 pending after the S3 ETL pivot.

Phase 2 removed V2's direct connection to chat-ai (it was never
reachable; see commit history). The old integrity checks here all
queried chat-ai directly via etl_readonly — which V2 no longer can.

Phase 3 will move the source-of-truth half of integrity to rishi-1
(where chat-ai IS reachable) and upload results to S3 alongside the
CSV deltas. V2 will then compare rishi-1's reported counts against its
own etl_processed_files + table counts.

Until Phase 3 ships, this file:
  - keeps the constants (so tests + admin endpoints stay valid)
  - exports a no-op integrity_loop that idles
  - exports get_status() returning {"status": "phase_3_pending"} so the
    /admin/etl-integrity endpoint stays mounted

Removing the loop entirely would require touching app/main.py too,
which Phase 3 will revisit; cleaner to keep the wiring.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


# Kept for tests + future Phase 3 reuse. Same values as before so the
# acceptance bands don't drift while integrity is offline.
INTEGRITY_INTERVAL_SEC = 60 * 60
INITIAL_DELAY_SEC = 10 * 60

MAX_DRIFT_ROWS = 500
FAIL_DRIFT_ROWS = 5000

SAMPLE_CONVERSATIONS = 20
WARN_SAMPLE_MISMATCHES = 1
FAIL_SAMPLE_MISMATCHES = 3

CHECKED_TABLES = ("ai_influencers", "conversations", "messages")


async def integrity_loop():
    """No-op loop. Phase 3 will replace this with the S3-based check."""
    logger.info(
        "etl_integrity: Phase 3 pending — integrity loop idle "
        "until rishi-1 reports counts via S3"
    )
    while True:
        try:
            await asyncio.sleep(INTEGRITY_INTERVAL_SEC)
        except asyncio.CancelledError:
            raise


async def get_status(v2_pool) -> dict:
    """Phase-3-pending placeholder for /admin/etl-integrity."""
    return {
        "status": "phase_3_pending",
        "reason": (
            "V2 no longer talks to chat-ai directly. Phase 3 will move "
            "integrity sourcing to rishi-1 and publish results to S3."
        ),
        "fail_count_24h": 0,
        "warn_count_24h": 0,
        "latest_per_check": [],
    }
