"""Tests for the server-side blur + subscription-gated response
pipeline (PR #51, Rishi choice 2026-07-08).

Trust model under test:
  * client sends `is_subscribed` in request body / query param
  * backend picks clear vs pre-blurred URLs based on that flag
  * response includes `collage_bot_id` + `collage_date` so mobile
    can store just the reference in the chat message + refetch on
    subscription transitions
"""

import asyncio
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

MODULE_ROUTE = Path(__file__).parent.parent / "app" / "routes" / "request_images.py"
MODULE_COLLAGE = Path(__file__).parent.parent / "app" / "services" / "image_collage.py"


def _load(name: str):
    import sys, importlib

    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    return importlib.import_module(name)


def test_pillow_installed():
    """Pillow is the CPU-bound blur backend. Not being installable
    inside the image = every collage generation fails silently at
    the blur step (partial success falls back to serving clear URLs
    to non-subscribers = paywall bypass by omission)."""
    from PIL import Image, ImageFilter  # noqa: F401


def test_gaussian_blur_roundtrip():
    """Concrete blur function contract: takes JPEG bytes, returns
    smaller-or-equal JPEG bytes that a Pillow reopen recognises.
    Guards against a future 'refactor' that forgets JPEG encoding
    or drops the alpha->RGB conversion (some Replicate outputs come
    through as RGBA and PIL can't save those as JPEG directly)."""
    import io
    from PIL import Image

    ib = _load("services.image_blur")
    # Synthesize a small RGBA image so we exercise the conversion path
    src = Image.new("RGBA", (128, 128), (255, 0, 0, 255))
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    src_bytes = buf.getvalue()
    # gaussian_blur_jpeg accepts JPEG; feeding PNG works because
    # Image.open is format-agnostic. The output MUST be a valid JPEG.
    out = ib.gaussian_blur_jpeg(src_bytes, radius=10)
    assert len(out) > 0
    reopened = Image.open(io.BytesIO(out))
    assert reopened.format == "JPEG", f"blur output not a JPEG — got {reopened.format}"
    assert reopened.mode == "RGB", (
        f"blur output not RGB (subscriber alpha-channel edge case) — got {reopened.mode}"
    )


def test_envelope_subscribed_returns_clear_urls():
    """Trust the client flag when explicitly true → return clear URLs."""
    ri = _load("routes.request_images")
    collage = {
        "bot_id": "tara-uuid",
        "generation_date": date(2026, 7, 8),
        "theme": "TAARA on Santorini",
        "image_urls": ["https://clear/1.jpg", "https://clear/2.jpg"],
        "image_urls_blurred": ["https://blurred/1.jpg", "https://blurred/2.jpg"],
        "generated_at": None,
    }
    resp = ri._envelope_for_ready(collage, user_id="some-user", is_subscribed=True)
    assert resp["images"] == ["https://clear/1.jpg", "https://clear/2.jpg"]
    assert resp["is_blurred"] is False
    assert resp["collage_bot_id"] == "tara-uuid"
    assert resp["collage_date"] == "2026-07-08"


def test_envelope_not_subscribed_returns_blurred_urls():
    """Trust the client flag when explicitly false → return blurred."""
    ri = _load("routes.request_images")
    collage = {
        "bot_id": "tara-uuid",
        "generation_date": date(2026, 7, 8),
        "theme": "TAARA on Santorini",
        "image_urls": ["https://clear/1.jpg"],
        "image_urls_blurred": ["https://blurred/1.jpg"],
        "generated_at": None,
    }
    resp = ri._envelope_for_ready(collage, user_id="some-user", is_subscribed=False)
    assert resp["images"] == ["https://blurred/1.jpg"], (
        "non-subscriber received clear URLs — paywall bypassed via flag=False"
    )
    assert resp["is_blurred"] is True


