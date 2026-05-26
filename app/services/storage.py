import uuid
import logging
from datetime import datetime

import boto3
from botocore.config import Config as BotoConfig

import config

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg"}

MIME_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg",
}


def _get_extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def mime_from_extension(ext: str) -> str:
    return MIME_TYPES.get(ext.lower(), "application/octet-stream")


_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is not None:
        return _s3_client

    if not config.AWS_ACCESS_KEY_ID or not config.AWS_S3_BUCKET:
        logger.warning("S3 not configured — media upload will not work")
        return None

    _s3_client = boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT_URL or None,
        aws_access_key_id=config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
        region_name=config.AWS_REGION,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    return _s3_client


async def upload(
    user_id: str,
    file_bytes: bytes,
    file_extension: str,
    content_type: str,
) -> tuple[str, int]:
    client = _get_s3_client()
    if not client:
        raise RuntimeError("S3 not configured")

    filename = f"{uuid.uuid4()}{file_extension}"
    key = f"{user_id}/{filename}"
    size = len(file_bytes)

    client.put_object(
        Bucket=config.AWS_S3_BUCKET,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
        ContentLength=size,
    )
    return (key, size)


def generate_presigned_url(key: str) -> str:
    if not key:
        return ""

    if key.startswith("http://") or key.startswith("https://"):
        from urllib.parse import urlparse
        host = urlparse(key).hostname or ""
        allowed_hosts = ["gateway.storjshare.io"]
        if any(host.endswith(h) for h in allowed_hosts):
            return key
        return ""

    client = _get_s3_client()
    if not client:
        return key

    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": config.AWS_S3_BUCKET, "Key": key},
            ExpiresIn=config.S3_URL_EXPIRES_SECONDS,
        )
        return url
    except Exception as e:
        logger.error(f"Failed to generate presigned URL for {key}: {e}")
        return key


def validate_image(filename: str, size: int):
    ext = _get_extension(filename)
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported image format. Allowed: {', '.join(sorted(IMAGE_EXTENSIONS))}")
    if size > config.MAX_IMAGE_SIZE_BYTES:
        raise ValueError(f"Image too large. Max: {config.MAX_IMAGE_SIZE_MB}MB")


def validate_audio(filename: str, size: int):
    ext = _get_extension(filename)
    if ext not in AUDIO_EXTENSIONS:
        raise ValueError(f"Unsupported audio format. Allowed: {', '.join(sorted(AUDIO_EXTENSIONS))}")
    if size > config.MAX_AUDIO_SIZE_BYTES:
        raise ValueError(f"Audio too large. Max: {config.MAX_AUDIO_SIZE_MB}MB")
