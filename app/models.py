from datetime import date
from typing import Optional, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field, field_validator


class InfluencerResponse(BaseModel):
    id: str
    name: str
    display_name: str
    avatar_url: str
    description: str
    category: str
    is_active: str  # "active" / "coming_soon" / "discontinued" — mobile expects string, not bool
    created_at: str
    conversation_count: Optional[int] = None


class InfluencersListResponse(BaseModel):
    influencers: list[InfluencerResponse]
    total: int
    limit: int
    offset: int


class InfluencerDetailResponse(BaseModel):
    id: str
    name: str
    display_name: str
    avatar_url: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    system_instructions: str
    personality_traits: Optional[dict] = None
    initial_greeting: Optional[str] = None
    suggested_messages: Optional[list[str]] = None
    is_active: str
    is_nsfw: bool = False
    parent_principal_id: Optional[str] = None
    source: Optional[str] = None
    created_at: str
    updated_at: str
    metadata: Optional[dict] = None
    starter_video_prompt: Optional[str] = None


# Influencer creation models

# NOTE on request/response models: every `Optional[X] = None` field
# emits an OpenAPI 3.1 `anyOf: [{...}, {"type": "null"}]` schema under
# pydantic v2 / modern FastAPI. Codegen consumers that can't follow
# $refs through anyOf-null (apple/swift-openapi-generator#817 today)
# then DROP the property entirely — a request model silently loses
# fields for such clients. The wizard models below therefore use plain
# defaulted fields (no Optional): the property becomes
# required-with-default in the doc, consumers may omit it, and null
# schemas never appear.
class CreateInfluencerRequest(BaseModel):
    name: str = Field(min_length=3, max_length=50, pattern=r"^[a-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=255)
    system_instructions: str = Field(min_length=10, max_length=10000)
    bot_principal_id: str = Field(min_length=1, max_length=255)
    # Plain defaults instead of Optional[...] — see the model-level
    # note. Empty string / empty container means "not provided"; the
    # create handler and the DB treat it the same as the old `None`
    # (influencer_repo.create already does `json.dumps(x or {})`).
    avatar_url: str = ""
    description: str = ""
    category: str = ""
    personality_traits: dict = Field(default_factory=dict)
    initial_greeting: str = ""
    suggested_messages: list[str] = Field(default_factory=list)
    is_nsfw: bool = False
    source: str = ""
    metadata: dict = Field(default_factory=dict)

    # Mobile sends TitleCase names — lowercase before pattern validation
    @field_validator("name", mode="before")
    @classmethod
    def lowercase_name(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v


class GeneratePromptRequest(BaseModel):
    # Mobile sends "prompt", backend canonical name is "concept" — accept both
    concept: str = Field(validation_alias=AliasChoices("concept", "prompt"))
    language: str = ""


class GeneratePromptResponse(BaseModel):
    system_instructions: str


class ValidateAndGenerateRequest(BaseModel):
    # Mobile sends "system_instructions", backend canonical name is "concept" — accept both
    concept: str = Field(
        validation_alias=AliasChoices("concept", "system_instructions"),
    )
    language: str = ""


class ValidateAndGenerateResponse(BaseModel):
    # Declares exactly what validate_and_generate_metadata returns: the
    # rejected case carries is_valid=false + reason; the accepted case
    # carries the full persona. Plain defaults instead of Optional —
    # see the model-level note on CreateInfluencerRequest. avatar_url
    # is a plain string because the route coalesces a failed generation
    # (None) to "" — a nullable schema here would get the field dropped
    # by some codegen clients (anyOf-null, see the note above).
    is_valid: bool
    reason: str = ""
    name: str = ""
    display_name: str = ""
    description: str = ""
    avatar_url: str = ""
    initial_greeting: str = ""
    suggested_messages: list[str] = Field(default_factory=list)
    personality_traits: dict = Field(default_factory=dict)
    category: str = ""
    image_prompt: str = ""


class UpdateSystemPromptRequest(BaseModel):
    system_instructions: str


class GenerateVideoPromptRequest(BaseModel):
    topic: str = ""


class GenerateVideoPromptResponse(BaseModel):
    prompt: str


# Conversation models


class ConversationInfluencer(BaseModel):
    id: str
    name: str
    display_name: str
    avatar_url: str
    category: Optional[str] = None
    suggested_messages: Optional[list[str]] = None
    # Phase 2.7 SSE follow-up: mobile uses this to skip the streaming endpoint
    # entirely for NSFW conversations (which fall through to OpenRouter via the
    # legacy non-streaming path). Avoids a try-and-fail round-trip.
    is_nsfw: bool = False


class ConversationLastMessage(BaseModel):
    content: str
    role: str
    created_at: str


class ChatMessage(BaseModel):
    id: str
    conversation_id: Optional[str] = None
    role: str
    content: Optional[str] = None
    message_type: str
    media_urls: Optional[list[str]] = None
    audio_url: Optional[str] = None
    audio_duration_seconds: Optional[int] = None
    token_count: Optional[int] = None
    created_at: str
    # Migration 050 (2026-07-13) — collage reference for the
    # self-healing render path (design §5). Populated only when
    # message_type == "collage"; null on every other row. Serialized
    # as `collage_id: str` on the wire (UUID → str for Sarvesh's DTO).
    collage_id: Optional[UUID] = None
    collage_bot_id: Optional[str] = None
    collage_date: Optional[date] = None


class ConversationResponse(BaseModel):
    id: str
    user_id: str
    influencer: ConversationInfluencer
    created_at: str
    updated_at: str
    message_count: int
    last_message: Optional[ConversationLastMessage] = None
    recent_messages: Optional[list[ChatMessage]] = None


class ConversationsListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int
    limit: int
    offset: int


class CreateConversationRequest(BaseModel):
    influencer_id: str


class DeleteConversationResponse(BaseModel):
    success: bool
    message: str
    deleted_conversation_id: str
    deleted_messages_count: int


# Message models


class SendMessageRequest(BaseModel):
    content: Optional[str] = Field(default=None, max_length=50000)
    # 2026-07-13 — 'collage' widened per migration 047 CHECK; mobile
    # send-message wire allows message_type=collage plus the 3
    # reference fields below so the row round-trips through the
    # self-healing render path (design §5).
    message_type: Literal["text", "multimodal", "image", "audio", "collage"] = "text"
    media_urls: Optional[list[str]] = Field(default=None, max_length=10)
    audio_url: Optional[str] = Field(default=None, max_length=2000)
    audio_duration_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    client_message_id: Optional[str] = Field(default=None, max_length=255)
    # Migration 050 — collage reference. Nullable — non-collage
    # messages leave them unset. Mobile is expected to co-set all 3
    # (or none). Server does NOT reject other combinations today; a
    # CHECK constraint is deliberately deferred per the brief to
    # avoid a lock-time cost on the large messages table.
    collage_id: Optional[UUID] = None
    collage_bot_id: Optional[str] = Field(default=None, max_length=255)
    collage_date: Optional[date] = None


class AssistantError(BaseModel):
    code: Literal["BLOCKED_CONTENT", "TRANSIENT", "NO_PROVIDER"]
    message: str
    retryable: bool


class SendMessageResponse(BaseModel):
    user_message: ChatMessage
    assistant_message: Optional[ChatMessage] = None
    error: Optional[AssistantError] = None


class GenerateImageRequest(BaseModel):
    prompt: Optional[str] = Field(default=None, max_length=2000)


class ConversationMessagesResponse(BaseModel):
    conversation_id: str
    messages: list[ChatMessage]
    total: int
    limit: int
    offset: int


# Media upload models


class UploadResponse(BaseModel):
    url: str
    storage_key: str
    type: Optional[str] = None
    size: Optional[int] = None
    mime_type: Optional[str] = None
    uploaded_at: Optional[str] = None


# Human chat models


class CreateHumanConversationRequest(BaseModel):
    participant_id: str


class HumanConversationPeer(BaseModel):
    id: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
