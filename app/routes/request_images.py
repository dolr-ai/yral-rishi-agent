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

    URL selection:
      - Subscribed → clear `image_urls`
      - Not subscribed → pre-blurred `image_urls_blurred` if present,
        else fall back to `image_urls` (rollout window only)

    Response fields for mobile message payload (2026-07-09 refactor):
      - `collage_id` (UUID) — preferred handle stored in the chat
        message. Mobile refetches via GET ?collage_id=<uuid>.
      - `collage_bot_id` + `collage_date` — kept for legacy clients
        + human-debuggable identity. Also usable as a fallback
        lookup via GET ?date=<YYYY-MM-DD>.
    """
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
    collage_id_raw = collage.get("id") or collage.get("collage_id")
    return {
        "images": images,
        "is_blurred": not resolved,
        "theme": collage["theme"],
        "generated_at": collage.get("generated_at"),
        "collage_id": str(collage_id_raw) if collage_id_raw is not None else None,
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
    return _map_result(result, user_id, is_subscribed)


@router.get("/api/v1/influencers/{influencer_id}/collage", status_code=200)
async def get_collage(
    influencer_id: str,
    request: Request,
    is_subscribed: bool | None = None,
    collage_id: str | None = None,
    date: str | None = None,
) -> dict:
    """Idempotent read — used by mobile for polling + reload +
    render-time refetch (design §5 self-healing pattern). Never
    consumes quota, never elects a generator.

    Lookup precedence (2026-07-09 refactor):
      1. `?collage_id=<uuid>` — direct fetch by opaque handle.
         Preferred: mobile stores the UUID in the chat message.
      2. `?date=YYYY-MM-DD` — fetch by (bot_id, date). Fallback for
         legacy chat messages that predate the UUID field.
      3. Neither → today's UTC calendar date.

    `is_subscribed` drives the blur decision so the same collage row
    can serve subscribers (clear) + non-subscribers (pre-blurred).

    Returns 404 if the requested collage doesn't exist (never
    generated, or generation failed). Returns 400 if the date param
    is malformed (must be ISO 8601 YYYY-MM-DD)."""
    user_id = get_current_user(request)
    pool = await get_pool()

    from datetime import date as date_cls, datetime, timezone

    from repositories import influencer_collage_repo

    row = None
    if collage_id:
        row = await influencer_collage_repo.get_by_id(pool, collage_id)
        # Guard against a UUID from bot A being used to peek at bot B's
        # collages — the URL path pins the caller to influencer_id.
        if row and row.get("bot_id") != influencer_id:
            row = None
    else:
        if date:
            try:
                target = date_cls.fromisoformat(date)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="date must be ISO 8601 YYYY-MM-DD",
                )
        else:
            target = datetime.now(timezone.utc).date()
        row = await influencer_collage_repo.get(pool, influencer_id, target)

    if row is None or row.get("state") != "succeeded":
        raise HTTPException(status_code=404, detail="collage not found")
    # Chain through _ready_response to sign storage keys → 15-min
    # presigned URLs. Matches POST-path semantics (orchestrate already
    # runs the raw row through _ready_response before it reaches
    # _envelope_for_ready). 2026-07-14 Sarvesh integration bug: GET
    # was returning raw `collage-blurred/{bot}/{date}/{i}.jpg` bucket
    # keys — mobile can't render those. _envelope_for_ready reads
    # image_urls / image_urls_blurred by name so chaining preserves
    # the wire shape; the only difference is the list contents are
    # now signed URLs instead of raw keys.
    signed = image_collage._ready_response(row)
    return _envelope_for_ready(signed, user_id, is_subscribed)


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
