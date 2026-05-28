#!/usr/bin/env python3
"""Backfill embeddings for user_memories rows that pre-date Phase 4.4.

Idempotent: only touches rows WHERE embedding IS NULL. Re-runnable.
Uses Gemini :batchEmbedContents to keep round-trips low.

Run from inside the agent container (it has GEMINI_API_KEY + DB env vars):
    docker exec <yral-rishi-agent-container> python /app/../scripts/backfill_memory_embeddings.py

The script:
1. Pulls 50 rows with embedding IS NULL at a time
2. Batch-embeds them via Gemini
3. UPDATEs each row with its embedding
4. Repeats until no rows remain
5. Prints per-batch progress + final summary
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))


BATCH_SIZE = 50
SLEEP_BETWEEN_BATCHES_SEC = 0.2  # gentle pacing to avoid rate limits


async def run():
    import database
    from repositories import memory_repo
    from services import embeddings

    pool = await database.get_pool()

    total = await pool.fetchval(
        "SELECT COUNT(*) FROM user_memories WHERE embedding IS NULL"
    )
    print(f"=== Backfill embeddings — {total} rows pending ===")
    if total == 0:
        print("Nothing to do.")
        return 0

    done = 0
    failed = 0
    t_start = time.monotonic()

    while True:
        batch = await memory_repo.list_missing_embedding(pool, limit=BATCH_SIZE)
        if not batch:
            break

        texts = [
            embeddings.memory_to_embed_text(r["category"], r["key"], r["value"])
            for r in batch
        ]
        t_batch_start = time.monotonic()
        vectors = await embeddings.embed_batch(texts)
        embed_ms = (time.monotonic() - t_batch_start) * 1000

        # Update rows one-by-one — small N per batch (50), keeps SQL simple
        batch_success = 0
        for row, vec in zip(batch, vectors):
            if vec is None:
                failed += 1
                continue
            try:
                await memory_repo.update_embedding(pool, row["id"], vec)
                batch_success += 1
            except Exception as e:
                print(f"  UPDATE failed for id={row['id']}: {e}")
                failed += 1

        done += batch_success
        elapsed_total = time.monotonic() - t_start
        print(
            f"  batch: {batch_success}/{len(batch)} embedded in {embed_ms:.0f}ms "
            f"| total: {done}/{total} ({done * 100 // max(total, 1)}%) "
            f"| elapsed: {elapsed_total:.1f}s | failed: {failed}"
        )

        if len(batch) < BATCH_SIZE:
            # Last partial batch — we're done.
            break

        await asyncio.sleep(SLEEP_BETWEEN_BATCHES_SEC)

    elapsed = time.monotonic() - t_start
    remaining = await pool.fetchval(
        "SELECT COUNT(*) FROM user_memories WHERE embedding IS NULL"
    )
    print("\n=== Backfill complete ===")
    print(f"  Embedded:  {done}")
    print(f"  Failed:    {failed}")
    print(f"  Remaining: {remaining}  (re-run to retry the failed ones)")
    print(f"  Elapsed:   {elapsed:.1f}s")
    return 1 if remaining > 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
