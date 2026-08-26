"""The loop that finishes generations.

Scans `pending` rows and asks ComfyUI whether each one is done.

That is the entire async machinery — the previous design used a message broker,
HMAC-signed completion callbacks, a retry outbox and pre-signed upload URLs with
an expiry-refresh endpoint to achieve the same thing.

**Several copies of this loop run at once** — the service is 2 swarm replicas x
4 uvicorn workers, and every worker process starts its own. Polling is left
unsynchronised because it is a cheap idempotent read; the *finish* step is
claimed, because downloading, uploading and registering the post are not. Without
that claim all eight ran the finish for the same generation: the first two videos
this service ever produced were each fetched and uploaded six times, and the
seven losers got `DuplicatePostId` (see `repository.claim_for_finish`).

It is restart-safe by construction: the row is written before the job is
submitted, so a process that dies mid-generation simply finds the row still
pending — and re-claimable once its lease lapses — on a later tick. There is no
separate resume path because the loop *is* the resume path.

Ordering at completion matters. `add_post_2` runs **before** the row is closed —
reversed, the app's spinner would disappear a beat before the draft appeared,
and the user would watch their video vanish.
"""

import asyncio
import logging

import config
import sentry_sdk

from videogen import comfyui, repository, spacetime, storage

logger = logging.getLogger(__name__)


async def _finish(pool, row: dict, file_ref: dict) -> None:
    """Fetch, store, register, close — for one finished generation."""
    video_id = row["video_id"]
    user_id = row["user_id"]

    video_bytes = await comfyui.fetch_output(file_ref)
    video_url = await storage.store(user_id, video_id, video_bytes)

    token = row.get("user_token")
    if not token:
        # Only reachable if the row was closed concurrently; nothing to do.
        logger.warning("videogen: %s has no user token — skipping post", video_id)
        return

    await spacetime.add_draft_post(
        video_id=video_id,
        owner_of_video=user_id,
        prompt=row["prompt"] or "",
        user_token=token,
    )
    await repository.mark_complete(pool, video_id=video_id, video_url=video_url)
    logger.info("videogen: %s complete", video_id)


async def _advance_one(pool, row: dict) -> None:
    video_id = row["video_id"]
    comfy_id = row.get("comfy_id")

    if not comfy_id:
        # Submission never got as far as recording an id. The stale sweep will
        # retire it; polling is impossible without a prompt id.
        return

    state, file_ref = await comfyui.poll(comfy_id)
    if state == "pending":
        return
    if state == "failed":
        await repository.mark_failed(
            pool, video_id=video_id, reason="generation failed"
        )
        logger.warning("videogen: %s failed at ComfyUI", video_id)
        return

    # Everything above is idempotent and safe to duplicate; everything below is
    # not. Take the claim here, so the exclusive window is the few seconds the
    # finish takes rather than the whole generation.
    if not await repository.claim_for_finish(
        pool, video_id=video_id, lease_seconds=config.VIDEOGEN_CLAIM_LEASE_SECONDS
    ):
        return  # another worker is finishing this one

    try:
        await _finish(pool, row, file_ref)
    except Exception as e:
        # The video may well be stored already; what failed is downstream of it.
        # Record the failure so the spinner clears, and report it — a run of
        # these means SpacetimeDB is refusing us, not that generation broke.
        logger.error("videogen: %s finish failed: %s", video_id, e)
        sentry_sdk.capture_exception(e)
        await repository.mark_failed(pool, video_id=video_id, reason=str(e))


async def tick(pool) -> None:
    """One pass. Separated from the loop so it can be driven from a test or an
    admin endpoint without waiting on the interval."""
    await repository.fail_stale(
        pool, older_than_seconds=config.VIDEOGEN_STALE_AFTER_SECONDS
    )
    for row in await repository.list_pending(pool):
        try:
            await _advance_one(pool, row)
        except Exception as e:
            logger.error("videogen: tick failed for %s: %s", row.get("video_id"), e)
            sentry_sdk.capture_exception(e)


async def run_forever() -> None:
    """Background task started from the app lifespan.

    On by default. ENABLE_VIDEOGEN_LOOP=false stops it — checked every tick
    rather than once at startup, so stopping and restarting is an env change,
    not a redeploy.
    """
    from database import get_pool
    from kill_switch import is_enabled

    logger.info(
        "videogen: poll loop started (every %ds)", config.VIDEOGEN_POLL_INTERVAL_SECONDS
    )
    while True:
        try:
            if not is_enabled("videogen"):
                await asyncio.sleep(config.VIDEOGEN_POLL_INTERVAL_SECONDS)
                continue
            pool = await get_pool()
            await tick(pool)
        except Exception as e:
            # Never let the loop die — a transient DB blip must not require a
            # redeploy to resume generations.
            logger.error("videogen: poll loop error: %s", e)
            sentry_sdk.capture_exception(e)
        await asyncio.sleep(config.VIDEOGEN_POLL_INTERVAL_SECONDS)
