# Coordination Asks — Running List

Every open ask from Rishi to Saikat (infra), Sarvesh + Shivam (mobile), Naitik (Vault/Coolify), or Ravi (existing services). This file is updated as asks are made + answered, so nothing falls through the cracks.

Status legend: 🟢 answered/done · 🟡 asked, awaiting response · ⚪ not asked yet · ❌ declined/blocked

## Saikat (CTO — infra, servers, DNS, DB, team infra)

| # | Ask | Status | Answer (if any) | Blocking? |
|---|---|---|---|---|
| S1 | Provision rishi-4/5/6 (3 new servers, Hetzner bare metal) | 🟢 | Done 2026-04-23. IPs captured in memory + plan. | no |
| S2 | Confirm DNS routing strategy: Cloudflare stays as-is, rishi-1/2 Caddy upstream-proxies specific subdomains to rishi-4/5 | 🟢 | Done 2026-04-23. | no |
| S3 | Confirm reuse of existing Sentry on rishi-3 (`sentry.rishi.yral.com`) for all v2 services | 🟢 | Done 2026-04-23. Confirmed by Rishi. | no |
| S4 | OK to add Redis Cluster (Sentinel mode) as new dependency on rishi-4/5/6 | 🟢 | Done 2026-04-23. Approved. | no |
| S5 | OK to self-host Langfuse on rishi-4 for LLM tracing | 🟢 | Done 2026-04-23. Approved. | no |
| S6 | Confirm SSH/root access model for rishi-4/5/6 (time-limited root + rishi-deploy user + scoped sudoers) | 🟢 | `rishi-deploy` user, key `~/.ssh/rishi-hetzner-ci-key`, Saikat gives ~1-week root for bootstrap. Confirmed in memory. | no |
| S7 | Confirm hardware specs (i7-6700, 62.6 GB RAM, 2× NVMe, Ubuntu 24.04.4) for rishi-4/5/6 | 🟢 | Confirmed 2026-04-23 via Saikat. | no |
| S8 | SLA on rishi-1/2 Caddy uptime (our v2 availability is coupled to rishi-1/2) | ⚪ | Not yet asked. Not blocking. Would be good to know for runbook. | no |
| S9 | Self-service Sentry project creation permission on `sentry.rishi.yral.com` (we need ~13 projects) | ⚪ | Not yet asked — Rishi owns the Sentry instance so likely self-serves. Confirm. | minor |
| S10 | GPU capacity for eventual self-hosted LLM (Month 7-12+) | ⚪ | Not urgent — deferred per Rishi's 2026-04-23 answer. | no |
| S11 | Disk layout confirmation on rishi-4/5/6 (RAID1 expected; verify via `/proc/mdstat` day-0) | ⚪ | Day-0 verification per Rishi's Q5 answer 2026-04-23. | no |
| S12 | Datacenter confirmation — rishi-6 Nuremberg vs Falkenstein (affects Patroni sync replica placement) | ⚪ | Day-0 verification. | no |

## Sarvesh (iOS + Android mobile) + Shivam (Android)

