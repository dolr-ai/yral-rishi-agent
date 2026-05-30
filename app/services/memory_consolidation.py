"""Phase 4.8: nightly memory consolidation.

Background loop that finds near-duplicate user_memories (very low cosine
distance between embeddings) and merges them. As the table grows, repeat
extractions of the same fact ("favorite_food=biryani" said three times)
should collapse to a single row — otherwise prompt-token budget bloats.

Algorithm (per user):
1. Pull all memories with non-null embeddings
2. For each memory, query its 2 nearest neighbors via pgvector
3. If neighbor distance < MERGE_DISTANCE_THRESHOLD and they're not the
   same row, mark the older / lower-confidence one for deletion
4. Delete in a single batch at the end of the user's pass

Runs once every 24 hours. First run is delayed by INITIAL_DELAY_SEC so
the loop doesn't slam Postgres immediately on container restart.

Safe to run while traffic is live — each merge is a single transaction
and the surviving row is always the more recent / higher-confidence one
that production extraction would pick anyway.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Cosine distance < this → treat as the same fact. 0.08 is loose enough
# to catch paraphrases ("loves cricket" / "enjoys watching cricket") while
# leaving genuinely-different facts ("loves cricket" / "loves football")
# clearly above the threshold.
MERGE_DISTANCE_THRESHOLD = 0.08

CONSOLIDATION_INTERVAL_SEC = 24 * 60 * 60  # 24h
INITIAL_DELAY_SEC = 10 * 60  # 10 min after startup


async def consolidate_once(pool) -> dict:
    """One full pass across every user with embedded memories.

    Returns a dict of {users_scanned, pairs_merged, rows_deleted} for logging.
    Idempotent — running it twice in a row should report ~zero merges the
    second time.
    """
    users = await pool.fetch(
        "SELECT DISTINCT user_id FROM user_memories WHERE embedding IS NOT NULL"
    )
    pairs_merged = 0
    rows_deleted = 0

    for u in users:
        user_id = u["user_id"]
        # For each memory of this user, find the nearest non-self neighbor.
        # We use a self-join via the pgvector <=> operator. Limit to pairs
        # below threshold; pick the loser (older or lower-confidence) to drop.
        candidates = await pool.fetch(
            """
            WITH neighbors AS (
                SELECT
                    a.id AS a_id,
                    a.confidence AS a_conf,
                    a.updated_at AS a_updated,
                    b.id AS b_id,
                    b.confidence AS b_conf,
                    b.updated_at AS b_updated,
                    (a.embedding <=> b.embedding) AS dist
                FROM user_memories a
                JOIN user_memories b
                  ON a.user_id = b.user_id
                 AND a.id < b.id  -- only consider each unordered pair once
                 AND a.embedding IS NOT NULL
                 AND b.embedding IS NOT NULL
                WHERE a.user_id = $1
            )
            SELECT * FROM neighbors WHERE dist < $2
            """,
            user_id,
            MERGE_DISTANCE_THRESHOLD,
        )

        to_delete: set[str] = set()
        for row in candidates:
            # Pick the loser: lower confidence, ties broken by older updated_at
            if row["a_conf"] < row["b_conf"]:
                loser = row["a_id"]
            elif row["a_conf"] > row["b_conf"]:
                loser = row["b_id"]
            else:
                loser = (
                    row["a_id"] if row["a_updated"] < row["b_updated"] else row["b_id"]
                )
            # Avoid re-deleting a row already in to_delete on this pass
            if loser in to_delete:
                continue
            to_delete.add(loser)
            pairs_merged += 1

        if to_delete:
            result = await pool.execute(
                "DELETE FROM user_memories WHERE id = ANY($1::text[])",
                list(to_delete),
            )
            # asyncpg returns "DELETE N" as the result string
            try:
                deleted_n = int(result.split()[-1])
            except (ValueError, IndexError):
                deleted_n = len(to_delete)
            rows_deleted += deleted_n

    return {
        "users_scanned": len(users),
        "pairs_merged": pairs_merged,
        "rows_deleted": rows_deleted,
    }


async def consolidation_loop():
    """Run consolidate_once forever, every 24h, after an initial delay."""
    from database import get_pool
    from kill_switch import is_enabled

    await asyncio.sleep(INITIAL_DELAY_SEC)
    while True:
        try:
            # Emergency kill-switch (Phase 19.3 stop-gap, 2026-05-30).
            if not is_enabled("memory_consolidation"):
                await asyncio.sleep(CONSOLIDATION_INTERVAL_SEC)
                continue
            pool = await get_pool()
            t0 = asyncio.get_event_loop().time()
            stats = await consolidate_once(pool)
            elapsed = asyncio.get_event_loop().time() - t0
            logger.info(
                f"memory_consolidation: scanned {stats['users_scanned']} users, "
                f"merged {stats['pairs_merged']} pairs, deleted {stats['rows_deleted']} rows "
                f"in {elapsed:.1f}s"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Non-fatal — log and try again at the next interval
            logger.warning(f"memory_consolidation pass failed (non-fatal): {e}")
        await asyncio.sleep(CONSOLIDATION_INTERVAL_SEC)
