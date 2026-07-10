from typing import Optional, Literal
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


class CreateInfluencerRequest(BaseModel):
    name: str = Field(min_length=3, max_length=50, pattern=r"^[a-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=255)
    system_instructions: str = Field(min_length=10, max_length=10000)
    bot_principal_id: str = Field(min_length=1, max_length=255)
    avatar_url: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    personality_traits: Optional[dict] = None
    initial_greeting: Optional[str] = None
    suggested_messages: Optional[list[str]] = None
    is_nsfw: bool = False
    source: Optional[str] = None
    metadata: Optional[dict] = None

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
    language: Optional[str] = None


class GeneratePromptResponse(BaseModel):
    system_instructions: str


class ValidateAndGenerateRequest(BaseModel):
    # Mobile sends "system_instructions", backend canonical name is "concept" — accept both
    concept: str = Field(
        validation_alias=AliasChoices("concept", "system_instructions"),
    )
    language: Optional[str] = None


class ValidateAndGenerateResponse(BaseModel):
    is_valid: bool
    name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    system_instructions: Optional[str] = None
    avatar_url: Optional[str] = None
    initial_greeting: Optional[str] = None
    suggested_messages: Optional[list[str]] = None
    personality_traits: Optional[dict] = None
    rejection_reason: Optional[str] = None


class UpdateSystemPromptRequest(BaseModel):
    system_instructions: str


class GenerateVideoPromptRequest(BaseModel):
    topic: Optional[str] = None


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


class LinkCta(BaseModel):
    """Spicy chat gate — CTA link the mobile client renders as a
    tappable card on top of the assistant message. Empty by default,
    populated only when native deflection swaps in the "chat with me
    privately" reply. Sarvesh contract per design §5.4."""

    ctaUrl: str
    ctaLabel: str


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
    link_cta: Optional[LinkCta] = None


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
    message_type: Literal["text", "multimodal", "image", "audio"] = "text"
    media_urls: Optional[list[str]] = Field(default=None, max_length=10)
    audio_url: Optional[str] = Field(default=None, max_length=2000)
    audio_duration_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    client_message_id: Optional[str] = Field(default=None, max_length=255)
    # Spicy chat gate: which surface is originating the request.
    #   "app"        — native mobile app (default; preserves existing
    #                  mobile behavior when unset)
    #   "web_spicy"  — amorae-web server-to-server (requires the shared
    #                  X-Amorae-Secret header; native clients cannot
    #                  set this — server enforces 403)
    surface: Literal["app", "web_spicy"] = "app"


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
