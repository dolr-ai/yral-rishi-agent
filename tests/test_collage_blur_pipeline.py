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
from unittest.mock import patch


MODULE_ROUTE = Path(__file__).parent.parent / "app" / "routes" / "request_images.py"
MODULE_COLLAGE = Path(__file__).parent.parent / "app" / "services" / "image_collage.py"


def _load(name: str):
    import importlib

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
    """Trust the client flag when explicitly true → return clear URLs.
    The envelope uses whatever the route's `image_urls` list contains
    verbatim; per-entry signing happens upstream in _ready_response."""
    ri = _load("routes.request_images")
    collage = {
        "bot_id": "tara-uuid",
        "generation_date": date(2026, 7, 8),
        "theme": "TAARA on Santorini",
        "image_urls": ["https://signed/clear/1.jpg", "https://signed/clear/2.jpg"],
        "image_urls_blurred": [
            "https://signed/blurred/1.jpg",
            "https://signed/blurred/2.jpg",
        ],
        "generated_at": None,
    }
    resp = ri._envelope_for_ready(collage, user_id="some-user", is_subscribed=True)
    assert resp["images"] == [
        "https://signed/clear/1.jpg",
        "https://signed/clear/2.jpg",
    ]
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
        "image_urls": ["https://signed/clear/1.jpg"],
        "image_urls_blurred": ["https://signed/blurred/1.jpg"],
        "generated_at": None,
    }
    resp = ri._envelope_for_ready(collage, user_id="some-user", is_subscribed=False)
    assert resp["images"] == ["https://signed/blurred/1.jpg"], (
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
        "image_urls": ["https://signed/clear/1.jpg"],
        "image_urls_blurred": [],
        "generated_at": None,
    }
    resp = ri._envelope_for_ready(collage, user_id="some-user", is_subscribed=False)
    assert resp["images"] == ["https://signed/clear/1.jpg"]
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
        "image_urls": ["https://signed/clear/1.jpg"],
        "image_urls_blurred": ["https://signed/blurred/1.jpg"],
        "generated_at": None,
    }
    with patch.object(ri, "subscription_stub") as sub:
        sub.is_subscribed.return_value = True
        resp = ri._envelope_for_ready(collage, user_id="rishi", is_subscribed=None)
        sub.is_subscribed.assert_called_once_with("rishi")
    assert resp["images"] == ["https://signed/clear/1.jpg"]


def test_ready_response_signs_each_stored_key():
    """2026-07-09 fix: DB now stores S3 KEYS in image_urls +
    image_urls_blurred (not signed URLs). _ready_response MUST run
    each stored entry through storage.generate_presigned_url so
    every response ships fresh 15-min-valid URLs regardless of how
    old the row is. The earlier bug: signatures baked in at
    generation time expired 15 min later, so rows served hours
    later returned dead URLs. Locking this test prevents regression."""
    ic = _load("services.image_collage")

    signed_calls: list[str] = []

    def _fake_sign(k: str) -> str:
        signed_calls.append(k)
        return f"https://gateway.storjshare.io/signed/{k}?fresh_sig"

    row = {
        "id": "some-uuid",
        "bot_id": "tara-uuid",
        "generation_date": date(2026, 7, 9),
        "theme": "TAARA Tokyo",
        # These are S3 KEYS, not URLs — the new storage contract
        "image_urls": ["collage-clear/tara/2026-07-09/00.jpg"],
        "image_urls_blurred": ["collage-blurred/tara/2026-07-09/00.jpg"],
        "generated_at": None,
    }
    with patch.object(ic.storage, "generate_presigned_url", side_effect=_fake_sign):
        env = ic._ready_response(row)
    assert signed_calls == [
        "collage-clear/tara/2026-07-09/00.jpg",
        "collage-blurred/tara/2026-07-09/00.jpg",
    ], "_ready_response didn't sign each stored key — dead URLs will ship"
    assert env["image_urls"] == [
        "https://gateway.storjshare.io/signed/collage-clear/tara/2026-07-09/00.jpg?fresh_sig"
    ]
    assert env["image_urls_blurred"] == [
        "https://gateway.storjshare.io/signed/collage-blurred/tara/2026-07-09/00.jpg?fresh_sig"
    ]


