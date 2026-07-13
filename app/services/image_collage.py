"""Phase 0 Request Images track B — collage orchestration.

Wraps the reservation-row race lock (design §1a) into one entry
point the route layer calls. Contract:

  orchestrate(pool, user_id, bot_id, theme) ->
      {"status": "ready" | "pending" | "failed", "collage": {...}?, ...}

  * "ready"   — collage is populated and safe to serve. Route wraps
    with subscription-gated blur decision + returns it to mobile.
  * "pending" — another concurrent requester is generating; we timed
    out polling. Route returns 202 so mobile can retry / show
    "still cooking" copy.
  * "failed"  — generation failed content-safety or a hard cost cap
    tripped. Route returns 502.

State machine (order matters — brief-locked):

  1. try_record on user_image_requests → False = rate-limit hit; the
     route raises 429 without ever touching the collage row.

  2. get() on today's collage:
       state='succeeded' → cache hit, return "ready"
       state='reserved'  → other requester is generating; poll
       state='failed'    → serve most-recent succeeded (2026-07-13
                           hardening — see _fallback_or_failed); only
                           bubble "failed" if no recent success exists.

  3. If no row exists yet, reserve():
       True  → elected generator; run the batch, complete or fail.
       False → someone else JUST reserved; fall through to poll.

  4. Poll loop for the "not the winner" branches — sleep +
     COLLAGE_POLL_INTERVAL_SEC, check state, stop when succeeded
     or timeout hits.

Budget guard: the elected generator checks the day's summed
cost_usd against COLLAGE_DAILY_BUDGET_HARD_USD BEFORE spending; a
soft-alert log line fires on crossing COLLAGE_DAILY_BUDGET_SOFT_USD.
Both are hot-editable env knobs (Rishi's ADHD-observability rule).

Content-safety: Replicate's own safety refusal manifests as
`generate_batch` returning fewer URLs than requested (design §2.5).
When len(urls) < COLLAGE_IMAGE_COUNT we mark the row failed and
return "failed" — no partial cache poisoning.
"""

import asyncio
import logging
from datetime import date, datetime, timezone

import httpx

import config
from repositories import influencer_collage_repo, user_image_request_repo
from services import image_blur, replicate, storage

logger = logging.getLogger(__name__)


# Cap the concurrent blur uploads per collage so a slow S3 or a
# CPU-bound Pillow spike doesn't monopolise the event loop when 6
# variants land at once. Six is fine to run in parallel on modern
# containers but leaving room for future N > 6 batches.
_BLUR_UPLOAD_CONCURRENCY = 4


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


async def _daily_cost_usd(pool) -> float:
    """Sum of today's cost_usd across all bots. Feeds the soft-alert
    log line + the hard-cap guard below."""
    row = await pool.fetchrow(
        """
        SELECT COALESCE(SUM(cost_usd), 0)::float AS total
        FROM influencer_collages
        WHERE generation_date = $1
        """,
        _today_utc(),
    )
    return float(row["total"]) if row else 0.0


async def _check_budget(pool) -> bool:
    """Return True if under the hard cap. Sentry-log the crossing of
    either threshold. Best-effort: any DB error → True (fail-open),
    the request path is more important than perfect accounting."""
    try:
        spent = await _daily_cost_usd(pool)
    except Exception as e:
        logger.warning("collage budget check skipped (%s)", e)
        return True

    if spent >= config.COLLAGE_DAILY_BUDGET_HARD_USD:
        logger.error(
            "collage HARD cap tripped: spent=$%.2f cap=$%.2f",
            spent,
            config.COLLAGE_DAILY_BUDGET_HARD_USD,
        )
        return False
    if spent >= config.COLLAGE_DAILY_BUDGET_SOFT_USD:
        logger.warning(
            "collage SOFT cap crossed: spent=$%.2f threshold=$%.2f",
            spent,
            config.COLLAGE_DAILY_BUDGET_SOFT_USD,
        )
    return True


