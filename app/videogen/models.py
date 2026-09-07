"""Wire models for video generation.

Field names and nullability mirror the mobile Kotlin DTOs exactly
(`GenerateVideoDtos.kt`, `ProviderDto.kt`, `InProgressDraftDtos.kt`) — the app
is already built against the old `storage-interface` shapes and we are moving
the *host*, not the contract. Anything renamed here is a mobile crash.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── generate ───────────────────────────────────────────────────────────


class ImageValue(BaseModel):
    data: str
    # Plain default instead of Optional — see the
    # CreateInfluencerRequest note in app/models.py (anyOf-null
    # schemas get dropped by codegen clients). comfyui.upload_image
    # hard-codes the content type; this field is passthrough metadata
    # today, "" = unspecified.
    mime_type: str = ""


class ImagePayload(BaseModel):
    """Mobile sends `{"type":"Base64","value":{"data":…,"mime_type":…}}`.
    Only Base64 exists today; the tagged shape is the app's, kept as-is."""

    type: Literal["Base64"]
    value: ImageValue


class GenerateRequestBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prompt: str
    model_id: str
    # Wire name is `user_id`, but the app puts the AI influencer's id here —
    # `BotVideoGenCoordinator` sets `userId = botPrincipal`. The video belongs to
    # the BOT, not to the human who owns it. The misleading wire name is why an
    # earlier change dismissed this field as a duplicate of the caller and
    # dropped it; every generated video then landed in the owner's drafts
    # instead of the bot's. Aliased so our code says what it means.
    bot_id: str = Field(validation_alias="user_id", serialization_alias="user_id")
    # Plain defaults instead of `X | None = None` for fields consumed
    # falsy-safely or as unused passthroughs — see the
    # CreateInfluencerRequest note in app/models.py (anyOf-null schemas
    # get dropped by codegen clients):
    #   - duration_seconds: comfyui.build_workflow does
    #     `min(duration_seconds or MAX_DURATION_SECONDS, MAX)` — 0
    #     (unspecified) resolves to the provider max, same as None.
    #   - image: genuinely tri-state (None = text-to-video vs an image
    #     payload = image-to-video) — kept Optional; the one remaining
    #     anyOf-null warning for this field is accepted (mobile always
    #     sends the field explicitly; no codegen consumer depends on
    #     its absence shape).
    #   - generate_audio/seed: UNREAD passthroughs today (the graph
    #     randomizes seeds per request); plain defaults remove the null
    #     schema without changing any behavior.
    image: ImagePayload | None = None
    aspect_ratio: str = ""
    duration_seconds: int = 0
    generate_audio: bool = True
    negative_prompt: str = ""
    resolution: str = ""
    seed: int = 0
    # Mobile always sends "Free" and every provider costs 0. Accepted so the
    # payload validates; deliberately unused — there is no billing here.
    token_type: str = ""


class GenerateRequest(BaseModel):
    request: GenerateRequestBody
    # Plain default — unused passthrough (upload handling is a
    # provider-side concern the graph encodes); no anyOf-null on the
    # published schema.
    upload_handling: str = ""


class GenerateResponse(BaseModel):
    """The app reads exactly these two fields and nothing else."""

    operation_id: str
    provider: str


# ─── drafts ─────────────────────────────────────────────────────────────


class InProgressDraftsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # Same wire name, same meaning as above: whose drafts to list.
    bot_id: str = Field(validation_alias="user_id", serialization_alias="user_id")


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
