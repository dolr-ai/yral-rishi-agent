# YRAL Chat AI Service — Complete Feature Parity Inventory

**Service URL:** https://chat-ai.rishi.yral.com
**Live Since:** 2026-04-23
**Based On:** Rust service `dolr-ai/yral-ai-chat`
**Source Location:** `/Users/rishichadha/Claude Projects/yral-chat-ai/`
**Audit Date:** 2026-04-23

---

## 1. HTTP Endpoints (Complete Inventory)

### Health & Status (No Auth Required)

| Endpoint | Method | Auth | Request | Response | Status Codes | Side Effects | Source |
|----------|--------|------|---------|----------|--------------|--------------|--------|
| `/` | GET | None | Empty | `{service, version, status}` | 200 | None | `/app/routes/health.py:16-23` |
| `/health` | GET | None | Empty | `{status, database}` or error | 200, 503 | DB query | `/app/routes/health.py:26-34` |
| `/status` | GET | None | Empty | `{service, version, environment, database, gemini_model}` | 200, 503 | DB query | `/app/routes/health.py:37-47` |

### Auth Test

| Endpoint | Method | Auth | Request | Response | Status Codes | Side Effects | Source |
|----------|--------|------|---------|----------|--------------|--------------|--------|
| `/api/v1/auth/me` | GET | JWT | Empty | `{user_id}` | 200, 401 | None | `/app/main.py:179-183` |

### AI Chat — Version 1 (Inbox + Messages)

All require **JWT Bearer token**.

| Endpoint | Method | Request | Response | Status Codes | Side Effects | Source |
|----------|--------|---------|----------|--------------|--------------|--------|
| `/api/v1/chat/conversations` | POST | `{influencer_id}` | `ConversationDto` with recent messages | 201, 404, 409 | Creates conversation, saves initial greeting if exists, broadcasts WS | `chat_v1.py:190-248` |
| `/api/v1/chat/conversations` | GET | `?limit=20&offset=0&influencer_id=optional` | `{conversations[], total, limit, offset}` with last + recent messages + unread count | 200 | Batch queries | `chat_v1.py:255-331` |
| `/api/v1/chat/conversations/{id}/messages` | GET | `?limit=50&offset=0&order=desc/asc` | `{conversation_id, messages[], total, limit, offset}` with presigned S3 URLs | 200, 403, 404 | DB query | `chat_v1.py:338-374` |
| `/api/v1/chat/conversations/{id}/messages` | POST | `{content, message_type, media_urls[], audio_url, audio_duration_seconds, client_message_id}` | `{user_message, assistant_message}` | 200, 503, 400, 403, 404 | Audio transcribe → save user msg → call Gemini/OpenRouter → save AI msg → background memory extraction → push notification → WS broadcast (new_message, typing_status start/stop) | `chat_v1.py:381-570` |
| `/api/v1/chat/conversations/{id}/read` | POST | Empty | `{unread_count}` | 200, 403, 404 | Marks read in DB, broadcasts read receipt via WS | `chat_v1.py:765-794` |
| `/api/v1/chat/conversations/{id}` | DELETE | Empty | `{success, deleted_conversation_id, deleted_messages_count}` | 200, 403, 404 | Cascade-deletes messages + conversation | `chat_v1.py:801-826` |
| `/api/v1/chat/conversations/{id}/images` | POST | `{prompt: optional, aspect_ratio: "9:16"}` | Message object type='image', media_urls=[s3_key] | 201, 400, 403, 404, 503 | Replicate (FLUX) → download → re-upload S3 → save assistant message | `chat_v1.py:642-758` |

### AI Chat — Version 2 (Bot-Aware Inbox)

| Endpoint | Method | Request | Response | Source |
|----------|--------|---------|----------|--------|
| `/api/v2/chat/conversations` | GET | `?principal=USER_OR_BOT_ID&limit=20&offset=0&influencer_id=optional` | Format depends on caller type (user vs bot) | `chat_v2.py:123-159` |

