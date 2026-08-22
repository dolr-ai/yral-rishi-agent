"""Where a finished video lands.

**The object key is the contract, not the URL.** The mobile app never reads a
video URL from us — it builds one from the post's `video_uid` and creator
principal (`IndividualUserDataSourceImpl.videoUrl`):

    https://cdn-yral-sfw.yral.com/{principal}/{video_id}.mp4
    https://cdn-yral-sfw.yral.com/{principal}/{video_id}-thumbnail.png

So a generated video is only playable if it sits at exactly `{principal}/{video_id}`
in whatever bucket that CDN hostname fronts. Changing bucket or provider is a
config + CDN-origin change; changing the *key layout* is a mobile release.

The thumbnail is not optional. Every draft card and feed card fetches the
`-thumbnail.png` sibling, so skipping it means broken images everywhere rather
than a missing nicety.

Reuses the Storj gateway client from `services/storage.py` — same credentials
and endpoint as chat media and profile pictures, only the bucket differs.
"""

import asyncio
import logging
import tempfile

import config
from services import storage

logger = logging.getLogger(__name__)

THUMBNAIL_SUFFIX = "-thumbnail.png"


class ThumbnailError(RuntimeError):
    """ffmpeg could not produce a first frame."""


def video_key(user_id: str, video_id: str) -> str:
    return f"{user_id}/{video_id}.mp4"


def thumbnail_key(user_id: str, video_id: str) -> str:
    return f"{user_id}/{video_id}{THUMBNAIL_SUFFIX}"


def public_url(key: str) -> str:
    return f"{config.VIDEOGEN_PUBLIC_URL_BASE.rstrip('/')}/{key}"


async def extract_thumbnail(video_bytes: bytes) -> bytes:
    """First frame as PNG.

    Same single-frame extraction the old Rust service did, and like it we hand
    ffmpeg a **file path** rather than piping the video in on stdin. MP4 keeps
    its `moov` index atom at the end of the file, so a decoder has to seek back
    after reading it; on a pipe it cannot, and ffmpeg fails with "Cannot
    determine format of input after EOF". Verified inside the runtime image.

    Output still goes to stdout — PNG is written linearly, so that direction is
    safe and saves a second temp file.
    """
    with tempfile.NamedTemporaryFile(suffix=".mp4") as source:
        source.write(video_bytes)
        source.flush()
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            source.name,
            "-vframes",
            "1",
            "-f",
            "image2",
            "-c:v",
            "png",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
    if proc.returncode != 0 or not out:
        raise ThumbnailError((err or b"").decode("utf-8", "replace")[:300])
    return out


def _put(key: str, body: bytes, content_type: str) -> None:
    client = storage._get_s3_client()  # shared Storj gateway client
    if not client:
        raise RuntimeError("videogen storage not configured")
    client.put_object(
        Bucket=config.VIDEOGEN_S3_BUCKET,
        Key=key,
        Body=body,
        ContentType=content_type,
    )


async def store(user_id: str, video_id: str, video_bytes: bytes) -> str:
    """Write the video and its thumbnail, and return the video's public URL.

    The thumbnail goes first: a video with no thumbnail renders as a broken card,
    whereas a thumbnail with no video is never referenced, because nothing links
    to the post until `add_post` runs after this returns.
    """
    thumb = await extract_thumbnail(video_bytes)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, _put, thumbnail_key(user_id, video_id), thumb, "image/png"
    )
    await loop.run_in_executor(
        None, _put, video_key(user_id, video_id), video_bytes, "video/mp4"
    )

    logger.info(
        "videogen: stored %s (%d bytes video, %d bytes thumbnail)",
        video_id,
        len(video_bytes),
        len(thumb),
    )
    return public_url(video_key(user_id, video_id))
