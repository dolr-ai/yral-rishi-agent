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
       state='failed'    → return "failed" (retry-elect is Phase 1)

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


def _ready_response(collage: dict) -> dict:
    """Envelope carries `bot_id` + `generation_date` so the route
    layer's response echoes back both fields — mobile stores JUST
    those in the chat-message payload (design §5 self-healing
    pattern). The 2026-07-08 initial ship missed forwarding
    `generation_date` here, leaving the field `null` in the response;
    Sarvesh would have hit that on first integration."""
    gen_date = collage.get("generation_date")
    return {
        "status": "ready",
        "bot_id": collage.get("bot_id"),
        "generation_date": (
            gen_date.isoformat() if hasattr(gen_date, "isoformat") else gen_date
        ),
        "theme": collage["theme"],
        "image_urls": list(collage["image_urls"] or []),
        "image_urls_blurred": list(collage.get("image_urls_blurred") or []),
        "generated_at": (
            collage["generated_at"].isoformat() if collage.get("generated_at") else None
        ),
    }


async def _poll_for_winner(pool, bot_id: str, generation_date: date) -> dict:
    """Sleep-and-check until the elected generator flips the row to
    succeeded or the timeout fires. Returns a "ready" / "pending" /
    "failed" envelope."""
    deadline = asyncio.get_event_loop().time() + config.COLLAGE_POLL_TIMEOUT_SEC
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(config.COLLAGE_POLL_INTERVAL_SEC)
        current = await influencer_collage_repo.get(pool, bot_id, generation_date)
        if current is None:
            # Winner crashed hard enough to lose the row (shouldn't
            # happen — reservation rows survive rollbacks). Give up.
            return {"status": "failed", "reason": "reservation_lost"}
        if current["state"] == "succeeded":
            return _ready_response(current)
        if current["state"] == "failed":
            return {"status": "failed", "reason": "generator_failed"}
    return {"status": "pending"}


async def _download_and_blur_upload(
    http: httpx.AsyncClient,
    clear_url: str,
    bot_id: str,
    generation_date: date,
    index: int,
) -> str | None:
    """Fetch one Replicate output → Gaussian blur → upload to our S3
    under a deterministic key. Returns the blurred variant's URL
    (presigned or public). Best-effort: returns None on any error so
    the route can fall back to serving `image_urls` when a variant
    doesn't materialise (design §5 rollout — partial blur is not a
    hard fail)."""
    try:
        r = await http.get(clear_url, timeout=30)
        r.raise_for_status()
        clear_bytes = r.content
        # Pillow's blur is CPU-bound; offload to a worker so we
        # don't stall the event loop while 6 variants churn.
        blurred_bytes = await asyncio.to_thread(
            image_blur.gaussian_blur_jpeg,
            clear_bytes,
            config.COLLAGE_BLUR_RADIUS_PX,
        )
        key = f"collage-blurred/{bot_id}/{generation_date.isoformat()}/{index:02d}.jpg"
        await storage.upload_at_key(key, blurred_bytes, content_type="image/jpeg")
        return storage.generate_presigned_url(key)
    except Exception as e:
        logger.warning(
            "collage blur variant failed: bot=%s idx=%d %s",
            bot_id,
            index,
            e,
        )
        return None


async def _generate_blurred_variants(
    clear_urls: list[str], bot_id: str, generation_date: date
) -> list[str]:
    """Fan out `_download_and_blur_upload` across the batch. Preserves
    ordering so `image_urls_blurred[i]` corresponds to `image_urls[i]`
    — the mobile side relies on parallel arrays for the grid layout."""
    sem = asyncio.Semaphore(_BLUR_UPLOAD_CONCURRENCY)

    async def _one(idx: int, url: str) -> str | None:
        async with sem:
            async with httpx.AsyncClient() as http:
                return await _download_and_blur_upload(
                    http, url, bot_id, generation_date, idx
                )

    results = await asyncio.gather(*[_one(i, u) for i, u in enumerate(clear_urls)])
    return [r for r in results if r is not None]


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

    # Server-side pre-blur (design §5, Rishi 2026-07-08). Fetches
    # each clear URL, applies Pillow gaussian blur, uploads to our
    # own S3 bucket. Non-subscribers never receive clear pixels; the
    # blurred URLs are what get served to them, keyed by the same
    # collage row so the parallel arrays stay index-aligned. Partial
    # success is tolerated — if some variants fail to upload, the
    # route falls back to serving the clear URLs for those indices
    # (never worse than pre-blur behavior).
    blurred_urls = await _generate_blurred_variants(urls, bot_id, generation_date)
    if len(blurred_urls) < n:
        logger.warning(
            "collage blur partial: bot=%s got=%d/%d blurred; non-subs may see clear",
            bot_id,
            len(blurred_urls),
            n,
        )

    cost = config.COLLAGE_COST_PER_IMAGE_USD * n
    await influencer_collage_repo.complete(
        pool,
        bot_id,
        generation_date,
        urls,
        cost_usd=cost,
        image_urls_blurred=blurred_urls,
    )
    fresh = await influencer_collage_repo.get(pool, bot_id, generation_date)
    return _ready_response(
        fresh
        or {"theme": theme, "image_urls": urls, "image_urls_blurred": blurred_urls}
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
            return {"status": "failed", "reason": "generator_failed"}
        # 'reserved' — poll for the other requester's result.
        return await _poll_for_winner(pool, bot_id, today)

    won = await influencer_collage_repo.reserve(pool, bot_id, today, theme)
    if not won:
        # Lost a photo-finish race to another concurrent requester.
        return await _poll_for_winner(pool, bot_id, today)

    # Elected generator — enforce the daily hard cap BEFORE spending.
    if not await _check_budget(pool):
        await influencer_collage_repo.mark_failed(pool, bot_id, today)
        return {"status": "failed", "reason": "budget_hard_cap"}

    return await _run_generation(pool, bot_id, today, theme, lora_weights_url)


def _next_utc_midnight() -> str:
    now = datetime.now(timezone.utc)
    tomorrow = date(now.year, now.month, now.day).toordinal() + 1
    reset = datetime.fromordinal(tomorrow).replace(tzinfo=timezone.utc)
    return reset.isoformat()