**Caller Type:** Looks up principal in `ai_influencers` table. If found → bot perspective (returns users as peers, batch-fetches user profiles via `METADATA_URL/metadata-bulk`). Else → user perspective (returns influencers).

### AI Chat — Version 3 (Unified Inbox)

| Endpoint | Method | Request | Response | Source |
|----------|--------|---------|----------|--------|
| `/api/v3/chat/conversations` | GET | `?limit=20&offset=0` | UNION of AI + human chats sorted by `updated_at DESC` | `chat_v3.py:46-216` |

### Human Chat (NEW — not in Ravi's Rust service)

| Endpoint | Method | Request | Response | Side Effects | Source |
|----------|--------|---------|----------|--------------|--------|
| `/api/v1/chat/human/conversations` | POST | `{participant_id}` | Conversation object | Creates human_chat conversation (or returns existing) | `human_chat.py:97-166` |
| `/api/v1/chat/human/conversations` | GET | `?limit=20&offset=0` | `{conversations[], total, limit, offset}` filtered where user is `user_id` OR `participant_b_id` | DB query | `human_chat.py:173-245` |
| `/api/v1/chat/human/conversations/{id}/messages` | POST | Same shape as AI message | `{user_message, assistant_message: null}` | Saves message, WS broadcast to RECIPIENT, push notification to recipient | `human_chat.py:252-368` |

**Key:** No AI call. `assistant_message` always null. Recipient gets notified, not sender.

### Influencers

| Endpoint | Method | Auth | Request | Response | Source |
|----------|--------|------|---------|----------|--------|
| `/api/v1/influencers` | GET | None | `?limit=50&offset=0` | List active. **Cached 300s** | `influencers.py:136-164` |
| `/api/v1/influencers/trending` | GET | None | `?limit=50&offset=0` | Sorted by `message_count DESC`. **Cached 300s** | `influencers.py:167-195` |
| `/api/v1/influencers/{id}` | GET | None | Empty | Full influencer with `system_instructions`, `metadata`. **Cached 300s** | `influencers.py:202-212` |
| `/api/v1/influencers/generate-prompt` | POST | JWT | `{concept, language?}` | `{system_instructions}` | Calls Gemini via character_generator | `influencers.py:219-233` |
| `/api/v1/influencers/validate-and-generate-metadata` | POST | JWT | `{concept, language?}` | `{is_valid, name, display_name, description, system_instructions, initial_greeting, suggested_messages, personality_traits, rejection_reason}` | Calls Gemini for full metadata | `influencers.py:236-252` |
| `/api/v1/influencers/create` | POST | JWT | Full influencer body (incl. `bot_principal_id`, `parent_principal_id`, etc.) | Created influencer | Checks name uniqueness → appends safety guardrails → generates greeting/suggestions if missing → DB insert | `influencers.py:255-314` |
| `/api/v1/influencers/{id}/system-prompt` | PATCH | JWT | `{system_instructions}` | Updated influencer | Ownership check (`parent_principal_id == user_id`), append guardrails, DB update | `influencers.py:321-345` |
| `/api/v1/influencers/{id}/generate-video-prompt` | POST | JWT | `{topic?}` | `{prompt}` | Cinematic prompt for LTX video model | `influencers.py:348-366` |
| `/api/v1/influencers/{id}` | DELETE | JWT | Empty | Updated influencer (soft-deleted) | Sets `is_active='discontinued'`, renames to 'Deleted Bot' | `influencers.py:373-392` |
| `/api/v1/admin/influencers/{id}` | POST | X-Admin-Key | Empty | Updated influencer | Constant-time key compare → discontinue → Google Chat ping | `influencers.py:399-433` |
| `/api/v1/admin/influencers/{id}/unban` | POST | X-Admin-Key | Empty | Updated influencer | Reverse: set active → Google Chat ping | `influencers.py:436-470` |

**System-prompt display:** Guardrails are appended on save BUT STRIPPED on display so user only sees their own text.

### Media Upload

