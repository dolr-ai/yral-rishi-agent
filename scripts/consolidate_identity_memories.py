#!/usr/bin/env python3
"""Phase 4.6 backfill: consolidate per-influencer identity memories into global.

Before Phase 4.6, identity facts (name, age, location, ...) were stored with
influencer_id=<bot> just like every other category. After Phase 4.6, new
identity extractions write influencer_id=NULL so they apply to every bot.

This script catches up the existing rows: for each (user_id, key) where any
row has category='identity', pick the most-recently-updated row's value,
upsert it as global (influencer_id=NULL), then delete the per-influencer
copies.

Idempotent. Re-runnable. Safe if run mid-traffic: each (user, key) is handled
in its own transaction, so concurrent extractions either land before us
(consolidated normally) or after (overwrites the global row).

Run from inside the agent container:
    docker cp /tmp/agent-build/scripts/consolidate_identity_memories.py <CN>:/tmp/
    docker exec <CN> python /tmp/consolidate_identity_memories.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


async def run():
    import database

    pool = await database.get_pool()

    # Find every (user_id, key) where there's an identity row that's NOT global
    candidates = await pool.fetch(
        """
        SELECT DISTINCT user_id, key
        FROM user_memories
        WHERE category = 'identity' AND influencer_id IS NOT NULL
        """
    )
    print(f"=== Consolidate identity memories — {len(candidates)} (user, key) pairs ===")
    if not candidates:
        print("Nothing to do.")
        return 0

    consolidated = 0
    skipped = 0
    for row in candidates:
        user_id = row["user_id"]
        key = row["key"]

        # Pick the most recently updated value (works whether the value is
        # stable across bots or has drifted — most-recent wins, like the
        # rest of memory_repo.upsert).
        winner = await pool.fetchrow(
            """
            SELECT category, value, confidence, source_message_id, embedding
            FROM user_memories
            WHERE user_id = $1 AND key = $2 AND category = 'identity'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            user_id,
            key,
        )
        if not winner:
            skipped += 1
            continue

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Upsert the global row (NULL influencer_id). NULLS NOT DISTINCT
                # ensures we end up with exactly one row for (user, NULL, key).
                # NB: we go through raw SQL here (not memory_repo.upsert) to
                # avoid re-embedding — we already have the winner's embedding.
                emb = winner["embedding"]  # already a pgvector string; pass through
                await conn.execute(
                    """
                    INSERT INTO user_memories (
                        user_id, influencer_id, category, key, value,
                        confidence, source_message_id, embedding
                    )
                    VALUES ($1, NULL, $2, $3, $4, $5, $6, $7::vector)
                    ON CONFLICT (user_id, influencer_id, key) DO UPDATE SET
                        value = EXCLUDED.value,
                        confidence = EXCLUDED.confidence,
                        source_message_id = EXCLUDED.source_message_id,
                        embedding = EXCLUDED.embedding,
                        updated_at = NOW()
                    """,
                    user_id,
                    winner["category"],
                    key,
                    winner["value"],
                    winner["confidence"],
                    winner["source_message_id"],
                    emb,
                )
                # Delete the per-influencer copies now that the global exists
                await conn.execute(
                    """
                    DELETE FROM user_memories
                    WHERE user_id = $1 AND key = $2 AND category = 'identity'
                      AND influencer_id IS NOT NULL
                    """,
                    user_id,
                    key,
                )
        consolidated += 1
        if consolidated % 50 == 0:
            print(f"  consolidated={consolidated} skipped={skipped}")

    remaining = await pool.fetchval(
        """
        SELECT COUNT(*) FROM user_memories
        WHERE category = 'identity' AND influencer_id IS NOT NULL
        """
    )
    global_count = await pool.fetchval(
        """
        SELECT COUNT(*) FROM user_memories
        WHERE category = 'identity' AND influencer_id IS NULL
        """
    )
    print("\n=== Consolidation complete ===")
    print(f"  Consolidated: {consolidated}")
    print(f"  Skipped:      {skipped}")
    print(f"  Global identity rows now: {global_count}")
    print(f"  Per-influencer identity rows still: {remaining} (should be 0)")
    return 1 if remaining > 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