def _sign_stored(stored: str) -> str:
    """Turn a stored `image_urls`/`image_urls_blurred` entry into a
    fresh URL. Post-2026-07-09 entries are S3 keys (relative paths
    like `collage-clear/{bot}/{date}/{i}.jpg`) — `generate_presigned_url`
    signs a 15-min URL from the key. Legacy entries (pre-fix rows)
    are raw URLs — `generate_presigned_url` returns storjshare hosts
    as-is (already-expired signatures — user sees a broken image on
    the legacy row, but new rows work). Replicate URLs get rejected
    by the storage helper (not in allowed_hosts), returning empty
    string — the safest behavior for a legacy Replicate URL that has
    almost certainly already been reaped by Replicate."""
    return storage.generate_presigned_url(stored)


def _ready_response(collage: dict) -> dict:
    """Envelope carries `id` + `bot_id` + `generation_date` so the
    route layer's response echoes back all three — mobile stores the
    opaque UUID `collage_id` in the chat-message payload (design §5
    self-healing pattern, 2026-07-09 refactor). `bot_id` +
    `generation_date` are also included for legacy clients and for
    debugging (the UUID alone is opaque).

    URL signing (2026-07-09 second fix): the stored `image_urls` +
    `image_urls_blurred` arrays now hold S3 KEYS, not signed URLs.
    Signing at read time means every response carries a fresh 15-min
    signature — the earlier design baked the signature into the DB
    row at generation time, which meant rows served hours after
    generation returned already-expired URLs (real bug caught during
    the 2026-07-09 Sarvesh integration verify)."""
    gen_date = collage.get("generation_date")
    return {
        "status": "ready",
        "id": (str(collage.get("id")) if collage.get("id") is not None else None),
        "bot_id": collage.get("bot_id"),
        "generation_date": (
            gen_date.isoformat() if hasattr(gen_date, "isoformat") else gen_date
        ),
        "theme": collage["theme"],
        "image_urls": [_sign_stored(k) for k in (collage["image_urls"] or [])],
        "image_urls_blurred": [
            _sign_stored(k) for k in (collage.get("image_urls_blurred") or [])
        ],
        "generated_at": (
            collage["generated_at"].isoformat() if collage.get("generated_at") else None
        ),
    }


async def _fallback_or_failed(pool, bot_id: str, reason: str) -> dict:
    """Shared fallback lookup for the two "today failed" branches:
      1. orchestrate() reads an existing state='failed' row
      2. _poll_for_winner() watches the elected generator flip to
         state='failed'

    Behavior: look up the bot's most-recent succeeded row within the
    configured window; serve it as a ready response with a warning
    log so the fallback firing shows up in ops. Fall back to the
    original "failed" envelope only when no recent success exists —
    that way a systemic outage still bubbles up visibly.

    Set COLLAGE_FALLBACK_MAX_DAYS=0 to disable this behavior entirely
    (paranoid switch — reverts to the pre-2026-07-13 behavior)."""
    fallback = await influencer_collage_repo.get_latest_succeeded(
        pool, bot_id, within_days=config.COLLAGE_FALLBACK_MAX_DAYS
    )
    if fallback is None:
        return {"status": "failed", "reason": reason}
    logger.warning(
        "collage: today failed for bot=%s (reason=%s), serving fallback id=%s from %s",
        bot_id,
        reason,
        fallback.get("id"),
        fallback.get("generation_date"),
    )
    return _ready_response(fallback)


async def _poll_for_winner(pool, bot_id: str, generation_date: date) -> dict:
    """Sleep-and-check until the elected generator flips the row to
    succeeded or the timeout fires. Returns a "ready" / "pending" /
    "failed" envelope. A `state='failed'` from the winner triggers
    the fallback path (see _fallback_or_failed) so a losing polling
    requester gets the same graceful degrade as the elected one."""
    deadline = asyncio.get_event_loop().time() + config.COLLAGE_POLL_TIMEOUT_SEC
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(config.COLLAGE_POLL_INTERVAL_SEC)
        current = await influencer_collage_repo.get(pool, bot_id, generation_date)
        if current is None:
            # Winner crashed hard enough to lose the row (shouldn't
            # happen — reservation rows survive rollbacks). Give up.
            return await _fallback_or_failed(pool, bot_id, "reservation_lost")
        if current["state"] == "succeeded":
            return _ready_response(current)
        if current["state"] == "failed":
            return await _fallback_or_failed(pool, bot_id, "generator_failed")
    return {"status": "pending"}