| Endpoint | Method | Auth | Request | Response | Source |
|----------|--------|------|---------|----------|--------|
| `/api/v1/media/upload` | POST | JWT | `multipart/form-data: file, type=image\|audio` | `{url, storage_key, type, size, mime_type, uploaded_at}` | `media.py:39-133` |

**Validation:** images max 10 MB (jpg/jpeg/png/gif/webp), audio max 20 MB (mp3/m4a/wav/ogg). Presigned URL expires in 15 min (configurable).

### WebSocket

| Endpoint | Method | Auth | Source |
|----------|--------|------|--------|
| `/api/v1/chat/ws/inbox/{user_id}` | WebSocket | JWT in `?token=` query | `websocket.py:37-130` |
| `/api/v1/chat/ws/docs` | GET | None | Returns JSON schema of event types | `websocket.py:133-172` |

**WebSocket events** (server → client):
- `new_message` — `{conversation_id, message, influencer (id/display_name/avatar_url/is_online), unread_count}`
- `conversation_read` — `{conversation_id, unread_count, read_at}`
- `typing_status` — `{conversation_id, influencer_id, is_typing}` — sent at start + end of `send_message`

🚨 **CRITICAL: WebSocket is in-memory only (no cross-node delivery).** Connections live in `_connections: dict[str, list[WebSocket]]` per app process. With 2 app instances (rishi-1, rishi-2), if user's WS is on rishi-1 but message processed on rishi-2, the broadcast doesn't reach the user. V2 must fix this (Phase 5+ idea per existing service: PostgreSQL LISTEN/NOTIFY).

---

## 2. External Integrations

