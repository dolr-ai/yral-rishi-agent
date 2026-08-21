"""Wire models for video generation.

Field names and nullability mirror the mobile Kotlin DTOs exactly
(`GenerateVideoDtos.kt`, `ProviderDto.kt`, `InProgressDraftDtos.kt`) — the app
is already built against the old `storage-interface` shapes and we are moving
the *host*, not the contract. Anything renamed here is a mobile crash.
"""

from typing import Any, Literal

from pydantic import BaseModel

# ─── generate ───────────────────────────────────────────────────────────


class ImageValue(BaseModel):
    data: str
    mime_type: str | None = None


class ImagePayload(BaseModel):
    """Mobile sends `{"type":"Base64","value":{"data":…,"mime_type":…}}`.
    Only Base64 exists today; the tagged shape is the app's, kept as-is."""

    type: Literal["Base64"]
    value: ImageValue


class GenerateRequestBody(BaseModel):
    prompt: str
    model_id: str
    user_id: str
    image: ImagePayload | None = None
    aspect_ratio: str | None = None
    duration_seconds: int | None = None
    generate_audio: bool | None = None
    negative_prompt: str | None = None
    resolution: str | None = None
    seed: int | None = None
    # Mobile always sends "Free" and every provider costs 0. Accepted so the
    # payload validates; deliberately unused — there is no billing here.
    token_type: str | None = None


class GenerateRequest(BaseModel):
    request: GenerateRequestBody
    upload_handling: str | None = None


class GenerateResponse(BaseModel):
    """The app reads exactly these two fields and nothing else."""

    operation_id: str
    provider: str


# ─── drafts ─────────────────────────────────────────────────────────────


class InProgressDraftsRequest(BaseModel):
    user_id: str


class InProgressDraftItem(BaseModel):
    operation_id: str
    status: str
    created_at: str
    model_id: str
    prompt: str
    provider: str | None = None
    thumbnail_url: str | None = None


class InProgressDraftsResponse(BaseModel):
    items: list[InProgressDraftItem]


# ─── publish ────────────────────────────────────────────────────────────


class MarkPostAsPublishedRequest(BaseModel):
    post_id: str


# ─── providers ──────────────────────────────────────────────────────────

# One provider, fixed capabilities, zero cost. This was 118 lines of Rust
# returning a constant; it is a constant. `providers` filters out internal
# entries, `providers-all` does not — that is the only difference.
LTX2_PROVIDER: dict[str, Any] = {
    "id": "ltx2",
    "name": "Ltx2",
    "description": "LTX video generation",
    "cost": {"usd_cents": 0, "dolr": 0, "sats": 0},
    "supports_image": True,
    "supports_negative_prompt": False,
    "supports_audio": True,
    "supports_seed": True,
    "allowed_aspect_ratios": ["16:9", "9:16", "1:1"],
    "allowed_resolutions": [],
    "allowed_durations": [5],
    "default_aspect_ratio": "16:9",
    "default_resolution": None,
    "default_duration": 5,
    "is_available": True,
    "is_internal": False,
    "model_icon": None,
    "extra_info": {},
}

ALL_PROVIDERS: list[dict[str, Any]] = [LTX2_PROVIDER]


def public_providers() -> list[dict[str, Any]]:
    return [p for p in ALL_PROVIDERS if not p["is_internal"]]
