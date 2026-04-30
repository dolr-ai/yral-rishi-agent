# Internal RPC Contracts — Service ↔ Service

> Inter-service calls inside the v2 cluster. All on Swarm overlay `yral-v2-internal` per C3. JSON over HTTP (FastAPI). No public exposure.

## Authentication between services

Services trust each other on the overlay (no public access per C3). Optional mTLS in future phases. Each request carries:
- `X-Internal-Caller: <service-name>` (for tracing)
- `X-Trace-Id: <uuid>` (for end-to-end Langfuse correlation)
- `X-User-Id: <user-id>` (forwarded from public-api after JWT validation)

Downstream services trust X-User-Id without re-validating (per E6).

## public-api → orchestrator

```
POST http://yral-rishi-agent-conversation-turn-orchestrator:8000/turn

Request:
{
  user_id: string,
  conversation_id: string,
  ai_influencer_id: string,
  message_content: string,
  client_message_id: string,
  media_urls: string[] | null
}

Response: SSE stream of events
  event: token       data: { delta: "..." }
  event: token       data: { delta: "..." }
  event: complete    data: { message: MessageDto }
  event: error       data: { code, message }
```

Used: every chat turn. Streaming response per E2.

## public-api → influencer-and-profile-directory

```
GET http://yral-rishi-agent-influencer-and-profile-directory:8000/influencers/{id}
→ InfluencerDto

POST .../influencers (create flow)
→ InfluencerDto

PATCH .../influencers/{id}/system-prompt
→ InfluencerDto

DELETE .../influencers/{id}
→ {}
```

Mostly thin proxy — public-api forwards to influencer-directory.

## orchestrator → soul-file-library

```
GET http://yral-rishi-agent-soul-file-library:8000/composed-prompt
  ?influencer_id=<id>
  &user_segment=<new|paying|dormant>

→ {
    layered_prompt: string,    // 4 layers concatenated
    version_pin: string,       // for rollback if needed
    cache_hit: boolean
  }
```

Hot path. Must be <5ms warm cache hit (E1 budget).

## orchestrator → user-memory-service

```
GET http://yral-rishi-agent-user-memory-service:8000/context
  ?user_id=<id>
  &influencer_id=<id>
  &recent_messages=10

→ {
    semantic_facts: [{fact_text, confidence}],
    user_profile: {tone_preference, language, ...},
    recent_episodes: [...]
  }

POST http://yral-rishi-agent-user-memory-service:8000/extract-async
{
  user_id, message_id, content
}
→ 202 Accepted (fire and forget)
```

`/context` is hot path (parallel-fetched per Section 2.7). `/extract-async` is fire-and-forget for memory extraction.

## orchestrator → content-safety-and-moderation

```
POST http://yral-rishi-agent-content-safety-and-moderation:8000/check-input
{
  user_id, message_content
}
→ {
    safe: boolean,
    crisis_detected: boolean,
    flag_reason: string | null
  }

POST .../check-output
{
  user_id, response_content
}
→ same shape
```

Pre-LLM check on user message + post-LLM check on response. Per H4, must be live before any real-user canary.

## public-api → yral-billing (EXTERNAL — Ravi's service)

```
GET https://yral-billing.../google/chat-access/check
  ?user_id=<id>&bot_id=<id>

→ ApiResponse<ChatAccessDataDto>
```

Cached in v2 Redis 60s per E7. Per D1 — yral-billing is external; we consume.

## payments-and-creator-earnings → yral-billing (EXTERNAL)

```
GET https://yral-billing.../transactions?bot_id=<id>&since=<timestamp>
→ Transaction[]
```

Read-only mirror. v2 caches earnings rollups; we never write to yral-billing's ledger.

## All services → Sentry (sentry.rishi.yral.com)

Standard Sentry SDK. DSN per service from secrets.yaml. Tag `service=<name>` per D3. Per A7 + C4 — NEVER apm.yral.com.

## All services → Langfuse (rishi-6 self-hosted)

Standard Langfuse SDK. Public + secret keys from Vault per D8 (shared, not per-service).

Every LLM call auto-traced per D4 + middleware in template.

## Event stream (Redis Streams)

Services emit + consume via overlay `yral-v2-data-plane`. Stream keys:

| Stream | Producer | Consumer(s) |
|---|---|---|
| `events:user.message.sent` | public-api | analytics, memory-extractor |
| `events:turn.completed` | orchestrator | analytics, bot-quality-scorer |
| `events:memory.candidate` | orchestrator | memory-service |
| `events:influencer.created` | influencer-directory | analytics |
| `events:safety.flagged` | content-safety | analytics, audit-log |
| `events:payment.completed` | payments | analytics, earnings rollups |

Standard envelope:
```json
{
  "event_id": "uuid",
  "event_type": "user.message.sent",
  "timestamp": "ISO8601",
  "user_id": "...",
  "data": { ... }
}
```

## Failure modes

- Downstream timeout → return graceful fallback (e.g., orchestrator without memory enrichment)
- Downstream 5xx → log to Sentry, return `service_unavailable` to caller
- Network partition → Patroni/Sentinel handle stateful; stateless services already replicated 3×