| Service | Config | Where invoked | Source |
|---------|--------|---------------|--------|
| **Gemini Flash** | `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-2.5-flash`), `GEMINI_MAX_TOKENS` (2048), `GEMINI_TEMPERATURE` (0.7), `GEMINI_TIMEOUT` (60s) | Chat responses, memory extraction, audio transcription, character generation, video prompts | `services/ai_client.py` (native API with `?key=` query param, NOT OpenAI SDK — works with new "AQ." key format) |
| **OpenRouter** | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` (default `google/gemini-2.5-flash`) | NSFW influencer fallback ONLY (gated by `is_nsfw` field on influencer) | `services/ai_client.py` (OpenAI-compatible AsyncOpenAI SDK) |
| **Replicate** | `REPLICATE_API_TOKEN`, `REPLICATE_MODEL` (default `black-forest-labs/flux-dev`) | Image generation (`/conversations/{id}/images`) + character avatars | `services/replicate.py` (uses `flux-dev` for standard, `flux-kontext-dev` for reference-based) |
| **Metadata service** | `METADATA_URL` (default `https://metadata.yral.com`), `YRAL_METADATA_NOTIFICATION_API_KEY` | (a) Push notifications POST to `{METADATA_URL}/notifications/{user_id}/send`. (b) `/metadata-bulk` for batch user profiles in v2 inbox | `services/push_notifications.py`, `routes/chat_v2.py:_fetch_user_profiles` |
| **Hetzner S3** | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET`, `AWS_REGION`, `S3_ENDPOINT_URL`, `S3_PUBLIC_URL_BASE`, `S3_URL_EXPIRES_SECONDS` (900) | Media uploads + presigned URLs (presigned everywhere media returned) | `services/storage.py` |
| **Google Chat webhook** | `GOOGLE_CHAT_WEBHOOK_URL` | Admin ban/unban notifications only | `services/google_chat.py` |
| **PostgreSQL** | `DATABASE_URL` | Every endpoint, asyncpg pool (min 2, max 10) | `database.py` |

---

## 3. Background Tasks (asyncio.create_task)

| Task | Trigger | What it does | Source |
|------|---------|--------------|--------|
| Memory extraction | After `/conversations/{id}/messages` AI response saved | Calls Gemini with user msg + AI response → extracts facts → merges into `conversations.metadata.memories` JSONB | `chat_v1.py:573-594` |
| Push notification | After AI response OR human-to-human msg | POSTs to `{METADATA_URL}/notifications/{user_id}/send` | `services/push_notifications.py:26-82` |
| WebSocket broadcast | After AI response, read receipt, or human msg | Sends event to all connected sockets for that user | `services/websocket_manager.py:77-101+` |

---

## 4. Database Schema (3 tables, fully migrated)

### `ai_influencers`
- `id` (PK, varchar 255 — bot's IC principal ID)
- `name` (UNIQUE, 3-50 chars alphanumeric+underscore)
- `display_name` (NOT NULL)
- `avatar_url`, `description`, `category` (nullable)
- `system_instructions` (TEXT, NOT NULL — WITH guardrails appended on save, stripped on display)
- `personality_traits` (JSONB)
- `initial_greeting` (sent as first message when conv created)
- `suggested_messages` (JSONB array — starter prompts shown in first message)
- `is_active` (active / coming_soon / discontinued — soft-delete via 'discontinued')
- `is_nsfw` (BOOLEAN — gates OpenRouter routing)
- `parent_principal_id` (creator's principal — owner check)
- `source` (user_created / admin_curated / etc.)
- `metadata` (JSONB)
- `created_at`, `updated_at` (auto-updated by trigger)

### `conversations`
- `id` (PK, UUID)
- `user_id` (creator's principal)
- `influencer_id` (FK → ai_influencers, NULL for human_chat)
- `conversation_type` ('ai_chat' | 'human_chat')
- `participant_b_id` (other person's principal for human_chat — NULL for ai_chat)
- `metadata` (JSONB stores `{memories: {key: value}}` for user facts)
- `created_at`, `updated_at` (auto-updated by trigger when message inserted — keeps inbox sorted)

**Unique constraints:**
- One AI chat per (user_id, influencer_id)
- One human chat per (user_id, participant_b_id) — bidirectional logic via OR query

### `messages`
- `id` (PK, UUID)
- `conversation_id` (FK CASCADE delete)
- `role` ('user' | 'assistant')
- `sender_id` (principal of sender — required for human chat to distinguish two humans)
- `content`, `message_type` ('text' | 'multimodal' | 'image' | 'audio')
- `media_urls` (JSONB array of S3 keys)
- `audio_url`, `audio_duration_seconds`
- `token_count` (Gemini tokens for AI responses)
- `client_message_id` (deduplication — unique per conversation)
- `is_read`, `status`
- `metadata` (JSONB)
- `created_at`

---

## 5. Auth Flow

JWT validated at `auth.py:get_current_user(request)`:
1. Read `Authorization: Bearer <token>`
2. **Decode WITHOUT signature validation** — matches Rust service intentionally
3. Validate `iss` ∈ `["https://auth.yral.com", "https://auth.dolr.ai"]`
4. Validate `sub` not empty + `exp` not expired
5. Return `sub` as `user_id`

🚨 **The "JWT signature gap" we attributed to Ravi is ALSO present in the Python service.** Both services intentionally skip RS256 signature validation. We treated this as a "fix in v2" — confirm with Rishi if v2 should fix it (proper JWKS validation) or maintain the current behavior.

WebSocket: JWT passed as `?token=` query param (headers don't work for WS).

---

## 6. Paywall — NOT IN THE CODEBASE 🚨

**Major finding:** the codebase has NO paywall enforcement.

- No 50-message check before sending
- No response shape for "blocked" messages
- No yral-billing integration in the chat service
- `RATE_LIMIT_PER_MINUTE` and `RATE_LIMIT_PER_HOUR` env vars exist in `config.py` but aren't used

**Implication:** paywall enforcement happens at the **mobile client** before sending. Mobile calls yral-billing's `/google/chat-access/check` first; only sends a chat message if the user has access. This explains the "pre-chat access check" pattern we documented.

Worth confirming: the per-(user, influencer) 50-msg counter must live somewhere. Either mobile tracks it, or yral-billing's `bot_chat_access` table tracks it via the `expires_at` column. Likely yral-billing.

---

## 7. AI Influencer Creation Flow (3 steps)

### Step 1: `POST /api/v1/influencers/generate-prompt`
- Request: `{concept, language?}`
- Calls Gemini with `GENERATE_PROMPT` template → expands concept into 500 words of system instructions
- Response: `{system_instructions}`

### Step 2: `POST /api/v1/influencers/validate-and-generate-metadata`
- Request: `{concept, language?}`
- Calls Gemini with `VALIDATE_PROMPT` template → safety check + generates: `name` (URL slug 3-12 chars), `display_name`, `description`, `initial_greeting`, `suggested_messages` (3-4), `personality_traits`, `category`, `image_prompt` for avatar
- Response: full metadata + `{is_valid, rejection_reason}`

### Step 3: `POST /api/v1/influencers/create`
- Request: full influencer body including `bot_principal_id` (from IC canister), `parent_principal_id` (creator)
- Validates name uniqueness (409 if taken)
- Appends safety guardrails to `system_instructions`
- Generates greeting/suggestions if missing
- DB insert
- Response: full influencer object

### System-prompt edit: `PATCH /api/v1/influencers/{id}/system-prompt`
- Owner check via `parent_principal_id == user_id`
- Appends guardrails on save
- Strips guardrails on display (so user sees their text only)

---

## 8. CRITICAL FINDINGS — what v2 might miss without care

| # | Finding | Risk | Where v2 must handle |
|---|---|---|---|
| 1 | **`client_message_id` deduplication** — every message endpoint queries existing by client_message_id; if found, returns cached response | Without this, network retries duplicate messages | Public-api + orchestrator |
| 2 | **Unified `conversations` table for AI + human** — one schema, `conversation_type` distinguishes | Splitting tables breaks v3 inbox UNION query + WebSocket | Conversation schema + influencer-and-profile-directory |
| 3 | **`metadata.memories` for user facts** — JSONB dict updated by background task; appended to system instructions on next AI call | Without this, context lost between conversations | user-memory-service (extends to tiered memory in v2) |
| 4 | **Soft-delete via `is_active='discontinued'`** — preserves conversation history but hides from lists | Hard-delete leaves orphaned `conversations.influencer_id` | influencer-and-profile-directory |
| 5 | **Guardrails appended on save / stripped on display** — for `system_instructions` | If v2 displays raw saved text, users see safety rules in edit screen (confusing) | soul-file-library (this IS the layered system handling guardrails as Layer 0/1) |
| 6 | **Presigned S3 URLs in every media response** — 15-min expiry | Raw S3 keys won't display in mobile app | media-generation-and-vault + every message-returning endpoint |
| 7 | **WebSocket in-memory only** — no cross-node delivery | Multi-replica deploys silently lose events | public-api + Redis Streams pub/sub for cross-node WS |
| 8 | **Image history window optimization** — only last 3 messages get inlined images | Inlining all images = 200-500ms S3 download per image + token blowout | conversation-turn-orchestrator |
| 9 | **Bidirectional unique constraint for human chat** — `(user_id, participant_b_id) UNIQUE WHERE conversation_type='human_chat'`; queries use OR logic | Directional logic = duplicate conv records per pair | conversation schema + queries |
| 10 | **Auto-timestamping triggers** — `trigger_update_conversation_timestamp` (on message insert) + `trigger_update_influencer_timestamp` (on update) | App-code timestamping breaks inbox sorting | Postgres migrations |
| 11 | **OpenRouter routing via `is_nsfw` flag on influencer** — NOT per-influencer-id routing | If v2 routes via per-id table only, NSFW bots get neutered Gemini responses | llm-client abstraction needs to honor `is_nsfw` field too |
| 12 | **Multi-modal: text / multimodal / image / audio with transcription** — audio transcribed by Gemini before saving, prepended as `[Transcribed: ...]` | Without transcription, audio bots can't "hear" | conversation-turn-orchestrator |
| 13 | **Read receipts via WebSocket** — `conversation_read` event with `read_at` timestamp | UX regression if missing | public-api WS broadcasts |
| 14 | **Typing indicators via WebSocket** — `is_typing=true` at start of AI call, `is_typing=false` at end | UX regression if missing | conversation-turn-orchestrator + WS broadcasts |
| 15 | **Cache-Control 300s on influencer GET endpoints** — Cloudflare/mobile honor this | If missing, every list call hits server | public-api response headers |
| 16 | **CORS via env-var configurable origins** — `CORS_ORIGINS` | Mobile/web from different origins blocked | public-api middleware |
| 17 | **JWT signature validation skipped intentionally in BOTH services** | If v2 enables it without auth-team coordination, all logged-in users get 401 | Coordinate with Ravi/auth-team before enabling JWKS validation |
| 18 | **3-step influencer creation flow** uses character_generator (Gemini-driven)** — generate-prompt + validate-and-generate-metadata + create | Lose any one step → bot quality regresses (Tara's lineage uses this flow) | influencer-and-profile-directory + soul-file-library |
| 19 | **`PATCH /system-prompt` is already the creator-edit endpoint** — owner-check on `parent_principal_id == user_id` | This IS what we'd be wrapping with the Soul File Coach (creator-studio service) | creator-studio writes through this endpoint or directly to soul-file-library |
| 20 | **Admin ban via X-Admin-Key constant-time compare** | Without admin tooling, no way to ban abusive bots in v2 | content-safety-and-moderation owns admin endpoints |

---

## 9. Other Service-Level Constants

- **Auth issuers (only):** `https://auth.yral.com`, `https://auth.dolr.ai`
- **Image upload max:** 10 MB (configurable `MAX_IMAGE_SIZE_MB`)
- **Audio upload max:** 20 MB (configurable `MAX_AUDIO_SIZE_MB`), max 300s duration
- **S3 presigned URL TTL:** 15 minutes (`S3_URL_EXPIRES_SECONDS=900`)
- **Image history window:** 3 messages (`IMAGE_HISTORY_WINDOW=3`)
- **DB connection pool:** min 2, max 10
- **Influencer GET cache:** 300 seconds public Cache-Control
- **Gemini timeout:** 60s
- **OpenRouter timeout:** 30s
- **Replicate polling:** 2s interval, 60s max wait