def test_mirror_batch_stores_keys_not_urls():
    """Source-pin the mirror pipeline: `_mirror_batch` returns S3
    KEYS (deterministic relative paths like
    `collage-clear/{bot}/{date}/{i}.jpg`) — never the Replicate
    delivery URL and never a presigned URL. Storing keys is the
    invariant that makes fresh-signing on read possible."""
    body = (
        Path(__file__).parent.parent / "app" / "services" / "image_collage.py"
    ).read_text()
    # The clear-side key layout
    assert (
        'f"collage-clear/{bot_id}/{generation_date.isoformat()}/{index:02d}.jpg"'
        in body
    ), (
        "clear S3 key template changed — mirror pipeline may store a "
        "URL instead of a key, re-introducing the 2026-07-09 URL-expiry bug"
    )
    assert (
        'f"collage-blurred/{bot_id}/{generation_date.isoformat()}/{index:02d}.jpg"'
        in body
    )
    # `complete()` MUST be called with clear_keys, not the raw Replicate urls
    assert "clear_keys," in body and "urls=urls" not in body, (
        "complete() being called with raw Replicate urls instead of the "
        "S3 keys we just uploaded — reverts the mirror fix"
    )


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
    so the route's _envelope_for_ready has both arrays to pick from.
    Signing behavior is tested separately in
    test_ready_response_signs_each_stored_key."""
    ic = _load("services.image_collage")
    row = {
        "theme": "TAARA on Santorini",
        "image_urls": ["collage-clear/tara/2026-07-08/00.jpg"],
        "image_urls_blurred": ["collage-blurred/tara/2026-07-08/00.jpg"],
        "generated_at": None,
    }
    with patch.object(
        ic.storage, "generate_presigned_url", side_effect=lambda k: f"signed:{k}"
    ):
        env = ic._ready_response(row)
    assert env["image_urls"] == ["signed:collage-clear/tara/2026-07-08/00.jpg"]
    assert env["image_urls_blurred"] == [
        "signed:collage-blurred/tara/2026-07-08/00.jpg"
    ], (
        "image_urls_blurred dropped in _ready_response — non-subs won't "
        "see the blurred variants even when the row has them"
    )


def test_ready_response_forwards_bot_id_and_generation_date():
    """Regression: the initial 2026-07-08 ship of this envelope
    dropped `generation_date`, leaving `collage_date: null` in the
    response — Sarvesh would have hit that on first integration
    (mobile can't store a null date as the chat-message reference).

    Both `bot_id` and `generation_date` MUST make it from the DB row
    into the envelope so the route's `_envelope_for_ready` can echo
    them back."""
    ic = _load("services.image_collage")
    row = {
        "id": "some-uuid",
        "bot_id": "tara-uuid",
        "generation_date": date(2026, 7, 8),
        "theme": "TAARA on Santorini",
        "image_urls": ["https://clear/1.jpg"],
        "image_urls_blurred": ["https://blurred/1.jpg"],
        "generated_at": None,
    }
    env = ic._ready_response(row)
    assert env["bot_id"] == "tara-uuid", (
        "bot_id missing from _ready_response envelope — mobile has no "
        "reference to store in the chat message"
    )
    assert env["generation_date"] == "2026-07-08", (
        "generation_date missing or not ISO-formatted — mobile has no "
        "date to store in the chat message, refetch on subscription "
        "change is broken"
    )


def test_ready_response_forwards_collage_id_uuid():
    """Migration 048 + 2026-07-09 refactor: mobile stores `collage_id`
    (the opaque UUID) in the chat message so it can refetch via
    ?collage_id=<uuid>. The envelope MUST forward the UUID from the DB
    row; dropping it silently breaks the primary lookup path."""
    ic = _load("services.image_collage")
    row = {
        "id": "abc-123-uuid",
        "bot_id": "tara-uuid",
        "generation_date": date(2026, 7, 9),
        "theme": "TAARA Maldives",
        "image_urls": ["https://clear/1.jpg"],
        "image_urls_blurred": ["https://blurred/1.jpg"],
        "generated_at": None,
    }
    env = ic._ready_response(row)
    assert env["id"] == "abc-123-uuid", (
        "collage id missing from _ready_response — mobile's preferred "
        "chat-message reference (opaque UUID) breaks silently"
    )


def test_envelope_includes_collage_id_for_mobile_message_reference():
    """The route-side envelope MUST include `collage_id` alongside
    `collage_bot_id` + `collage_date`. Mobile prefers the UUID for
    refetch (single opaque handle) but keeps the composite fields
    for legacy compatibility + human debugging."""
    ri = _load("routes.request_images")
    collage = {
        "id": "abc-123-uuid",
        "bot_id": "tara-uuid",
        "generation_date": date(2026, 7, 9),
        "theme": "TAARA Maldives",
        "image_urls": ["https://clear/1.jpg"],
        "image_urls_blurred": ["https://blurred/1.jpg"],
        "generated_at": None,
    }
    resp = ri._envelope_for_ready(collage, user_id="u", is_subscribed=True)
    assert resp["collage_id"] == "abc-123-uuid", (
        "collage_id missing from response envelope — Sarvesh can't "
        "store the preferred UUID reference"
    )
    # legacy fields still present
    assert resp["collage_bot_id"] == "tara-uuid"
    assert resp["collage_date"] == "2026-07-09"


def test_route_supports_collage_id_query_param():
    """Source-pin the route signature so a future refactor can't
    silently drop the query params that Sarvesh's mobile depends on."""
    src = (
        Path(__file__).parent.parent / "app" / "routes" / "request_images.py"
    ).read_text()
    assert "collage_id: str | None = None" in src, (
        "route dropped the collage_id query param — mobile's primary "
        "lookup path is broken"
    )
    assert "date: str | None = None" in src, (
        "route dropped the date query param — mobile's fallback path "
        "for legacy chat messages is broken"
    )
    # And the handler MUST actually branch on collage_id first
    assert "if collage_id:" in src, (
        "route no longer prefers collage_id over date — precedence bug"
    )
    # And the bot-id-mismatch guard MUST stay in place (security-adjacent)
    assert 'row.get("bot_id") != influencer_id' in src, (
        "the guard clearing the row when the UUID belongs to a different "
        "bot was removed — one bot's UUID could peek at another bot's collage"
    )


def test_mirror_variant_failure_returns_none_pair_not_exception():
    """If S3 upload or Replicate download blows up for one variant,
    the pipeline MUST NOT crash the whole batch — `_mirror_batch`
    silently drops the failed index from the returned arrays. The
    caller `_run_generation` decides whether the resulting shortfall
    is fatal (clear-side < N) or just a warning (blurred-side <
    clear-side)."""
    ic = _load("services.image_collage")

    class _BoomClient:
        async def get(self, *a, **kw):
            raise RuntimeError("network dead")

    async def _run():
        return await ic._mirror_and_blur_variant(
            _BoomClient(),
            "https://replicate.delivery/whatever.jpg",
            bot_id="tara-uuid",
            generation_date=date(2026, 7, 8),
            index=0,
        )

    result = asyncio.run(_run())
    assert result == (None, None), (
        "mirror variant failure raised or returned a non-None pair — "
        "the batch aggregator can't tell 'partial failure' from 'crash'"
    )
