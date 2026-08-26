"""Video generation endpoints.

Five routes, matching the paths and payloads the mobile app already sends to the
departed `storage-interface`. Auth is the yral-auth v2 JWT — the old service
wanted a chain-verified `delegated_identity` in the request body, which is
exactly what broke every one of these calls when the app moved to bearer tokens.

Error responses are shaped for the app's `VideoGenErrorDtoSerializer`: a single
key whose value is a plain string. Anything else falls through to its raw-text
branch and the user is shown a blob of JSON.
"""

import base64
import binascii
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import config
from auth import get_current_user
from database import get_pool
from repositories import influencer_repo
from videogen import comfyui, models, prompt_check, repository, spacetime

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Video generation"])

PROVIDER_ID = "ltx2"


def _bearer(request: Request) -> str:
    """The caller's raw token, forwarded to SpacetimeDB later so the post is
    written as them. `get_current_user` has already verified it."""
    header = request.headers.get("Authorization", "")
    return header[7:] if header[:7].lower() == "bearer " else ""


def _error(status: int, variant: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={variant: message})


def _decode_image(image: models.ImagePayload) -> bytes:
    raw = image.value.data
    if raw.startswith("data:"):  # tolerate a data-URL prefix
        comma = raw.find(",")
        raw = raw[comma + 1 :] if comma >= 0 else raw
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("image is not valid base64") from e


@router.get("/api/v2/videogen/providers")
async def list_providers():
    return {"providers": models.public_providers()}


@router.get("/api/v2/videogen/providers-all")
async def list_all_providers():
    """Internal builds show disabled/experimental entries too."""
    return {"providers": models.ALL_PROVIDERS}


@router.post("/api/v2/videogen/generate")
async def generate_video(body: models.GenerateRequest, request: Request):
    owner_id = get_current_user(request)
    req = body.request

    if not (req.prompt or "").strip():
        return _error(400, "InvalidInput", "Add a prompt to create a video.")

    if req.model_id != PROVIDER_ID:
        return _error(400, "UnsupportedModel", req.model_id)

    image_bytes: bytes | None = None
    if req.image is not None:
        try:
            image_bytes = _decode_image(req.image)
        except ValueError:
            return _error(400, "InvalidInput", "That image couldn't be read.")
        if len(image_bytes) > config.MAX_IMAGE_SIZE_BYTES:
            return _error(
                400,
                "InvalidInput",
                f"That image is too large. Max {config.MAX_IMAGE_SIZE_MB}MB.",
            )

    # `bot_id` (wire name `user_id`) is the AI influencer the video is FOR. It is
    # normally NOT the caller — a human owner generating for their bot — so a
    # mismatch is the expected case, not an error. What must be checked is that
    # the caller owns that bot, otherwise anyone knowing a bot id could generate
    # into someone else's account. Ordered after the pure validations so a
    # malformed request never reaches the database.
    pool = await get_pool()
    if req.bot_id and req.bot_id != owner_id:
        if await influencer_repo.get_parent_principal(pool, req.bot_id) != owner_id:
            logger.warning(
                "videogen: %s tried to generate for bot %s it does not own",
                owner_id,
                req.bot_id,
            )
            return _error(401, "AuthError", "You don't have access to that profile.")

    # One multimodal call covers the prompt and the image together. Fails
    # closed — see prompt_check.
    if not await prompt_check.is_safe(req):
        return _error(400, "InvalidInput", prompt_check.REJECTION_MESSAGE)

    # One id serves as operation id, storage object name and post id.
    video_id = str(uuid.uuid4())

    # Recorded before anything is submitted, so a crash between here and the
    # ComfyUI call leaves a row the sweep can retire rather than a ghost job.
    await repository.create_pending(
        pool,
        user_id=req.bot_id or owner_id,
        video_id=video_id,
        prompt=req.prompt,
        model_id=req.model_id,
        user_token=_bearer(request),
    )

    try:
        image_filename = None
        if image_bytes is not None:
            image_filename = await comfyui.upload_image(
                image_bytes, f"{video_id}-source"
            )
        graph = comfyui.build_workflow(
            prompt=req.prompt,
            duration_seconds=req.duration_seconds,
            image_filename=image_filename,
        )
        comfy_id = await comfyui.submit(graph)
    except comfyui.ComfyUnavailable as e:
        logger.error("videogen: submit failed for %s: %s", video_id, e)
        await repository.mark_failed(pool, video_id=video_id, reason=str(e))
        return _error(
            502, "ProviderError", "Video generation is unavailable right now."
        )

    await repository.attach_comfy_id(pool, video_id=video_id, comfy_id=comfy_id)
    logger.info("videogen: queued %s as comfy %s", video_id, comfy_id)

    return models.GenerateResponse(operation_id=video_id, provider=PROVIDER_ID)


@router.post("/api/v2/videogen/drafts/in-progress")
async def in_progress_drafts(body: models.InProgressDraftsRequest, request: Request):
    """Polled by the Drafts tab while a generation runs.

    Scoped to `bot_id` (wire name `user_id`) when the caller owns that bot, so a
    bot's spinner shows its own generations. Falls back to the caller's own rows
    when no bot is named."""
    owner_id = get_current_user(request)
    pool = await get_pool()
    subject = owner_id
    if body.bot_id and body.bot_id != owner_id:
        if await influencer_repo.get_parent_principal(pool, body.bot_id) != owner_id:
            raise HTTPException(status_code=401, detail="Not your profile")
        subject = body.bot_id
    rows = await repository.list_in_progress(pool, user_id=subject)
    return models.InProgressDraftsResponse(
        items=[
            models.InProgressDraftItem(
                operation_id=r["video_id"],
                status="in_progress",
                created_at=r["created_at"].isoformat(),
                model_id=r["model_id"],
                prompt=r["prompt"],
                provider=PROVIDER_ID,
                thumbnail_url=None,
            )
            for r in rows
        ]
    )


@router.post("/mark-post-as-published")
async def mark_post_as_published(
    body: models.MarkPostAsPublishedRequest, request: Request
):
    """Publish a draft. The app reads this response as plain text, not JSON."""
    get_current_user(request)
    try:
        await spacetime.publish_post(post_id=body.post_id, user_token=_bearer(request))
    except spacetime.SpacetimeError as e:
        logger.error("videogen: publish failed for %s: %s", body.post_id, e)
        raise HTTPException(status_code=502, detail="Failed to publish")
    return PlainTextResponse("ok")
