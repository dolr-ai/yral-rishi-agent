# Feature Parity Audit — nothing from existing services gets dropped

**Hard constraint (Rishi, 2026-04-23):** every feature/service currently live in Ravi's Rust `yral-ai-chat` AND our Python `yral-chat-ai` must be preserved in v2. No silent regressions. This audit lists every known endpoint + capability, so v2's public-api can be a strict superset.

## Source of truth

- Ravi's Rust: `github.com/dolr-ai/yral-ai-chat` (current prod at `chat-ai.rishi.yral.com`)
- Our Python: `github.com/dolr-ai/yral-chat-ai` (going live 2026-04-25, replaces Rust)
- Local working copies: `~/Claude Projects/yral-ai-chat/`, `~/Claude Projects/yral-chat-ai/`

## Confirmed endpoints in the Python yral-chat-ai (checked 2026-04-23)

### Chat endpoints (`/api/v1/chat/*`)
- `POST /api/v1/chat/conversations` — create/get conversation with AI influencer
- `GET /api/v1/chat/conversations` — list user's conversations (inbox)
- `POST /api/v1/chat/conversations/{id}/messages` — send message + get AI reply
- `GET /api/v1/chat/conversations/{id}/messages` — list conversation messages
- `POST /api/v1/chat/conversations/{id}/read` — mark conversation read
- `DELETE /api/v1/chat/conversations/{id}` — delete conversation
- `POST /api/v1/chat/conversations/{id}/images` — generate image from context

### Influencer endpoints (`/api/v1/influencers/*`) — ALL must be preserved
- `GET /api/v1/influencers` — list all AI influencers (paginated)
- `GET /api/v1/influencers/trending` — list trending influencers
- `GET /api/v1/influencers/{id}` — get one influencer
- `POST /api/v1/influencers/generate-prompt` — STEP 1 of creation: generate personality from short concept
- `POST /api/v1/influencers/validate-and-generate-metadata` — STEP 2: validate + generate profile metadata
- `POST /api/v1/influencers/create` — STEP 3: create the influencer (returns 201)
- `PATCH /api/v1/influencers/{id}/system-prompt` — update personality/system prompt (owner only) — **this is already the creator editing endpoint**
- `POST /api/v1/influencers/{id}/generate-video-prompt` — generate a video prompt for this influencer
- `DELETE /api/v1/influencers/{id}` — delete (owner only)
- `POST /api/v1/admin/influencers/{id}` — admin ban
- `POST /api/v1/admin/influencers/{id}/unban` — admin unban

### WebSocket endpoints
- `WS /api/v1/chat/ws/inbox/{user_id}` — real-time inbox updates

### Health
- `GET /health` — container health probe

## Derived capabilities (behaviors we also need to preserve)

| Behavior | Source | V2 location |
|---|---|---|
| Chat-as-Human creator takeover | existing mobile + yral-chat-ai | orchestrator (2) + influencer-and-profile-directory (13) |
| 50-message paywall per (user, influencer) pair | existing mobile + yral-billing | public-api (1) pre-chat gate |
| Gemini Flash for main chat, OpenRouter for Tara + NSFW | yral-chat-ai AI service layer | llm-client abstraction in orchestrator (2) |
| Memory extraction background task (10-message window) | yral-chat-ai background | user-memory-service (4) — extended to tiered memory |
| Push notifications via metadata service | yral-chat-ai notifications.py | orchestrator (2) or proactive (6) — integrate metadata service |
| S3 media uploads (audio, images) | yral-chat-ai storage.py | media-generation-and-vault (7) |
| Google Chat webhook for admin events (ban/unban) | yral-chat-ai google_chat.py | events-and-analytics (10) or orchestrator (2) |
| Multi-modal messages (text, image, audio) | messages table `message_type` column | conversation schema in v2 preserves this |
| Human-to-human chat scaffolding (conversation_type) | yral-chat-ai schema (participant_b_id, conversation_type) | conversation schema in v2 preserves this |
| Character generator service (personality expansion from one-word concept) | yral-chat-ai character_generator.py | soul-file-library (3) — improves the quality of generated personalities |
| Moderation service | yral-chat-ai moderation.py | content-safety-and-moderation (9) |
| Replicate integration for image generation (FLUX model) | yral-chat-ai replicate.py | media-generation-and-vault (7) |

## Confirmed behaviors in Ravi's Rust service (some inherited from context-for-agents.md)

- OAuth2 JWT validation (issuer allowlist: `auth.yral.com`, `auth.dolr.ai`)
- CallerType resolution via ICP `USER_INFO_SERVICE` canister (BotAccount vs MainAccount) — enables "Chat as Human"
- Token-bucket rate limiter per-user (minute + hour buckets)
- Same ai_influencers + conversations + messages schema pattern

## Gaps we ADD in v2 (beyond feature parity)

These are net-new in v2 — NOT features of existing services, but things Rishi wants added:
- Streaming chat responses (SSE)
- Tiered long-term memory (current is just 10-message window)
- Soul File 4-layer composition
- Proactive messages (cross-session + first-turn nudge)
- Creator Prompt Coach
- Meta-AI advisor
- Programmatic AI influencer creation (open API + MCP) — explicit Rishi ask 2026-04-23
- Private content + monetization rails
- Multi-provider LLM routing with quality-per-archetype

## Gaps / unknowns to check before freezing v2 API

- [ ] Does yral-chat-ai have an `/api/v1/users` or `/api/v1/profiles` endpoint? (check `routes/`)
- [ ] Does yral-chat-ai have analytics event emission endpoints? (check `routes/`)
- [ ] Are there webhook receiver endpoints for yral-billing RTDN events that yral-chat-ai consumes?
- [ ] Full list of HTTP error codes + shapes returned (for mobile parity)
- [ ] Any GraphQL endpoints? (unlikely, but confirm)
- [ ] Any gRPC endpoints? (unlikely, but confirm)
- [ ] Admin-only endpoints not listed above?

## Rule for v2 public-api design

Every endpoint in the lists above MUST exist in `yral-rishi-agent-public-api` with identical path + method + request/response shape. V2 MAY add new endpoints but MUST NOT break existing ones. If a change seems warranted (e.g., renaming an endpoint), it requires Rishi approval + coordination with Sarvesh/Shivam + a dual-path deprecation period.

When the template-and-service build begins, the very first task inside `yral-rishi-agent-public-api` is copying this endpoint list + test coverage that confirms every path returns the expected shape against shadow traffic from the Python service.