| # | Ask | Status | Answer (if any) | Blocking? |
|---|---|---|---|---|
| M1 | Confirm DNS-routing-at-Caddy strategy works WITHOUT any mobile code change for cutover (base URL stays `chat-ai.rishi.yral.com`) | 🟢 | Mobile base URL is hardcoded to `chat-ai.rishi.yral.com` in `AppConfigurations.kt`. Caddy routing at rishi-1/2 does the work. Zero mobile change needed for URL. | no |
| M2 | Confirm paywall 402 response schema preservation — v2 matches Ravi/Python byte-for-byte | 🟢 | Paywall is NOT a 402 — it's a pre-chat `ApiResponse<ChatAccessDataDto{hasAccess, expiresAt}>`. V2 matches this shape. Confirmed in mobile architecture memory. | no |
| M3 | Confirm streaming SSE in v2.0 (requires ~1-2 weeks of mobile work: Ktor SSE parser + Compose wiring + Firebase Remote Config flag + fallback to JSON path) | 🟡 | Rishi's decision 2026-04-23: streaming IS in for v2.0. But the ask to Sarvesh/Shivam for the actual implementation timeline is still pending. Bundle with M4 + M5. | yes (Phase 1 exit) |
| M4 | Bundle Plan B.0 presence heartbeat (<1 day mobile work) with the streaming release | 🟡 | Bundled per Rishi's 2026-04-23 decision. Ask Sarvesh confirm bundling is OK. | yes (Phase 1 exit) |
| M5 | Bundle chip-dismissal-on-auto-fired-message with the streaming release (<1 day) | 🟡 | Bundled per Rishi. Ask Sarvesh confirm. | yes (Phase 1 exit) |
| M6 | Debug APK (or debug flavor in build config) that points `CHAT_BASE_URL` at `agent.rishi.yral.com` for Rishi's personal testing | 🟡 | Needed for Tier 1 testing per local-android-testing-shortest-path doc. Ask when streaming release is in progress. | no (Tier 0 works without) |
| M7 | Firebase Remote Config user-targeting rule for Rishi's account (`enable_v2_backend=true` for user_email=rishi@gobazzinga.io) | ⚪ | Needed for Tier 2 testing. Later. | no |
| M8 | Mobile app events list updates (new server-side events may need client-side event names) | ⚪ | Minor; later. | no |
| M9 | Walkthrough of any undocumented mobile behaviors / message types / UI states relying on backend behavior (so v2 API is a strict superset) | ⚪ | 15-min call once v2 API is close to frozen. | no |

## Naitik (Vault + Coolify)

| # | Ask | Status | Answer (if any) | Blocking? |
|---|---|---|---|---|
| N1 | Vault read access for team-shared secrets (e.g., `YRAL_METADATA_NOTIFICATION_API_KEY`) from v2 services | ⚪ | Existing template already does this (`infra.get_secret()`); inherited pattern. Probably already works with existing `VAULT_TOKEN` GitHub secret. Confirm. | minor |
| N2 | No new secrets being PUSHED to Vault from v2 (only reads) | 🟢 | Per Rishi's secret-management pattern memory: GitHub Secrets primary, Vault read-only for shared. Locked. | no |

## Ravi (yral-auth-v2, yral-ai-chat, yral-metadata)

| # | Ask | Status | Answer (if any) | Blocking? |
|---|---|---|---|---|
| R1 | Confirm `auth.yral.com/.well-known/jwks.json` endpoint exists + stable (we need it to validate JWT signatures properly in v2) | ⚪ | Ravi unresponsive per Rishi 2026-04-23. We take the safer approach: fetch JWKS, validate; if endpoint doesn't exist, we implement it ourselves or ask Ravi again. | minor |
| R2 | Walkthrough of how ICP `USER_INFO_SERVICE.get_user_profile_details_v_7` works for CallerType resolution (BotAccount vs MainAccount) — needed for "Chat as Human" | ⚪ | Ravi unresponsive. We copy the pattern from Ravi's Rust chat service code. | minor |
| R3 | List of any feature/endpoint in Ravi's Rust `yral-ai-chat` that is NOT in our Python `yral-chat-ai` (to avoid feature regression) | ⚪ | We'll audit the Rust repo ourselves. See feature-parity-with-existing-chat-services-audit/ | no |

## Ansuman (recommendation / discovery)

| # | Ask | Status | Notes |
|---|---|---|---|
| A1 | Any endpoints v2 needs to expose for the feed/discovery system to query? (e.g., "give me trending influencers by chat count") | ⚪ | Preserved from existing Python endpoint `GET /api/v1/influencers/trending`. Confirm no additional needed. |

## Running summary

- **Hard blockers right now (before Phase 0 can start):** none. Servers done, Sentry confirmed, secrets pattern locked.
- **Soft blockers (before Phase 1 exit):** M3 + M4 + M5 mobile streaming release.
- **Nice-to-haves (any time):** S8, S9, N1, R1-R3, A1.

Update this file when we ask or get answers. Any new ask added here gets a number.
