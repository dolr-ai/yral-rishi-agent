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

import config
from auth import get_current_user
from database import get_pool
from services import image_collage, subscription_stub

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat v1 — Request Images"])


def _resolve_theme(influencer_id: str) -> str:
    """Phase 0: single hardcoded theme for Tara. Phase 1 reads from
    influencer_collage_themes (see design §1b)."""
    return config.COLLAGE_THEME_TARA


def _resolve_lora(pool_result_row) -> str | None:
    """Placeholder for the Phase 0 LoRA URL wiring. Session 6 flips
    config.COLLAGE_LORA_WEIGHTS_URL once Tara's LoRA training
    completes; per-bot column plumbing is a Phase 1 concern (the
    ai_influencers.lora_weights_url column landed in migration 046
    but no route currently reads it)."""
    return config.COLLAGE_LORA_WEIGHTS_URL


def _envelope_for_ready(collage: dict, user_id: str) -> dict:
    """Shared response builder for POST + GET happy paths."""
    return {
        "images": collage["image_urls"],
        "is_blurred": not subscription_stub.is_subscribed(user_id),
        "theme": collage["theme"],
        "generated_at": collage.get("generated_at"),
    }


@router.post("/api/v1/influencers/{influencer_id}/request-images", status_code=200)
async def request_images(influencer_id: str, request: Request) -> dict:
    """User tapped Request Images in the chat menu. Consumes today's
    quota + resolves via image_collage.orchestrate."""
    user_id = get_current_user(request)
    pool = await get_pool()
    theme = _resolve_theme(influencer_id)
    lora = _resolve_lora(None)

    result = await image_collage.orchestrate(
        pool,
        user_id=user_id,
        bot_id=influencer_id,
        theme=theme,
        lora_weights_url=lora,
        consume_quota=True,
    )
    return _map_result(result, user_id)


@router.get("/api/v1/influencers/{influencer_id}/collage", status_code=200)
async def get_collage(influencer_id: str, request: Request) -> dict:
    """Idempotent read — used by mobile for polling + reload. Never
    consumes quota, never elects a generator: if today's collage
    isn't ready yet, 404."""
    user_id = get_current_user(request)
    pool = await get_pool()

    from datetime import datetime, timezone

    from repositories import influencer_collage_repo

    today = datetime.now(timezone.utc).date()
    row = await influencer_collage_repo.get(pool, influencer_id, today)
    if row is None or row["state"] != "succeeded":
        raise HTTPException(status_code=404, detail="no collage yet today")
    return _envelope_for_ready(row, user_id)


def _map_result(result: dict, user_id: str) -> dict:
    """Convert the orchestrate() envelope into an HTTP response.
    Splitting this out keeps the two route handlers readable."""
    status = result.get("status")

    if status == "ready":
        return _envelope_for_ready(result, user_id)

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
