"""Gaussian blur helper for Request Images pre-blurred variants.

Applied at collage-generate time to the 6 clear outputs so the
paywall gate has separate URLs to serve: `image_urls` (clear) go to
subscribers, `image_urls_blurred` (blurred JPEGs stored in our own
S3 bucket) go to everyone else. The client never receives clear
pixels unless the subscription flag says so — a modified app can
spoof the flag but that's a Play Store IAP-receipt enforcement
concern, not our backend's.

Blur radius chosen so the underlying scene is fully unrecognizable
(no face, no body silhouette detectable) but the color palette +
composition are preserved as a "teaser" — matches the design intent
of "enticing users to subscribe."
"""

import io
import logging

logger = logging.getLogger(__name__)

# 30 px is enough to make faces + fashion details unrecognizable at
# any mobile-screen zoom (empirical from 2026-07-08 test on Tara
# Dubai batch). Bumped from a lower default because 20 px still left
# outlines visible. Env-tunable via COLLAGE_BLUR_RADIUS if the
# product team wants a stronger or weaker tease.
_DEFAULT_BLUR_RADIUS_PX = 30

# JPEG quality after re-encoding. Blurred images have almost no
# high-frequency detail to preserve → 60 is plenty and cuts file
# size by half vs the 85 default we use for clear outputs. Cheaper
# S3 bytes = smaller mobile downloads on the free tier.
_BLURRED_JPEG_QUALITY = 60


def gaussian_blur_jpeg(
    clear_bytes: bytes, radius: int = _DEFAULT_BLUR_RADIUS_PX
) -> bytes:
    """Apply a Gaussian blur to a JPEG byte string and return the
    re-encoded JPEG bytes. Runs synchronously — Pillow is CPU-bound;
    callers on the request path should invoke via asyncio.to_thread
    to avoid blocking the event loop."""
    from PIL import Image, ImageFilter  # deferred import — Pillow is heavy

    img = Image.open(io.BytesIO(clear_bytes))
    # Convert to RGB up-front — some Replicate outputs come through
    # with color modes that PIL can't save back as JPEG (e.g. RGBA).
    if img.mode != "RGB":
        img = img.convert("RGB")
    blurred = img.filter(ImageFilter.GaussianBlur(radius=radius))
    out = io.BytesIO()
    blurred.save(out, format="JPEG", quality=_BLURRED_JPEG_QUALITY, optimize=True)
    return out.getvalue()
