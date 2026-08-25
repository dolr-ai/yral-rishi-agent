"""videogen_requests DB helpers — the whole state of a generation.

One row per generation, written *before* the job is submitted to ComfyUI so a
process restart can never lose an in-flight job: the poll loop's recovery path
is simply "scan pending", not a separate resume mechanism.

`user_token` holds the caller's yral-auth id_token, needed minutes later to
write the post to SpacetimeDB as that user. It is cleared the moment the row
reaches a terminal state, and never logged.
"""

import logging

logger = logging.getLogger(__name__)

TERMINAL = ("complete", "failed")


def _row(row) -> dict | None:
    return dict(row) if row else None


async def create_pending(
    pool,
    *,
    user_id: str,
    video_id: str,
    prompt: str,
    model_id: str,
    user_token: str,
) -> dict | None:
    """Reserve the row before submitting anything. `video_id` is the single
    identifier the rest of the system uses — operation id to mobile, object key
    in storage, and post id in SpacetimeDB."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO videogen_requests
                (user_id, video_id, prompt, model_id, user_token, status)
            VALUES ($1, $2, $3, $4, $5, 'pending')
            RETURNING *
            """,
            user_id,
            video_id,
            prompt,
            model_id,
            user_token,
        )
    return _row(row)


async def attach_comfy_id(pool, *, video_id: str, comfy_id: str) -> None:
    """Record the ComfyUI prompt id once the job is queued. Until this lands the
    row is pending with no comfy_id — the loop treats that as "submit failed"
    after the stale window rather than polling a job that never existed."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE videogen_requests SET comfy_id = $2 WHERE video_id = $1",
            video_id,
            comfy_id,
        )


async def claim_pending(pool, *, lease_seconds: int, limit: int = 50) -> list[dict]:
    """Take exclusive ownership of up to `limit` pending rows and return them.

    The service runs 2 replicas x 4 uvicorn workers, so eight copies of the poll
    loop run at once. A plain SELECT hands the same row to all of them: on
    2026-08-25 one generation was fetched from the GPU box and uploaded to Storj
    six times, and every loser got `DuplicatePostId` from SpacetimeDB.

    The UPDATE ... RETURNING is atomic, so exactly one worker sees each row.
    `SKIP LOCKED` keeps the other seven from queueing behind it rather than
    moving on to work of their own.

    Claims lapse after `lease_seconds` so a worker that dies mid-generation
    cannot strand a row — the next loop re-claims it. That window must comfortably
    exceed a full generation (~2-3 min) or a healthy job gets picked up twice.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE videogen_requests SET claimed_at = NOW()
            WHERE video_id IN (
                SELECT video_id FROM videogen_requests
                WHERE status = 'pending'
                  AND (claimed_at IS NULL
                       OR claimed_at < NOW() - ($1 || ' seconds')::interval)
                ORDER BY created_at ASC
                LIMIT $2
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            str(lease_seconds),
            limit,
        )
    return [dict(r) for r in rows]


async def list_in_progress(pool, *, user_id: str) -> list[dict]:
    """Backs the Drafts-tab spinner. Only ever this user's rows — the caller
    passes the principal from the verified JWT, not from the request body."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM videogen_requests
            WHERE user_id = $1 AND status = 'pending'
            ORDER BY created_at DESC
            """,
            user_id,
        )
    return [dict(r) for r in rows]


async def mark_complete(pool, *, video_id: str, video_url: str) -> None:
    """Close the row. Clears `user_token` — it is only needed while in flight.

    Guarded on `status = 'pending'` so a late duplicate cannot reopen or
    overwrite a row that already reached a terminal state."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE videogen_requests
            SET status = 'complete', video_url = $2, user_token = NULL
            WHERE video_id = $1 AND status = 'pending'
            """,
            video_id,
            video_url,
        )


async def mark_failed(pool, *, video_id: str, reason: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE videogen_requests
            SET status = 'failed', failure_reason = $2, user_token = NULL
            WHERE video_id = $1 AND status = 'pending'
            """,
            video_id,
            reason[:500],
        )


async def fail_stale(pool, *, older_than_seconds: int) -> int:
    """Sweep generations whose job vanished — the GPU box died, ComfyUI was
    restarted, the queue was cleared. Without this a spinner runs forever in
    the app. Returns how many rows were swept."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE videogen_requests
            SET status = 'failed',
                failure_reason = 'timed out waiting for generation',
                user_token = NULL
            WHERE status = 'pending'
              AND created_at < NOW() - ($1 || ' seconds')::interval
            RETURNING video_id
            """,
            str(older_than_seconds),
        )
    if rows:
        logger.warning("videogen: swept %d stale request(s)", len(rows))
    return len(rows)


async def get_by_video_id(pool, *, video_id: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM videogen_requests WHERE video_id = $1", video_id
        )
    return _row(row)
