# API Contract — Mobile ↔ V2 Public-API

> What the mobile client expects from `agent.rishi.yral.com`. Per A8 (feature parity) and A16 (mobile changes deferred), v2 returns SAME shapes as chat-ai for every endpoint mobile already calls. This file is the source of truth — Sessions 3 (public-api) and 4 (orchestrator + influencer) build to this contract.

## Source of authority

- chat-ai's live endpoints (https://chat-ai.rishi.yral.com) — golden reference
- `live-chat-ai-feature-audit-v2-must-preserve-everything-we-found-here/` — full inventory
- This file — what v2 must match

If chat-ai's response shape differs from this file, chat-ai wins; update this file. Captured shapes get committed as JSON fixtures in `tests/contract/`.

## Shared response envelope (per yral team convention)

Every endpoint returns this envelope:

```typescript
ApiResponse<T> {
  success: boolean,
  msg: string,           // user-facing message ("OK" or error)
  error: string | null,  // machine-readable error code
  data: T | null         // payload (null on error)
}
```

This matches what mobile already parses (per `reference_yral_mobile_architecture.md`). Never break this envelope.

## Endpoints mobile calls (the parity contract)

### Chat

| Method | Path | Purpose | data shape |
|---|---|---|---|
| POST | `/api/v1/chat/conversations` | Create or get conversation w/ AI influencer | `ConversationDto` |
| POST | `/api/v1/chat/conversations/{id}/messages` | Send message, get LLM reply | `MessageDto` |
| GET | `/api/v1/chat/conversations/{id}/messages?limit=N&before=ID` | Paginated history | `MessageDto[]` |
| POST | `/api/v1/chat/conversations/{id}/read` | Mark messages read | `{}` |
| DELETE | `/api/v1/chat/conversations/{id}` | Delete conversation | `{}` |
| GET | `/api/v1/chat/conversations` | v1 inbox | `ConversationDto[]` |
| GET | `/api/v2/chat/conversations` | v2 bot-aware inbox (mobile uses this) | `ConversationDto[]` |
| WS | `/api/v1/chat/ws/inbox/{user_id}` | Real-time inbox push | streaming events |

### Influencers (AI bots)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/influencers` | List all (Cache-Control 300s) |
| GET | `/api/v1/influencers/trending` | Trending |
| GET | `/api/v1/influencers/{id}` | Single |
| POST | `/api/v1/influencers/generate-prompt` | Step 1 of 3-step creation |
| POST | `/api/v1/influencers/validate-and-generate-metadata` | Step 2 |
| POST | `/api/v1/influencers/create` | Step 3 |
| PATCH | `/api/v1/influencers/{id}/system-prompt` | Edit Soul File (creator) |
| POST | `/api/v1/influencers/{id}/generate-video-prompt` | Video gen helper |
| DELETE | `/api/v1/influencers/{id}` | Soft-delete (sets `is_active='discontinued'`) |
| POST | `/api/v1/admin/influencers/{id}/ban` | Admin (X-Admin-Key) |
| POST | `/api/v1/admin/influencers/{id}/unban` | Admin |

### Billing access check (NOT a chat endpoint, but mobile calls it)

Mobile calls yral-billing directly for access check:
```
GET https://yral-billing.../google/chat-access/check?user_id=X&bot_id=Y
→ ApiResponse<ChatAccessDataDto{ hasAccess: bool, expiresAt: ISO8601 }>
```

V2's public-api also caches this in Redis (60s TTL per E7) but mobile path stays unchanged.

### Health

| Method | Path | Purpose |
|---|---|---|
| GET | `/health/live` | process alive |
| GET | `/health/ready` | dependencies healthy |
| GET | `/health/deep` | full round-trip check |

## Response DTOs (JSON shapes)

### MessageDto
```typescript
{
  id: string,                // UUID
  conversation_id: string,
  role: "user" | "assistant",
  content: string,
  media_urls: string[] | null,
  client_message_id: string | null,
  created_at: string,        // ISO8601
  count_toward_paywall: boolean
}
```

### ConversationDto
```typescript
{
  id: string,
  user_id: string,
  participant_b_id: string | null,    // for H2H chat
  ai_influencer_id: string | null,    // for AI chat
  conversation_type: "ai_chat" | "human_chat" | "chat_as_human",
  last_message: MessageDto | null,
  last_message_at: string,
  unread_count: number
}
```

### InfluencerDto
```typescript
{
  id: string,                 // UUID, preserved from chat-ai
  display_name: string,
  bio: string,
  avatar_url: string,
  archetype: string,          // "companion" | "nutritionist" | etc.
  is_nsfw: boolean,
  follower_count: number,
  creator_user_id: string | null,
  is_active: "active" | "discontinued"
}
```

### ChatAccessDataDto (from yral-billing, mirrored by v2 cache)
```typescript
{
  hasAccess: boolean,
  expiresAt: string | null    // ISO8601
}
```

## Headers v2 must accept + handle

| Header | Purpose | Required? |
|---|---|---|
| `Authorization: Bearer <jwt>` | Auth (per E6) | YES |
| `X-Idempotency-Key: <uuid>` | Dedup non-GET (per F10) | Default-on |
| `X-Admin-Key: <key>` | Admin endpoints | Only on `/admin/*` |
| `X-Client-Message-Id: <id>` | Client-side dedup for messages | Optional but standard |

## Error codes (the strings mobile expects in `error` field)

| Code | When |
|---|---|
| `unauthorized` | JWT missing or invalid |
| `forbidden` | JWT valid but no permission |
| `paywall_required` | (NOT 402) — pre-chat access check failed |
| `rate_limited` | Too many requests |
| `not_found` | Resource doesn't exist |
| `validation_failed` | Malformed input |
| `internal_error` | Generic server error |
| `service_unavailable` | Dependency down |

## What v2 may EXTEND (strict superset)

- New optional fields in DTOs (mobile ignores unknown fields safely)
- New endpoints under `/api/v2/`
- New optional headers
- Additional `data.metadata` fields

What v2 may NOT change:
- Existing field names or types
- Existing endpoint paths
- Existing error codes
- Envelope shape

## How sessions use this

- **Session 3 (public-api):** implements every endpoint above. Reads + writes via internal-rpc-contracts to other services.
- **Session 4 (orchestrator + influencer):** implements internal handlers Session 3 calls. Honors DTO shapes.
- **Session 5 (tests):** writes contract tests that hit chat-ai → record golden responses → assert v2 matches.