async def _mirror_and_blur_variant(
    http: httpx.AsyncClient,
    replicate_url: str,
    bot_id: str,
    generation_date: date,
    index: int,
) -> tuple[str | None, str | None]:
    """Mirror one Replicate output to our S3 in both clear + blurred
    variants. Downloads once, uploads twice. Returns (clear_key,
    blurred_key) — both S3 relative paths that get signed at read
    time via `_ready_response`. Returns (None, None) on any failure.

    Why store keys, not URLs (2026-07-09 fix): the earlier design
    stored presigned URLs directly in the DB, which meant the
    signature baked in at generation time expired 15 min later. A
    row served hours later returned already-dead URLs. Now we store
    the KEY (which never expires) and sign fresh on each response.

    Why also mirror clear (2026-07-09 fix, part 2): the earlier
    design stored raw `https://replicate.delivery/...` URLs in
    `image_urls`. Replicate reaps those after ~2 hours per their
    retention policy, so subscribers received 404s on any collage
    older than that. Now we own the pixel bytes end-to-end."""
    try:
        r = await http.get(replicate_url, timeout=30)
        r.raise_for_status()
        clear_bytes = r.content

        # Upload the clear variant first so a downstream failure on
        # the blur step doesn't cost us the mirror.
        clear_key = (
            f"collage-clear/{bot_id}/{generation_date.isoformat()}/{index:02d}.jpg"
        )
        await storage.upload_at_key(clear_key, clear_bytes, content_type="image/jpeg")

        # Pillow's blur is CPU-bound; offload to a worker so we
        # don't stall the event loop while 6 variants churn.
        blurred_bytes = await asyncio.to_thread(
            image_blur.gaussian_blur_jpeg,
            clear_bytes,
            config.COLLAGE_BLUR_RADIUS_PX,
        )
        blurred_key = (
            f"collage-blurred/{bot_id}/{generation_date.isoformat()}/{index:02d}.jpg"
        )
        await storage.upload_at_key(
            blurred_key, blurred_bytes, content_type="image/jpeg"
        )
        return clear_key, blurred_key
    except Exception as e:
        logger.warning(
            "collage variant mirror failed: bot=%s idx=%d %s",
            bot_id,
            index,
            e,
        )
        return None, None


async def _mirror_batch(
    replicate_urls: list[str], bot_id: str, generation_date: date
) -> tuple[list[str], list[str]]:
    """Fan out `_mirror_and_blur_variant` across the batch under a
    concurrency cap. Preserves ordering so `image_urls_blurred[i]`
    corresponds to `image_urls[i]` — mobile relies on parallel
    arrays for the grid layout. Only variants that succeeded on BOTH
    the clear + blurred upload count in the returned lists."""
    sem = asyncio.Semaphore(_BLUR_UPLOAD_CONCURRENCY)

    async def _one(idx: int, url: str) -> tuple[str | None, str | None]:
        async with sem:
            async with httpx.AsyncClient() as http:
                return await _mirror_and_blur_variant(
                    http, url, bot_id, generation_date, idx
                )

    results = await asyncio.gather(*[_one(i, u) for i, u in enumerate(replicate_urls)])
    clear_keys = [r[0] for r in results if r[0] is not None]
    blurred_keys = [r[1] for r in results if r[1] is not None]
    return clear_keys, blurred_keys


