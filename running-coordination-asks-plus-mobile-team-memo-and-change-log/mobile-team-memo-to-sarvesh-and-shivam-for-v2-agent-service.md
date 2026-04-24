# Memo to Sarvesh + Shivam — Mobile changes for the v2 agent platform

**Authors:** Rishi (lead) + Claude (architecture)
**Date:** 2026-04-23
**Status:** Draft for Sarvesh review (no work begins until Sarvesh + Saikat sign-off)

---

## TL;DR

We're building a brand-new chat backend (`yral-rishi-agent-*`, 13 services on rishi-4/5/6) that will eventually replace `yral-chat-ai` (the Python service that went live 2026-04-23). It coexists indefinitely; cutover happens only when Rishi explicitly says so.

**The mobile-side change asks below have been carefully derived after auditing the live `yral-chat-ai` Python service AND reading every file in `yral-mobile/shared/features/chat/` end-to-end.** No assumptions; everything is grounded in what mobile actually does today.

The mobile asks fall into three buckets:

1. **MUST changes for v2 launch** (~1.5-2 weeks of mobile work) — gated to streaming + first-turn nudge
2. **SHOULD verifications** (~half a day total) — quick tests Sarvesh runs to confirm assumptions
3. **MAY-defer features** (Plan F+ creator tools, monetization) — explicit deferrals, not part of v2.0

If we don't ship streaming + first-turn nudge in v2.0, the MUST changes drop to ZERO and v2 cuts over with no mobile work via Caddy upstream switch on rishi-1/2.

---

## What we found by reading the mobile repo

**Mobile architecture (verified from yral-mobile main branch):**
- Kotlin Multiplatform (KMP); ~90% shared
- Ktor HTTP client; **NO existing WebSocket or SSE usage** in production code
- Inbox is **poll-based** with pull-to-refresh + `refreshConversations()` triggered on screen visit
- Auth has working token refresh: `DefaultAuthClient.refreshTokensSilently()` runs on 401
- Chat URL is **hardcoded** to `chat-ai.rishi.yral.com` in `AppConfigurations.kt` (NOT remote-configured)
- Mobile uses Firebase Remote Config for feature flags (`featureflag/providers/FirebaseRemoteConfigProvider.kt`)
- Mobile UI for messages already supports incremental render via Compose `isWaiting` state in `ConversationMessageBubble`
- Image upload path: `api/v1/media/upload`

