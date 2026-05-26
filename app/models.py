from typing import Optional
from pydantic import BaseModel


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
