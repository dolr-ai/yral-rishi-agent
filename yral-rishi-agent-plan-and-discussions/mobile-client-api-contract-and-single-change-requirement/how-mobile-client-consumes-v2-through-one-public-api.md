# How the Mobile Client Sees V2 (answering Rishi's question 2026-04-23)

**Rishi asked:** "I don't understand if we'll need to change API calls in the mobile front end client, or if the heavy lifting of being able to use the new services will be done using the one public API."

**Short answer:** the heavy lifting IS done by ONE public API. Mobile sees one URL, one set of endpoints. It never knows there are 13 services behind the scenes.

## Plain-English explanation

Today, mobile talks to ONE server:
```
mobile → chat-ai.rishi.yral.com → (the Python yral-chat-ai, going live 2026-04-25)
```

That Python service does EVERYTHING internally: chat, influencers, billing check, memory extraction, moderation. One service, one URL, one codebase.

In v2, mobile STILL talks to ONE server:
```
mobile → chat-ai.rishi.yral.com → yral-rishi-agent-public-api
                                      │
                                      ↓ (internally routes to)
                                      ├─→ orchestrator
                                      ├─→ soul-file-library
                                      ├─→ user-memory-service
                                      ├─→ agent-skill-runtime
                                      ├─→ ... 9 more services
                                      └─→ content-safety-and-moderation
```

Mobile doesn't see any of the 13 services individually. All it sees is the same `chat-ai.rishi.yral.com` URL with the same endpoint shapes. The internal routing is our problem, not mobile's.

## So does the mobile app need any code changes?

**Goal: ONE bundled change.** The ideal is zero, but we want streaming (SSE) in v2.0, which requires mobile updates. We bundle everything into ONE mobile release:

### What mobile needs to change (bundled into a single release)

1. **SSE streaming support** (the big one). Mobile's Ktor HTTP client needs an SSE parser (~100 lines of Kotlin in `/shared/libs/http/`). New `sendMessageStream()` method returns a Kotlin Flow of token events. Feature-flagged via Firebase Remote Config so it can be rolled back instantly.
2. **Presence heartbeat** for Plan B.0 first-turn nudge. Client sends a lightweight ping every 10s while the chat screen is in foreground. Uses existing WebSocket channel. ~50 lines.
3. **Chip dismissal on auto-fired bot message**. When a new bot message arrives (streamed or unsolicited), if the chat's Default Prompts chips are still visible, dismiss them. Small UI state change.

### What mobile does NOT need to change

- Base URL (`chat-ai.rishi.yral.com` stays; Caddy on rishi-1/2 upstream-proxies to rishi-4/5 when v2 is ready)
- Authentication (same OAuth2 JWT via yral-auth-v2)
- Paywall flow (same Google Play IAP, same `/google/chat-access/check` + `/grant` endpoints, same JSON shape `ApiResponse<ChatAccessDataDto{hasAccess, expiresAt}>`)
- Endpoint URLs for chat, influencers, messages, conversations — all identical in v2 (feature parity, strict superset)
- Chat-as-Human toggle, Switch Profiles, Message Inbox — all unchanged
- Bot creation 3-step flow — backend improves quality, but mobile flow identical

## How the cutover works (timing at Rishi's discretion — not now)

Once v2 is ready and mobile ships the streaming release:
1. Caddy on rishi-1/2 adds ONE new snippet that routes `chat-ai.rishi.yral.com` to rishi-4/5 with weighted percentages (start 1%, ramp to 100%)
2. Mobile's Firebase Remote Config flips `enable_chat_streaming=true` per-user-cohort (matches the backend percentage)
3. Users on new stack get streaming + memory + Soul Files etc.; users on old stack keep the Python service
4. If anything breaks, flip the Caddy weight back to 0% and/or Firebase flag to false — INSTANT rollback, no code change

## Where v2's public-api actually lives

Service: `yral-rishi-agent-public-api` (on rishi-4/5/6 Docker Swarm)
External URL: `agent.rishi.yral.com` (during testing + canary; a separate subdomain under the existing wildcard)
Internal: reachable at overlay DNS `yral-rishi-agent-public-api` from other v2 services

The public-api's job:
- Receive HTTPS from mobile (via rishi-1/2 Caddy forwarding)
- Validate JWT against `auth.yral.com` JWKS (fixes Ravi's `insecure_disable_signature_validation` gap)
- Call yral-billing for pre-chat access check (for paywall)
- Route to orchestrator for chat turns, creator-studio for prompt coaching, etc.
- Return responses in the EXACT same JSON shape mobile expects today

## The "one API" promise in code

Every one of the 13 services internally can have its own REST/gRPC/event interface. The public-api is the ONLY service exposed externally. Everything else is service-mesh-internal on encrypted Swarm overlays.

This means:
- We can refactor internal services freely without breaking mobile
- We can split services further (30+ instead of 13) without breaking mobile
- We can change internal languages later if needed (Go/Rust hot path) without breaking mobile
- The contract with mobile is ONLY the public-api's endpoint shapes + response shapes

## Backup strategy if even one mobile change is too much

If Sarvesh/Shivam say "we can't ship the streaming release in time," we have a fallback:
- Ship v2 with **both** the new SSE endpoint AND the legacy non-streaming endpoint
- Mobile uses the non-streaming endpoint (zero change) during canary
- Streaming ships as v2.1 whenever mobile is ready
- Canary cutover still happens at any percentage; rollback still instant via Caddy

This preserves the "one-change-max" rule literally: if we can't make the one change, we make ZERO.
