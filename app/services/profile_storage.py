"""Profile-picture storage — a dedicated Storj bucket + public link-share.

Replaces the departed `storage-interface.prakash.yral.com` avatar upload. Reuses
the chat-media Storj client (`services/storage.py`) — same gateway creds, endpoint,
and the checksum config Storj's gateway requires — only the *bucket* differs, so no
new credentials are involved. Storj has no public-read policy, so the durable public
URL is the bucket's read-only, non-expiring `/raw/` link-share
(`PROFILE_PIC_PUBLIC_URL_BASE`) + the object key. We hold only the file; the profile
record itself lives in SpacetimeDB (`update_profile_details_v2`).
"""

import logging
import uuid

import config
from services import storage

logger = logging.getLogger(__name__)

_CONTENT_TYPE = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "webp": "image/webp",
}


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
    """Store the image in the public bucket and return its durable public URL."""
    client = storage._get_s3_client()  # same Storj client used for chat media
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
