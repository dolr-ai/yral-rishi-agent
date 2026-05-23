# Internal RPC Contracts — Service ↔ Service

> Inter-service calls inside the v2 cluster. All on Swarm overlay `yral-v2-internal` per C3. JSON over HTTP (FastAPI). No public exposure.

## Authentication between services

Services trust each other on the overlay (no public access per C3). Optional mTLS in future phases. Each request carries:
- `X-Internal-Caller: <service-name>` (for tracing)
- `X-Trace-Id: <uuid>` (for end-to-end Langfuse correlation)
- `X-User-Id: <user-id>` (forwarded from public-api after JWT validation)

Downstream services trust X-User-Id without re-validating (per E6).

## public-api → orchestrator

> **READ THIS FIRST.** The shape below is the INTERNAL RPC payload that
> the orchestrator returns to public-api. It is NOT what mobile clients
> receive. Public-api wraps this payload inside the locked
> `ApiResponse<MessageResponse>` envelope (per `00-api-contract.md`)
> before returning to mobile. Internal callers and mobile clients see
> DIFFERENT outer shapes; only the inner `MessageResponse` fields are
> byte-shape-identical to chat-ai's existing parity contract. Do not
> copy this internal-bare shape into any handler that returns to mobile.

```
POST http://yral-rishi-agent-conversation-turn-orchestrator:8000/v1/turn

Request:
{
  conversation_id: string,         // UUID of the conversation row;
                                   // orchestrator joins on this to find
                                   // user_id + ai_influencer_id (the
                                   // latter feeds the Soul-File lookup).
                                   // Orchestrator MUST verify the
                                   // conversation row's user_id equals
                                   // X-User-Id below before responding;
                                   // mismatch returns 403 (a caller may
                                   // never query another user's
                                   // conversation by id-guessing).
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
                     Orchestrator MUST cross-check this against the
                     conversation_id's owning user before doing any work;
                     reject 403 on mismatch.
  X-Idempotency-Key  REQUIRED from day 1, per F10 (default-on for every
                     non-GET endpoint). Same key + same user/conversation
                     within 24h MUST return the previously created
                     assistant MessageResponse from Redis without a
                     second LLM call. Implementations MUST ship the
                     Redis-backed dedup at the same time as the route
                     itself — F10 forbids deferring it.
                     Backend MUST be the C11 Sentinel-aware Redis client
                     (NOT `redis.asyncio.Redis.from_url(...)` directly).
                     Dedup MUST be atomic against concurrent duplicate
                     requests (e.g. `SET NX` in-progress lock + completed
                     payload, or Lua/transaction). Reject 400 if the
                     header is missing.
  X-Request-Id       Per Langfuse correlation, D4.

Response (internal-bare): JSON MessageResponse — the orchestrator returns
the bare object below to public-api over the internal RPC. The mobile-
facing endpoint (`POST /api/v1/chat/conversations/{id}/messages` on the
public-api) wraps this object in `ApiResponse<MessageResponse>{success,
msg, error, data}` per `00-api-contract.md`. The inner field names and
types below are byte-shape-identical to chat-ai's existing parity
contract — do not mutate them, only the outer wrapper differs by hop.
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
other internal response model owned by v2 (`InfluencerResponse`, etc.).
EXCEPTION — external contracts we consume but do not own (e.g.
yral-billing's `ChatAccessDataDto` later in this doc) keep their source-
party name in the doc + JSON shape; the internal Python class that
deserializes it may use a B-rules-compliant alias, but the wire format
and the doc reference both keep the external-owned name.

Source of truth: `yral-rishi-agent-conversation-turn-orchestrator/app/models/turn.py`
(`RunTurnRequest` + `MessageResponse` Pydantic models) and
`yral-rishi-agent-conversation-turn-orchestrator/app/run_turn.py`
(`POST /v1/turn` handler). Update this section if those models change.

## public-api → influencer-and-profile-directory

```
GET http://yral-rishi-agent-influencer-and-profile-directory_service:8000/v1/influencers
  ?limit=<int 1..100>
  &offset=<int >=0>
→ list[InfluencerResponse]    [PROPOSED — see DEP-013]

GET http://yral-rishi-agent-influencer-and-profile-directory_service:8000/v1/influencers/{id}
→ InfluencerResponse

POST .../v1/influencers (create flow)
→ InfluencerResponse

PATCH .../v1/influencers/{id}/system-prompt
→ InfluencerResponse

DELETE .../v1/influencers/{id}
→ {}
```

Mostly thin proxy — public-api forwards to influencer-directory.

**Headers on every request** (4 internal-call headers per public-api's
`directory_client._internal_headers()`): `X-User-Id` (forwarded from
the public-api JWT-validated user); `X-Internal-Caller`
(`yral-rishi-agent-public-api`); `X-Request-Id` + `X-Trace-Id` (both
carry the same value from public-api's `request_id_middleware`). No
`X-Idempotency-Key` on GETs (stateless reads; F10's per-endpoint
opt-out applies). The directory MAY mTLS-verify the caller by SAN
when the Day-N internal-mesh-mTLS lands; current shape relies on
the same-overlay-mesh trust model that orchestrator → soul-file
already uses.

**The list endpoint (`GET /v1/influencers?limit&offset`) is the
PROPOSED contract from DEP-013 (Session 3, 2026-05-22).** Session 4
ratifies when they build the real endpoint at
`yral-rishi-agent-influencer-and-profile-directory/app/api/`, or
pushes back with a different shape and Session 3 adjusts public-api's
wrapper accordingly. The by-id + create + edit + delete shapes are
the previously-declared contract on main.

**Pagination semantics:**
- `limit`: 1..100 plain int (matches yral-mobile
  `ChatRemoteDataSource.kt:50-70` listInfluencers contract — plain
  offset/limit, no cursor). Default `20`.
- `offset`: 0-indexed non-negative int. Default `0`.
- Response is a flat `list[InfluencerResponse]` — no `total_count`
  or `next_offset` wrapper today; mobile derives "more pages
  available" client-side from `len(items) == limit`. Future PR can
  add a `count` header or wrap the body if the catalog grows beyond
  the natural one-shot read.

**Note: stack-service DNS naming.** The Swarm DNS name for the
directory service is `<stack>_<service>` →
`yral-rishi-agent-influencer-and-profile-directory_service` (per
project.config + the compose service name `service`). Previous
version of this section dropped the `_service` suffix; updated here
to match the actual Swarm DNS resolution.

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

→ ApiResponse<ChatAccessDataDto>  // External contract from yral-billing
                                  // (Ravi-owned). Keep the source-party
                                  // name in this doc + JSON wire shape.
                                  // Per E7. Our internal Python class
                                  // may alias to a B-rules-compliant name
                                  // (e.g. ChatAccessData), but the
                                  // serialised JSON field and class
                                  // identifier mirror yral-billing's
                                  // existing release contract.
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
