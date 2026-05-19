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
POST http://yral-rishi-agent-conversation-turn-orchestrator:8000/v1/turn

Request:
{
  conversation_id: string,         // UUID of the conversation row;
                                   // orchestrator joins on this to find
                                   // user_id + ai_influencer_id (the
                                   // latter feeds the Soul-File lookup).
  user_message: string,            // Raw text the user typed. PII per
                                   // H6 — log only LENGTH, never the
                                   // value.
  media_urls: string[] | null,     // Attachment URLs from the user's
                                   // message (images/audio/video). REQUIRED
                                   // by A8 multi-modal parity; do NOT
                                   // drop. Public-api forwards these
                                   // inline so the orchestrator does not
                                   // need a second DB read per turn.
  client_message_id: string | null // Optional client-side dedup id the
                                   // mobile app may attach to the user
                                   // message; orchestrator echoes it
                                   // onto persisted user-msg traces but
                                   // assistant replies do NOT carry one.
}

Headers (ALL three required on every call):
  X-User-Id          Forwarded from public-api after JWT validation, per E6.
  X-Idempotency-Key  REQUIRED from day 1, per F10 (default-on for every
                     non-GET endpoint). Same key + same user/conversation
                     within 24h MUST return the previously created
                     assistant MessageResponse from Redis without a
                     second LLM call. Implementations MUST ship the
                     Redis-backed dedup at the same time as the route
                     itself — F10 forbids deferring it.
  X-Request-Id       Per Langfuse correlation, D4.

Response: JSON MessageResponse (byte-identical to chat-ai parity per A8 + A16)
{
  id: string,                        // fresh UUID per assistant reply
  conversation_id: string,           // echoes the request's conversation_id
  role: "user" | "assistant",        // orchestrator always returns "assistant"
  content: string,                   // the assistant reply text
  media_urls: string[] | null,       // attachment URLs the assistant
                                     // returns (null today; real Day-5+
                                     // may include generated images)
  client_message_id: string | null,  // null on assistant replies; copied
                                     // from request on user messages
                                     // (public-api owns user-msg persist)
  created_at: string,                // ISO8601 UTC, "YYYY-MM-DDTHH:MM:SSZ"
  count_toward_paywall: boolean      // E7 paywall counter; safety-blocked
                                     // turns flip false once H4/H5 land
}
```

Used: every chat turn. Plain JSON response (NOT SSE) per A16 — mobile
parity requires byte-shape-identical to chat-ai's existing
`POST /api/v1/chat/conversations/{id}/messages` contract. The v1 path
stays plain-JSON forever for parity stability. SSE streaming per E2
(first-token <200ms p95 target) lives at a separate `POST /v2/turn-stream`
path behind a feature flag per the Session-4 agent definition — the v1
JSON shape above never silently mutates into a stream.

Naming note (B1 + B2 + Rishi 2026-05-19): the response model is
`MessageResponse`, NOT `MessageDto`. The "DTO" abbreviation is not on the
B2 allowed-abbreviation list and the project's English-naming rule applies
to Python class names, not only JSON fields. Same rule applies to every
other response model in this doc (`InfluencerResponse`, etc.).

Source of truth: `yral-rishi-agent-conversation-turn-orchestrator/app/models/turn.py`
(`RunTurnRequest` + `MessageResponse` Pydantic models) and
`yral-rishi-agent-conversation-turn-orchestrator/app/run_turn.py`
(`POST /v1/turn` handler). Update this section if those models change.

## public-api → influencer-and-profile-directory

```
GET http://yral-rishi-agent-influencer-and-profile-directory:8000/influencers/{id}
→ InfluencerResponse

POST .../influencers (create flow)
→ InfluencerResponse

PATCH .../influencers/{id}/system-prompt
→ InfluencerResponse

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

→ ApiResponse<ChatAccessDataResponse>
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
