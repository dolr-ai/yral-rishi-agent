"""User profile-image upload.

Replaces the departed `storage-interface.prakash.yral.com` endpoint for bot
avatars: same path + request/response the mobile app already sends, but auth is
the yral-auth v2 JWT (no `delegated_identity_wire`, no IC canister write — that
legacy is what broke the old endpoint). We only store the bytes and hand back a
URL; the profile record itself lives in SpacetimeDB (`update_profile_details_v2`,
which the app calls separately right after).
"""

import base64
import binascii
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import config
from auth import get_current_user
from services import profile_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/user", tags=["User"])


class UploadProfileImageRequest(BaseModel):
    image_data: str


class UploadProfileImageResponse(BaseModel):
    profile_image_url: str


@router.post("/profile-image", response_model=UploadProfileImageResponse)
async def upload_profile_image(body: UploadProfileImageRequest, request: Request):
    owner = get_current_user(request)  # verifies the yral-auth JWT

    raw = body.image_data
    if raw.startswith("data:"):  # tolerate a data-URL prefix; app sends raw base64
        comma = raw.find(",")
        raw = raw[comma + 1 :] if comma >= 0 else raw
    try:
        image_bytes = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="image_data is not valid base64")
    if not image_bytes:
        raise HTTPException(status_code=422, detail="image_data is empty")
    if len(image_bytes) > config.MAX_IMAGE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large. Max: {config.MAX_IMAGE_SIZE_MB}MB",
        )

    ext = profile_storage.detect_extension(image_bytes)
    try:
        url = profile_storage.upload_profile_image(owner, image_bytes, ext)
    except Exception as e:
        logger.error(f"Profile image upload failed: {e}")
        raise HTTPException(status_code=502, detail="Failed to store profile image")

    return UploadProfileImageResponse(profile_image_url=url)