**API endpoints mobile actually hits today** (from `ChatRemoteDataSource.kt` constants):
| Const | Path | What |
|---|---|---|
| `INFLUENCERS_PATH` | `api/v1/influencers` | Influencer CRUD (read for now) |
| `INFLUENCER_FEED_PATH` | `api/v1/influencer-feed` | Recommendation feed (Ansuman's service, separate) |
| `CONVERSATIONS_PATH` | `api/v1/chat/conversations` | Create + send msg + get msgs + read + delete |
| `CONVERSATIONS_LIST_PATH` | `api/v2/chat/conversations` | Inbox list (bot-aware) |
| `UPLOAD_PATH` | `api/v1/media/upload` | Media upload |

**Important:** mobile uses **v1 + v2** API paths simultaneously (NOT v3). v3 unified inbox is unused by mobile.

**Mobile has Tara-specific billing product** (`ProductId.TARA_SUBSCRIPTION` + `ProductId.DAILY_CHAT`) — Tara has her own subscription SKU. This may or may not affect v2; flagging because billing service interactions matter.

**Mobile has retry logic** for billing grant access (`retryGrantAccess()`) — good token-handling practice already in place.

---

## What v2 will preserve EXACTLY (no mobile change needed)

V2's `yral-rishi-agent-public-api` service will be a **strict superset** of the live yral-chat-ai's API. Every endpoint mobile hits today returns the same path, same method, same auth header behavior, same JSON shape. Specifically:

- Same chat URL: `chat-ai.rishi.yral.com` (no DNS change; Caddy on rishi-1/2 routes to v2 cluster when Rishi says so)
- Same Authorization Bearer JWT model
- Same `/api/v1/chat/conversations` shape (create, send-message, get-messages, mark-read, delete)
- Same `/api/v2/chat/conversations` inbox shape
- Same `/api/v1/influencers` + 3-step creation flow + system-prompt PATCH + admin endpoints
- Same `/api/v1/media/upload` shape with presigned S3 URLs (15-min expiry)
- Same `client_message_id` deduplication semantics
- Same multi-modal message types (`text`, `multimodal`, `image`, `audio`)
- Same audio transcription via Gemini (transcription prepended to `content` as `[Transcribed: ...]`)
- Same error envelope (`ApiResponse<T> { success, msg, error, data }` for billing-related; standard FastAPI for chat)
- Same paywall pattern: mobile pre-checks via `yral-billing/google/chat-access/check` before sending; chat backend NEVER returns 402
- Same Cache-Control 300s on influencer GETs
- Same image-history-window optimization (only last 3 messages have inlined images for the LLM call)

The mobile app, with **zero code change**, can hit v2 once Caddy routes traffic to it. This is the safety net.

---

## MUST: changes if we want streaming + first-turn nudge in v2.0

These are the bundled mobile changes per the mobile-one-change rule. Estimate: **1.5-2 weeks of mobile work**, single shared-code PR (covers iOS + Android automatically).

### Change 1 — Add Server-Sent Events (SSE) parser to Ktor client

**Why:** v2 streams chat responses token-by-token via SSE. Mobile needs to render tokens as they arrive (first token <200ms target).

**What:**
- File: new `/shared/libs/http/src/commonMain/kotlin/com/yral/shared/http/SseClient.kt` (~100 lines)
- Wraps Ktor's `HttpClient.request()` returning a raw `ByteReadChannel`
- Parses SSE lines (split on `\n\n`, extract `data:` prefixed payloads)
- Returns a Kotlin `Flow<SseEvent>` to caller

**Reusable:** any future SSE need (notifications, live feed, etc.) reuses this.

### Change 2 — Add `sendMessageStream()` method to `ChatRemoteDataSource`

**Why:** dual-path approach — keep existing `sendMessageJson()` as fallback; add streaming path for v2.

**What:**
- File: `/shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/ChatRemoteDataSource.kt`
- New method `sendMessageStream(conversationId, message): Flow<ChatStreamEvent>`
- Calls `POST /api/v1/chat/conversations/{id}/messages` with header `Accept: text/event-stream`
- v2 server detects this header and switches to streaming response

**Event types** (server emits):
```
data: {"type":"token","content_delta":"Hi"}
data: {"type":"token","content_delta":" there"}
data: {"type":"token","content_delta":"!"}
data: {"type":"complete","message":{...full ChatMessageDto...}}
```
On error: `data: {"type":"error","message":"..."}`

### Change 3 — Update `ConversationViewModel` to consume the Flow

**Why:** existing `sendMessageJson()` returns one JSON; new flow returns incremental tokens. UI needs to update content as tokens arrive.

**What:**
- Insert assistant message with empty `content` + `isWaiting=true` (UI already handles this state)
- On each `TokenDelta`, append `content_delta` to the assistant message's content; Compose recomposes
- On `Complete`, mark `isWaiting=false` and persist final message via existing DB insert
- On `Error`, render error state and offer retry

Compose-side rendering is essentially unchanged — `ConversationMessageBubble` already shows `isWaiting` then content; we just update content live.

### Change 4 — Firebase Remote Config flag for streaming

**Why:** safe rollout. Default off; flip per-cohort during canary; instant rollback if anything breaks.

**What:**
- New flag: `enable_chat_streaming: boolean` (default: `false`)
- In `ConversationViewModel.sendMessage()`: read flag, route to either `sendMessageJson()` (existing path, fallback) or `sendMessageStream()` (new path)
- Mobile already uses Firebase Remote Config — just one new flag definition

### Change 5 — Plan B.0 first-turn nudge: presence heartbeat

**Why:** when user opens a chat with an influencer, sees the greeting + 3-4 chips, but doesn't act for 25 seconds, the bot should auto-fire a follow-up message to nudge. Mobile must signal "user is still on the chat screen."

**What:**
- File: `/shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/ui/conversation/ChatConversationScreen.kt`
- While chat screen is in foreground: every 10 seconds, send `POST /api/v1/chat/conversations/{id}/presence` (NEW endpoint v2 exposes; pure HTTP; no payload)
- On screen exit: stop the heartbeat
- v2 server tracks last-presence-ping per (user, conversation); after 25s of inactivity (no presence ping AND no user message), scheduler fires the follow-up message

**Note:** this is HTTP polling, NOT WebSocket. Reuses existing Ktor client. ~50 lines.

### Change 6 — Receive auto-fired bot messages while chat screen is open

**Why:** when v2's scheduler fires a follow-up message (Plan B.0) OR sends a proactive ping (cross-session), mobile needs to render it as a new bot message. Currently mobile's chat screen has no way to receive server-pushed messages while open (no WS, no SSE for inbox).

**Two options for this — Sarvesh's call:**

**Option A (recommended): reuse the SSE channel from change 2.**
After the streaming response ends (server emits `data: {"type":"complete",...}`), keep the SSE channel open in "watch mode" while chat screen is in foreground. Server pushes new messages on this channel:
```
data: {"type":"new_message","message":{...full ChatMessageDto...}}
```
Mobile inserts the new message into the conversation. When user sends next message, channel is reused or recycled.

**Option B (simpler, less elegant): poll every 5s while chat screen is open.**
Already-existing polling pattern (similar to how InboxScreen polls). Add a 5-second tick that calls `GET /api/v1/chat/conversations/{id}/messages?since=<lastTimestamp>` and appends new messages. Less efficient than SSE, but simpler.

Option A is cleaner; option B is faster to ship. We're fine either way — Sarvesh chooses based on his comfort with SSE-as-event-stream.

### Change 7 — Chip dismissal on auto-fired bot message

**Why:** when the auto-fired follow-up arrives, the Default Prompts chips should disappear (they're stale; the bot has spoken again).

**What:**
- Existing logic: chips disappear when user types or taps a chip
- Add: chips also disappear when ANY new bot message arrives (from streaming or push)
- ~10 lines of UI state change in `ChatConversationScreen`

### Bundled effort estimate

| Change | Time |
|---|---|
| 1. SSE parser in Ktor wrapper | 2-3 days |
| 2. `sendMessageStream` method | 1-2 days |
| 3. `ConversationViewModel` token consumption | 2-3 days |
| 4. Firebase Remote Config flag wiring | 0.5 day |
| 5. Presence heartbeat | 0.5-1 day |
| 6. Auto-fired message receive (SSE watch mode OR polling) | 1-2 days |
| 7. Chip dismissal | 0.5 day |
| Testing (debug APK + integration with v2 staging) | 2-3 days |
| **Total** | **~9-15 days = 2-3 sprints** |

This is the FULL mobile work for v2.0 features. After this PR ships to production, **all subsequent v2 backend changes (memory, Soul Files, Plan F creator tools, proactivity, programmatic AI influencer creation, monetization, etc.) require ZERO mobile changes** because they're all behind the same single API.

---

## SHOULD: verifications Sarvesh runs (~half day total)

These are quick reality checks, not coding tasks. Each is a 15-30 min verification.

### V1: confirm 401-on-expired-JWT triggers `refreshTokensSilently()`

**Why:** v2 MAY enable strict JWT signature validation later (constraint E9). When that flips on, any tokens that fail validation return 401. Mobile must auto-refresh and retry. Currently mobile has `refreshTokensSilently()` in `DefaultAuthClient.kt:110` — verify it's wired to the chat client's 401 path (not just auth-screen).

**How to verify:** intentionally expire a JWT; observe whether mobile auto-refreshes on next chat call OR shows the "session expired" error. Want auto-refresh.

### V2: confirm mobile handles expired presigned-S3 URL gracefully

**Why:** server presigns S3 URLs with 15-minute TTL. If user opens an old conversation 20+ min later, image URLs return 403. Worst case: app crashes. Acceptable: image fails silently, mobile refetches conversation.

**How to verify:** open an old conversation that has images, wait 20 minutes, scroll back to view images. Confirm app doesn't crash and either retries fetch OR shows placeholder.

### V3: confirm `INFLUENCER_FEED_PATH` (api/v1/influencer-feed) is Ansuman's service, not chat-ai

**Why:** I see the constant in mobile code but the path doesn't exist in our chat-ai service. Want to confirm this routes to Ansuman's recommendation service via Caddy → so v2 doesn't accidentally need to expose this endpoint.

**How to verify:** trace one network request from mobile → see which Hetzner service handles it. Or just confirm with Ansuman.

### V4: confirm mobile uses v1 + v2 paths only (NOT v3)

**Why:** I read the mobile constants and saw v1 + v2 only. Want to triple-check there's no v3 path elsewhere in the codebase that would force v2 to support all three API versions instead of two.

**How to verify:** `grep -rn "api/v3" /shared` returns zero results.

---

## MAY-DEFER: features explicitly NOT in v2.0

These are net-new mobile-side features for later v2.x releases. They DO require new mobile work, but they're not on the critical path.

| Feature | Mobile work | Defer to |
|---|---|---|
| Soul File Coach UI (creator-side: chat with LLM to improve your bot) | New screen + new flow | v2.1 |
| Memory display ("Bot remembers about you" tab) | New screen | v2.1 |
| Voice notes from bot (audio playback, server-generated TTS) | Likely zero (existing audio playback) | v2.x as needed |
| Bot quality scorer dashboard for creators | New screen | v2.x |
| Private content / monetization (tip jar, content unlock) | New screens, IAP flows | Plan G launch |
| Programmatic AI influencer creation via MCP | Pure backend; zero mobile | N/A |
| Multi-provider LLM routing (Tara on OpenRouter; Claude for crisis turns; etc.) | Pure backend; zero mobile | N/A |

Backend can ship all of these incrementally; mobile picks them up when ready.

---

## Hidden traps Sarvesh should know about

1. **WebSocket endpoint exists server-side but mobile never used it.** The current `yral-chat-ai` Python service exposes `WS /api/v1/chat/ws/inbox/{user_id}` — but mobile's KMP code has zero WebSocket usage. We were planning Redis pub/sub for cross-node WebSocket delivery in v2; **if mobile truly doesn't use WS, we can deprecate the WS endpoint in v2 entirely.** Confirm: should we keep WS for any future client (web, admin tools), or drop it?

2. **Tara has her own subscription product (`ProductId.TARA_SUBSCRIPTION`) separate from `DAILY_CHAT`.** This implies billing has special Tara-specific SKUs in Google Play. v2's `yral-rishi-agent-payments-and-creator-earnings` service must understand both product IDs. Worth confirming: is Tara's subscription a different price / duration / feature set than other bots? Does v2 need to preserve this distinction?

3. **Mobile inbox is poll-based, not real-time.** When v2's scheduler fires a proactive message (bot pings user), mobile sees it on next inbox refresh OR via push notification — NOT instantly via WebSocket. If we want "live inbox" UX, we'd need either SSE for the inbox screen OR more frequent polling. Currently scope: out. Push notification is the live path.

4. **`/api/v1/influencer-feed` is Ansuman's recommendation service, not chat-ai.** v2 doesn't need to expose this. Caddy on rishi-1/2 routes it to wherever Ansuman serves it from. Confirm with Ansuman if scope ever expands.

5. **`api/v1/upload` vs `api/v1/media/upload`.** The chat-ui-integration.md doc in mobile repo says `POST /api/v1/upload` but the constant in code is `api/v1/media/upload`. Code is truth; doc is stale. v2 preserves `api/v1/media/upload`.

6. **Three chat API versions exist server-side (`v1`, `v2`, `v3`) but mobile only uses v1 + v2.** v2 must preserve v1 + v2 endpoint shapes. v3 (unified inbox) can be deprecated in v2 unless any other client uses it (web admin? testing?). Worth confirming.

7. **Billing flow stays unchanged.** Mobile keeps calling yral-billing's `/google/chat-access/check` and `/google/chat-access/grant` directly — v2 chat service doesn't proxy billing. Same Google Play IAP flow.

8. **Greenfield database means existing chat history is NOT migrated to v2 (Rishi's call 2026-04-23).** When (eventually) Caddy switches to route to v2, users will see EMPTY conversation history with their existing influencers. This is a UX consideration:
   - **Option A:** ship as-is — users see fresh chats; old history accessible via push notification deep-links going to old service domain (preserved behind a different subdomain like `chat-ai-archive.rishi.yral.com`)
   - **Option B:** at cutover, do a one-time ETL to migrate last-30-days-of-chats per (user, influencer) so transition is smooth
   - **Option C:** show a one-time "moved to v2 — old chats archived at..." UI banner that links to the archive service
   - Sarvesh's input wanted on which UX is acceptable.

9. **Cache-Control 300s on influencer GETs is mobile-visible.** Mobile likely relies on 5-min CDN cache for influencer lists. v2 preserves this header. No action needed; just flagging so Sarvesh doesn't accidentally remove client-side caching assumptions.

10. **JWT signature validation flag (constraint E9).** v2 implements proper JWKS-based RS256 validation but DEFAULTS OFF on day 1. We dual-validate in shadow phase, flip ON after 7 days of zero divergence. **Sarvesh action:** verify mobile's 401-handling is robust BEFORE we flip strict on (see V1 above).

---

## What we ASK Sarvesh to confirm

Specifically, please confirm:

1. **Bundle scope OK?** All seven changes (1-7 above) bundled into ONE mobile PR / release. Acceptable?
2. **Estimate OK?** 2-3 sprints feasible, or longer?
3. **SSE vs polling for "new message while chat screen open" (change 6)?** Option A (SSE watch mode) or Option B (5s polling)?
4. **WebSocket endpoint deprecation OK?** Mobile doesn't use WS today; v2 can drop it OR keep it dormant. Your preference?
5. **Greenfield UX for cutover (trap 8)?** Option A (no migration) / B (last-30-days ETL) / C (banner + archive link). Your call.
6. **Tara billing nuance (trap 2)?** Confirm v2 must preserve `TARA_SUBSCRIPTION` + `DAILY_CHAT` product distinction and pre-check semantics.
7. **Verification asks (V1-V4 above)?** OK to do these as a lightweight pre-build sanity check?

---

## What we DON'T ASK (zero change required)

For clarity, here's what stays untouched on mobile:

- Auth flow (OAuth + JWT + refresh) — preserved exactly
- Influencer creation 3-step flow — same endpoints, may have improved Soul File output (invisible upgrade)
- System-prompt edit — same `PATCH` endpoint
- Admin ban/unban — same X-Admin-Key flow
- Inbox screen + pull-to-refresh — preserved
- Pagination semantics (limit/offset) — preserved
- Multi-modal message types — preserved
- Audio transcription behavior — preserved
- Image generation endpoint — preserved
- Read receipts — preserved
- Push notification format from metadata service — preserved
- Cache-Control headers — preserved
- CORS origins — preserved (env-configurable, no client-side change)
- Billing pre-check via yral-billing — preserved (mobile keeps calling yral-billing directly; chat service has zero billing logic)
- Conversation deletion — preserved

---

## Timing

- **Plan-only mode active** — no v2 code is being written until Rishi explicitly says "build"
- **When build approved:** Phase 0 (template + cluster setup) takes 1-3 weeks
- **When mobile work could start:** in parallel with Phase 1 backend (~weeks 4-6 of v2 build) — once v2's `agent-debug.rishi.yral.com` SSE endpoint is testable
- **Timeline for Sarvesh:** estimate to send back to Rishi: how many sprints + which sprint can fit this work?

---

## Single most important thing in this memo

After Sarvesh ships ONE bundled mobile release containing changes 1-7 above, **all 13 v2 backend services and every future product feature land at zero additional mobile cost.** The single mobile API contract (`chat-ai.rishi.yral.com` → all v2 services internally) means the mobile team gets to ship once and then stop worrying about backend churn. That's the headline. That's why bundling is worth the 2-3 sprints.