def test_envelope_falls_back_to_clear_when_blurred_missing():
    """During the rollout window some old collage rows won't have
    blurred variants (they predate migration 047). For those, serving
    clear is the safe fallback — better than 500ing on the request
    path. The rollout completes when nightly pre-gen has regenerated
    every row with blur variants."""
    ri = _load("routes.request_images")
    collage = {
        "bot_id": "tara-uuid",
        "generation_date": date(2026, 7, 8),
        "theme": "TAARA on Santorini",
        "image_urls": ["https://clear/1.jpg"],
        "image_urls_blurred": [],
        "generated_at": None,
    }
    resp = ri._envelope_for_ready(collage, user_id="some-user", is_subscribed=False)
    assert resp["images"] == ["https://clear/1.jpg"]
    # is_blurred still True so mobile still renders the CTA overlay
    assert resp["is_blurred"] is True


def test_envelope_missing_flag_uses_subscription_stub():
    """Backwards compat: pre-#448 clients don't send the flag.
    Fallback to subscription_stub (Phase 0 YRAL-team allowlist)."""
    ri = _load("routes.request_images")
    collage = {
        "bot_id": "tara-uuid",
        "generation_date": date(2026, 7, 8),
        "theme": "TAARA on Santorini",
        "image_urls": ["https://clear/1.jpg"],
        "image_urls_blurred": ["https://blurred/1.jpg"],
        "generated_at": None,
    }
    with patch.object(ri, "subscription_stub") as sub:
        sub.is_subscribed.return_value = True
        resp = ri._envelope_for_ready(collage, user_id="rishi", is_subscribed=None)
        sub.is_subscribed.assert_called_once_with("rishi")
    assert resp["images"] == ["https://clear/1.jpg"]


def test_response_includes_reference_fields_for_mobile_message():
    """The whole self-healing-on-subscription pattern rests on mobile
    storing (collage_bot_id, collage_date) — NOT the URLs — in the
    chat message. If either field is missing from the response,
    Sarvesh has no way to refetch later."""
    ri = _load("routes.request_images")
    collage = {
        "bot_id": "tara-uuid",
        "generation_date": date(2026, 7, 8),
        "theme": "TAARA on Santorini",
        "image_urls": ["https://clear/1.jpg"],
        "image_urls_blurred": ["https://blurred/1.jpg"],
        "generated_at": None,
    }
    resp = ri._envelope_for_ready(collage, user_id="u", is_subscribed=True)
    assert resp["collage_bot_id"] == "tara-uuid", (
        "collage_bot_id missing — mobile can't reference this collage "
        "for refetch on subscription state change"
    )
    assert resp["collage_date"] == "2026-07-08", (
        "collage_date missing — mobile can't reference this collage "
        "for refetch on subscription state change"
    )


def test_ready_response_carries_blurred_urls_through():
    """The `_ready_response` helper in image_collage must forward
    the `image_urls_blurred` array from the row into the envelope
    so the route's _envelope_for_ready has both arrays to pick from."""
    ic = _load("services.image_collage")
    row = {
        "theme": "TAARA on Santorini",
        "image_urls": ["https://clear/1.jpg"],
        "image_urls_blurred": ["https://blurred/1.jpg"],
        "generated_at": None,
    }
    env = ic._ready_response(row)
    assert env["image_urls"] == ["https://clear/1.jpg"]
    assert env["image_urls_blurred"] == ["https://blurred/1.jpg"], (
        "image_urls_blurred dropped in _ready_response — non-subs won't "
        "see the blurred variants even when the row has them"
    )


def test_blur_variant_failure_returns_none_not_exception():
    """If S3 upload or Replicate download blows up for one variant,
    the pipeline MUST NOT crash the whole batch — the fallback is
    'serve the clear URL for that index' which is worse than pre-blur
    but not worse than pre-2026-07-08 behavior."""
    ic = _load("services.image_collage")

    class _BoomClient:
        async def get(self, *a, **kw):
            raise RuntimeError("network dead")

    async def _run():
        return await ic._download_and_blur_upload(
            _BoomClient(),
            "https://replicate.delivery/whatever.jpg",
            bot_id="tara-uuid",
            generation_date=date(2026, 7, 8),
            index=0,
        )

    result = asyncio.run(_run())
    assert result is None, (
        "blur variant failure raised instead of returning None — the "
        "route path can't tell 'clear-fallback' from 'system crash'"
    )
