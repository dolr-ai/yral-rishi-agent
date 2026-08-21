"""Profile-picture storage on Hetzner Object Storage (public bucket).

Deliberately separate from `services/storage.py` (the Storj chat-media store):
profile-picture URLs are written to SpacetimeDB and rendered by clients forever,
so they must be durable + public — not the private, presigned model used for chat
media. The `yral-profile-pictures` bucket carries a public-read policy, so any
uploaded object is fetchable at `{PROFILE_PIC_PUBLIC_URL_BASE}/{key}` with no auth.
"""

import logging
import uuid

import boto3
from botocore.config import Config as BotoConfig

import config

logger = logging.getLogger(__name__)

# Magic-byte → extension. Defaults to jpg (the app sends JPEG avatars).
_CONTENT_TYPE = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "webp": "image/webp",
}

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not config.PROFILE_PIC_S3_ACCESS_KEY_ID:
        logger.warning("Profile-pic S3 not configured — profile uploads will fail")
        return None
    _client = boto3.client(
        "s3",
        endpoint_url=config.PROFILE_PIC_S3_ENDPOINT,
        aws_access_key_id=config.PROFILE_PIC_S3_ACCESS_KEY_ID,
        aws_secret_access_key=config.PROFILE_PIC_S3_SECRET_ACCESS_KEY,
        region_name=config.PROFILE_PIC_S3_REGION,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return _client


def detect_extension(image_bytes: bytes) -> str:
    """Sniff the image type from its magic bytes; default to jpg."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if image_bytes[:2] == b"\xff\xd8":
        return "jpg"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "webp"
    return "jpg"


def upload_profile_image(owner: str, image_bytes: bytes, ext: str) -> str:
    """Store the image publicly and return its durable public URL."""
    client = _get_client()
    if not client:
        raise RuntimeError("Profile-pic storage not configured")
    key = f"{owner}/{uuid.uuid4()}.{ext}"
    client.put_object(
        Bucket=config.PROFILE_PIC_S3_BUCKET,
        Key=key,
        Body=image_bytes,
        ContentType=_CONTENT_TYPE.get(ext, "image/jpeg"),
    )
    return f"{config.PROFILE_PIC_PUBLIC_URL_BASE}/{key}"
