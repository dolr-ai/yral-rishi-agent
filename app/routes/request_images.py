"""Phase 0 Request Images track B — user-facing endpoints.

Contract per brief:
  POST /api/v1/influencers/{influencer_id}/request-images
    Auth: JWT. Body: {} (empty). Consumes daily quota. Runs the
    reservation-row race lock (image_collage.orchestrate). Response:
      200 {images: [...], is_blurred: bool, theme, generated_at?}
      202 {status: 'pending'}                   -- winner still generating
      429 {error: 'already_requested_today', resets_at}
      502 {error: 'generation_failed', reason}
      503 {error: 'budget_hard_cap'}

  GET /api/v1/influencers/{influencer_id}/collage
    Auth: JWT. Idempotent read — never consumes quota. Serves today's
    collage if it exists (state=succeeded), else 404 if the bot has
    no collage yet today. Same {images, is_blurred, ...} shape.

Blur decision: server-side via subscription_stub.is_subscribed —
route sets `is_blurred: not is_subscribed(user_id)`. Mobile handles
rendering (design §5 stubs the actual blur on the client for Phase
0 per brief; real pre-blurred blob variants ship in Phase 1).
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import config
from auth import get_current_user
from database import get_pool
from services import image_collage, subscription_stub, theme_generator


class RequestImagesBody(BaseModel):
    """Optional client hint for the paywall gate. Rishi choice
    2026-07-08: mobile knows the current subscription state via
    Play Store IAP; sending it in the request body lets the backend
    decide which URL set (clear vs pre-blurred) to serve without
    having to call billing.yral.com on every request. Absent = fall
    back to subscription_stub (YRAL team allowlist)."""

    is_subscribed: bool | None = None


logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat v1 — Request Images"])


async def _resolve_theme(pool, influencer_id: str) -> str:
    """LLM-generated fresh theme per batch (Rishi choice 2026-07-07,
    superseding the design's §1b DB-rotation plan). Falls back to
    config.COLLAGE_THEME_TARA if theme_generator can't produce a
    valid theme — never blocks the user path on LLM outages."""
    return await theme_generator.generate_daily_theme(pool, influencer_id)


def _resolve_lora(pool_result_row) -> str | None:
    """Placeholder for the Phase 0 LoRA URL wiring. Session 6 flips
    config.COLLAGE_LORA_WEIGHTS_URL once Tara's LoRA training
    completes; per-bot column plumbing is a Phase 1 concern (the
    ai_influencers.lora_weights_url column landed in migration 046
    but no route currently reads it)."""
    return config.COLLAGE_LORA_WEIGHTS_URL


def _envelope_for_ready(
    collage: dict, user_id: str, is_subscribed: bool | None
) -> dict:
    """Shared response builder for POST + GET happy paths.

    Blur decision (design §5 + 2026-07-08 Rishi choice):
      - `is_subscribed` provided by client → trust it
      - `is_subscribed` absent → fall back to subscription_stub
        (Phase 0 YRAL-team allowlist)

    URL selection:
      - Subscribed → clear `image_urls`
      - Not subscribed → pre-blurred `image_urls_blurred` if present,
        else fall back to `image_urls` (only happens for pre-blur-
        migration collage rows — new rows always have both arrays).

    Response includes `collage_bot_id` + `collage_date` so the mobile
    client can store JUST the reference in the chat message and
    refetch on subscription transitions ("self-healing historical
    messages" — design §5 discussion 2026-07-08). Never store the
    URLs in the message payload; the client's cache key is
    (collage_bot_id, collage_date, is_subscribed)."""
    resolved = (
        is_subscribed
        if is_subscribed is not None
        else subscription_stub.is_subscribed(user_id)
    )
    blurred = list(collage.get("image_urls_blurred") or [])
    clear = list(collage.get("image_urls") or [])
    if resolved:
        images = clear
    else:
        images = blurred if blurred else clear
    generation_date = collage.get("generation_date") or collage.get("collage_date")
    return {
        "images": images,
        "is_blurred": not resolved,
        "theme": collage["theme"],
        "generated_at": collage.get("generated_at"),
        "collage_bot_id": collage.get("bot_id"),
        "collage_date": (
            generation_date.isoformat()
            if hasattr(generation_date, "isoformat")
            else generation_date
        ),
    }


@router.post("/api/v1/influencers/{influencer_id}/request-images", status_code=200)
async def request_images(
    influencer_id: str,
    request: Request,
    body: RequestImagesBody | None = None,
) -> dict:
    """User tapped Request Images in the chat menu. Consumes today's
    quota + resolves via image_collage.orchestrate.

    Accepts optional `{is_subscribed: bool}` in body. Backend uses it
    to pick which URL set to return (clear vs pre-blurred). Absent =
    fallback to subscription_stub (Phase 0 YRAL-team allowlist)."""
    user_id = get_current_user(request)
    pool = await get_pool()
    theme = await _resolve_theme(pool, influencer_id)
    lora = _resolve_lora(None)
    is_subscribed = body.is_subscribed if body else None

    result = await image_collage.orchestrate(
        pool,
        user_id=user_id,
        bot_id=influencer_id,
        theme=theme,
        lora_weights_url=lora,
        consume_quota=True,
    )
    # Ensure result carries bot_id + generation_date so the response
    # envelope can echo them back for the mobile message reference.
    if result.get("status") == "ready" and "bot_id" not in result:
        result["bot_id"] = influencer_id
    return _map_result(result, user_id, is_subscribed)


@router.get("/api/v1/influencers/{influencer_id}/collage", status_code=200)
async def get_collage(
    influencer_id: str,
    request: Request,
    is_subscribed: bool | None = None,
) -> dict:
    """Idempotent read — used by mobile for polling + reload +
    render-time refetch (design §5 self-healing pattern). Never
    consumes quota, never elects a generator: if today's collage
    isn't ready yet, 404.

    `is_subscribed` query param drives the blur decision so the same
    collage row can serve subscribers (clear) and non-subscribers
    (pre-blurred) without duplicating storage or state. Historical
    messages that stored just `(collage_bot_id, collage_date)` fetch
    via this endpoint with the CURRENT subscription state — after a
    user subscribes, every historical collage message re-renders
    clear."""
    user_id = get_current_user(request)
    pool = await get_pool()

    from datetime import datetime, timezone

    from repositories import influencer_collage_repo

    today = datetime.now(timezone.utc).date()
    row = await influencer_collage_repo.get(pool, influencer_id, today)
    if row is None or row["state"] != "succeeded":
        raise HTTPException(status_code=404, detail="no collage yet today")
    return _envelope_for_ready(row, user_id, is_subscribed)


def _map_result(result: dict, user_id: str, is_subscribed: bool | None) -> dict:
    """Convert the orchestrate() envelope into an HTTP response.
    Splitting this out keeps the two route handlers readable."""
    status = result.get("status")

    if status == "ready":
        return _envelope_for_ready(result, user_id, is_subscribed)

    if status == "pending":
        # 202: winner is still generating. Mobile keeps its shimmer up
        # and polls GET /collage per design §7.
        raise HTTPException(status_code=202, detail="collage still generating")

    if status == "rate_limited":
        raise HTTPException(
            status_code=429,
            detail={
                "error": "already_requested_today",
                "resets_at": result.get("resets_at"),
            },
        )

    if status == "failed":
        reason = result.get("reason") or "unknown"
        if reason == "budget_hard_cap":
            raise HTTPException(status_code=503, detail="daily image budget reached")
        raise HTTPException(
            status_code=502,
            detail={"error": "generation_failed", "reason": reason},
        )

    # Defensive: an unknown status is a code bug on our side.
    logger.error("request_images: unknown orchestrate status %r", status)
    raise HTTPException(status_code=500, detail="internal")
