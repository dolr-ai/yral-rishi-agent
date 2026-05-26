# Shortest Path to Testing V2 on Your Android Device

**Hard constraint (Rishi, 2026-04-23):** test v2 on Rishi's personal Android phone as soon as possible.

## The three tiers of "testing on your phone"

Different strategies unlock at different build stages. Use the earliest available.

### Tier 0 — Just a browser (unlocks in Phase 0-1)

**When:** as soon as v2's `yral-rishi-agent-public-api` has a `/health/ready` endpoint and accepts a sample chat message via curl.

**What you do:**
1. Open Chrome on your Android phone.
2. Visit `https://agent.rishi.yral.com/health/ready` — should see a JSON response.
3. I build a tiny HTML debug page (no mobile-app change needed) at `https://agent-debug.rishi.yral.com/` that lets you paste your JWT, pick an influencer, type a message, and see the SSE stream render token-by-token in the browser.
4. You test the raw turn lifecycle. Zero mobile-app involvement.

**Pros:** zero mobile-app work needed. Unlocks in Phase 1 when the MVP turn works.
**Cons:** not testing the REAL app UX — just the backend.

### Tier 1 — Debug build of the real YRAL app (unlocks in Phase 1-2)

**When:** once Sarvesh/Shivam add the streaming client + presence heartbeat bundle (~1 week of mobile work after v2 API is stable).

**What you do:**
1. Sarvesh builds a debug APK that points `CHAT_BASE_URL` at `agent.rishi.yral.com` (hardcoded in `AppConfigurations.kt` for the debug flavor).
2. You install this APK on your phone via `adb install` or direct download.
3. Only your phone has it; no other users affected.
4. You use the real YRAL app UX against v2 backend.

**Pros:** real app experience; catches UI-side bugs you wouldn't catch in a browser.
**Cons:** needs Sarvesh's help + debug build CI setup.

### Tier 2 — Firebase Remote Config user-targeting on your prod account (unlocks any time after Tier 1)

**When:** after the streaming client ships to production builds AND v2 is canary-ready.

**What you do:**
1. Firebase Remote Config rule: `if user_id == <rishi_user_id> or user_email == "rishi@gobazzinga.io" → enable_v2_backend=true`.
2. Your normal production phone app now hits v2 for YOUR account only. Everyone else stays on Python.
3. You test with real data, real notifications, real IAP, real everything.

**Pros:** most realistic possible test; your phone is the canary.
**Cons:** highest blast radius if something goes wrong (your real account).

## The plan to unlock Tier 0 fastest

Tier 0 is the shortest path. Here's what needs to exist to hit it:

1. **Rishi-4/5/6 provisioned** — ✅ done 2026-04-23
2. **Phase 0 foundations** — Swarm + Patroni + Redis + Langfuse + Caddy up (1-2 weeks of work after build approval)
3. **V2 template proven via hello-world** — ~3 days after Phase 0 infra ready
4. **Public-api skeleton + orchestrator skeleton spawned from template** — 1 day after template works
5. **MVP turn implemented in orchestrator** — call Gemini via llm-client abstraction, stream tokens via SSE, persist message — ~1-2 weeks
6. **Caddy snippet on rishi-1/2 pointing `agent.rishi.yral.com` to rishi-4/5** — 30 minutes (drop snippet, validate, reload)
7. **Tiny HTML debug page at `agent-debug.rishi.yral.com`** — 1 day; served as static HTML from public-api or its own tiny service

**Total estimated time from build approval to Tier-0-testable on your phone: ~4-6 weeks** (assuming no blockers).

## Auth flow for testing

You need a JWT from `auth.yral.com` that the v2 public-api will accept. Two paths:

- **Use your real YRAL account's JWT:** log in via the production mobile app, intercept the JWT from a network trace (or via a debug override in Sarvesh's debug build), paste into the debug page. Matches the real auth + ICP identity flow.
- **Use a dev/synthetic JWT** (simplest for first testing): create a throwaway dev principal on `auth.dolr.ai` or equivalent, sign a short-lived token, use it only against the v2 cluster. Doesn't touch prod data.

I'll design the debug page to accept either — paste-your-JWT field.

## What I need from Sarvesh + Shivam (for Tier 1, not urgent)

1. Ability to produce a debug APK with a custom `CHAT_BASE_URL` (either a build flavor or a runtime override via a secret dev menu)
2. Same debug APK with `enable_chat_streaming=true` hardcoded for testing

**Not urgent.** Tier 0 (browser) unlocks Tier 1's value for 95% of testing needs. Tier 1 only matters when we want to test UI-specific bugs.

## What I need from Saikat

Nothing new. Just the things already requested:
- rishi-4/5/6 provisioned — ✅ done
- Ability for Caddy snippet on rishi-1/2 to be added (we have `deploy` SSH access — confirmed)

## Local developer testing (for me during build, not you)

While I'm building v2, I need to test the services without hitting rishi-4/5/6 (to avoid burning through provisioning time for every little change). So the template will include:

- `docker-compose.local.yml` — spins up all services locally on Rishi's laptop (or a throwaway EC2) with mock Patroni/Redis
- `scripts/local-smoke-test.sh` — runs a fake chat turn end-to-end locally
- These are NOT used for Rishi's phone testing — they're for my own loop during build

Your phone testing path (Tier 0) uses the REAL rishi-4/5/6 backend. Local dev is orthogonal.