async def _run_generation(
    pool, bot_id: str, generation_date: date, theme: str, lora_weights_url: str | None
) -> dict:
    """Elected-generator path: fire the batch, safety-filter,
    pre-blur variants, persist."""
    n = config.COLLAGE_IMAGE_COUNT
    urls = await replicate.generate_batch(theme, n=n, lora_weights_url=lora_weights_url)
    if len(urls) < n:
        # Replicate safety refusal or partial failure — design §2.5.
        # Don't cache a poisoned batch; mark failed so a follow-up
        # retry-elect can try tomorrow's theme.
        logger.warning(
            "collage generation short: bot=%s got=%d/%d — marking failed",
            bot_id,
            len(urls),
            n,
        )
        await influencer_collage_repo.mark_failed(pool, bot_id, generation_date)
        return {"status": "failed", "reason": "content_safety_or_partial"}

    # Mirror both variants to our S3 (2026-07-09 fix).
    # Previously we stored raw Replicate URLs in `image_urls` (which
    # Replicate reaps after ~2 h) and presigned Storj URLs in
    # `image_urls_blurred` (whose 15-min signature was baked in at
    # generation time, so rows served hours later returned dead
    # URLs). Now we mirror the pixels end-to-end into our own S3
    # under deterministic keys, store the KEYS in the DB, and sign
    # fresh URLs on every response via `_ready_response`.
    clear_keys, blurred_keys = await _mirror_batch(urls, bot_id, generation_date)
    if len(clear_keys) < n:
        # If we couldn't own the pixels, don't cache the row — the
        # Replicate URLs will 404 in a few hours anyway. Fail loud
        # and let a retry pick fresh pixels.
        logger.error(
            "collage mirror short: bot=%s clear=%d/%d — marking failed",
            bot_id,
            len(clear_keys),
            n,
        )
        await influencer_collage_repo.mark_failed(pool, bot_id, generation_date)
        return {"status": "failed", "reason": "s3_mirror_failed"}
    if len(blurred_keys) < len(clear_keys):
        logger.warning(
            "collage blur partial: bot=%s blur=%d/%d clear; non-subs will see fewer",
            bot_id,
            len(blurred_keys),
            len(clear_keys),
        )

    cost = config.COLLAGE_COST_PER_IMAGE_USD * n
    await influencer_collage_repo.complete(
        pool,
        bot_id,
        generation_date,
        clear_keys,
        cost_usd=cost,
        image_urls_blurred=blurred_keys,
    )
    fresh = await influencer_collage_repo.get(pool, bot_id, generation_date)
    return _ready_response(
        fresh
        or {
            "theme": theme,
            "image_urls": clear_keys,
            "image_urls_blurred": blurred_keys,
        }
    )


async def orchestrate(
    pool,
    *,
    user_id: str,
    bot_id: str,
    theme: str,
    lora_weights_url: str | None = None,
    consume_quota: bool = True,
) -> dict:
    """Top-level entry point for the POST route. See module docstring
    for the state-machine contract.

    consume_quota=False lets the GET /collage read path reuse the same
    orchestration path without burning the caller's daily quota (GET
    is idempotent by design §4)."""
    today = _today_utc()

    if consume_quota:
        accepted = await user_image_request_repo.try_record(
            pool, user_id, bot_id, today
        )
        if not accepted:
            return {"status": "rate_limited", "resets_at": _next_utc_midnight()}

    existing = await influencer_collage_repo.get(pool, bot_id, today)
    if existing is not None:
        if existing["state"] == "succeeded":
            return _ready_response(existing)
        if existing["state"] == "failed":
            # 2026-07-13: serve the most-recent succeeded row instead of
            # bubbling 502 while today is failed. Bounded lookup so a
            # multi-day outage still surfaces (see _fallback_or_failed).
            return await _fallback_or_failed(pool, bot_id, "generator_failed")
        # 'reserved' — poll for the other requester's result.
        return await _poll_for_winner(pool, bot_id, today)

    won = await influencer_collage_repo.reserve(pool, bot_id, today, theme)
    if not won:
        # Lost a photo-finish race to another concurrent requester.
        return await _poll_for_winner(pool, bot_id, today)

    # Elected generator — enforce the daily hard cap BEFORE spending.
    if not await _check_budget(pool):
        await influencer_collage_repo.mark_failed(pool, bot_id, today)
        return await _fallback_or_failed(pool, bot_id, "budget_hard_cap")

    # 2026-07-13: also apply the fallback when THIS requester was the
    # elected generator and their own batch just failed. Otherwise the
    # one user who tapped Request Images gets a raw 502 while everyone
    # after them (arriving to a state='failed' row) gets the fallback.
    result = await _run_generation(pool, bot_id, today, theme, lora_weights_url)
    if result.get("status") == "failed":
        return await _fallback_or_failed(
            pool, bot_id, result.get("reason", "generator_failed")
        )
    return result


def _next_utc_midnight() -> str:
    now = datetime.now(timezone.utc)
    tomorrow = date(now.year, now.month, now.day).toordinal() + 1
    reset = datetime.fromordinal(tomorrow).replace(tzinfo=timezone.utc)
    return reset.isoformat()