---

## Final Feature-Parity Checklist for v2 (must-preserve)

- [ ] All 21 HTTP endpoints + 1 WebSocket endpoint listed above
- [ ] `client_message_id` deduplication on every write endpoint
- [ ] Unified conversations table (`conversation_type` field)
- [ ] H2H chat full flow (create/list/send) with bidirectional unique constraint
- [ ] Memory extraction background task → `conversations.metadata.memories`
- [ ] Soft-delete `is_active='discontinued'` for influencers
- [ ] Guardrails append-on-save / strip-on-display for system_instructions
- [ ] Presigned S3 URLs in every media response
- [ ] WebSocket events: new_message, conversation_read, typing_status
- [ ] Image history window optimization (3 messages)
- [ ] Auto-timestamping triggers for conversations + influencers
- [ ] OpenRouter routing via `is_nsfw` flag (in addition to per-influencer Tara override)
- [ ] Multi-modal messages with audio transcription via Gemini
- [ ] Read receipts via WebSocket
- [ ] Typing indicators (start + end of AI call)
- [ ] Cache-Control 300s on influencer GETs
- [ ] CORS configurable origins
- [ ] 3-step influencer creation (generate-prompt → validate-and-generate-metadata → create)
- [ ] PATCH system-prompt as creator edit endpoint with owner check
- [ ] Admin ban/unban via X-Admin-Key with Google Chat notification
- [ ] Push notifications for AI responses + human messages
- [ ] All 7 external integrations preserved (Gemini, OpenRouter, Replicate, metadata, S3, Google Chat, Postgres)
