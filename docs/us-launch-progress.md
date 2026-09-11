# US Launch — progress & to-do

**Living tracker** (update in place). Started 2026-08-08.
Design spec: `docs/us-market-launch-spec-2026-08-08.md` — but the **strategy evolved** past it during planning; this file is the current source of truth.

## Current strategy
- **Lead with a clean SFW YRAL subscription** (App-Store-safe). The spicy/Amorae funnel is a **separate, later rail** — keep it out of anything Apple/Google/Facebook can see (policy risk).
- **Acquisition = paid Facebook UA, deep-linked straight into a specific persona's chat** (the ad targets US; the deep link routes). The market *feed* filter is for **organic** US only, not the paid test.
- **Monetize via the existing `yral-billing`** (Apple+Google; per-influencer: chat access / subscriptions / images / credits) — **extend it, don't add RevenueCat.** Recommend the **per-influencer** model over a flat "pro" tier (built + higher ceiling).
- **The test = revenue probability with 4 clean SFW US personas.**

## The real critical path (what actually gates the paid test)
1. **Mobile persona-chat deep-link route** → Play release + **iOS App Review (~1 wk)** ← the one true gate. Everything else (attribution, deep links, billing) already exists.
2. **4 SFW personas built** (LoRA/soul/avatar) — creative long-pole.
3. **Facebook ad account survives policy review** — de-risk THIS week, zero code.

## Tracks & to-do

### A. Personas — LONG POLE (Rishi + creative)
- [ ] Pick 4 clean SFW personas (name + one-line vibe). **NOT Mira/Nyx** (those are amorae semi-NSFW — separate identities, to avoid App-Review linking the mainstream app to adult content).
- [ ] Build each: LoRA, soul file, avatar, greeting, suggested messages.
- [ ] Tag `target_markets='{US}'` in `ai_influencers`.
- [ ] **DECISION (open):** per-influencer vs flat-pro monetization.

### B. Mobile — persona-chat deep link (session) — APP REVIEW GATE
- [ ] Add `ConversationRoute(influencerId)` → existing `openConversation`. ~30–50 lines in shared KMP (fixes Android + iOS). Branch + Facebook SDK + Meta Install Referrer + deferred deep link are **already built**.
- [ ] Rishi Motorola pass before ship (mandatory).
- [ ] Ship → Play release + iOS App Review. **Start the clock ASAP** — it's the gate.
- Note: `X-Market` header **deferred** — not needed for the Facebook deep-link test; only for the organic US feed.

### C. Billing absorption → `yral-rishi-billing` (session) — URGENT (Sarvesh left)
- [ ] Rishi: run `grant-ssh-access` for `sarvesh-1/2/3` (team_member=rishi).
- [ ] **Secure the DB + secrets from sarvesh-1** (88.99.58.111) — irreplaceable, time-sensitive.
- [ ] Fork `yral-billing` → `yral-rishi-billing`; deploy on the rishi swarm.
- [ ] Cutover: DNS repoint `billing.sarvesh.yral.com` → our fleet (mobile keeps working, no app review); update Apple/Google server-notification (RTDN) URLs; verify a live purchase + entitlement.
- [ ] **CHECK:** does billing already capture store-account **country** per user? (→ real US detection without `X-Market`)
- [ ] Entitlement wiring: v2/mobile ↔ billing — **who enforces the paywall.**

### D. Market filter PR1→PR2 (session) — for ORGANIC US, dormant
- [ ] PR1: `target_markets TEXT[]` column + dormant config (`pg_dump` first — Rule 9).
- [ ] PR2: one shared `market.py` helper; filter **discovery only** (feed/search/recs), **never the detail endpoint** (H2H list-vs-detail scar); **bypass Redis** for exclusive markets (query DB directly); **cycle, don't backfill**; `X-Market-Debug` override + a **principal-force debug hook** (test the US feed on your own phone, no mobile change).
- Note: **not** on the paid-Facebook critical path — this is organic-US polish.

### E. Facebook UA (Rishi / marketing)
- [ ] **This week, zero code:** small US Facebook test → generic chat tab (`$deeplink_path=/chat`). Validate the riskiest unknown for cheap: **ad APPROVAL (policy)**, US CPI, attribution firing.
- [ ] **Real test (~1 wk):** ad for Persona X → deep link `$deeplink_path=chat/conversation/<influencerId>` → land in her chat → convert.
- [ ] **FLAG:** Facebook is hostile to AI-companion/"virtual girlfriend" ads — keep the spicy/Amorae funnel **completely invisible** to Facebook or risk an ad-account ban.

### F. Store products (Rishi, consoles)
- [ ] Verify the paid-apps agreements exist (you take payments both stores → almost certainly yes).
- [ ] Create the subscription / per-bot products + US pricing (depends on the model decision).

### G. Measurement (analytics)
- [ ] Per-persona funnel dashboard: ad → install → chat → convert.
- Pipeline is **BACK** (consumer re-enabled 2026-08-08). ⚠️ **Aug 5→6 (~30h) has NO data** — those two days look artificially thin; don't read a traffic dip into them.

## Analytics re-enable — DONE (2026-08-08)
- `events-consumer` 1/1 on the fixed image (`events-1124540`), 34-min soak clean, ingestion ~9s behind live, Saikat's bridge healthy.
- **Follow-ups (not urgent):**
  1. Tell Saikat the fix is deployed + his bridge is clear; suggest a **bridge-side consumer-instance timeout** (auto-cleans orphans from any ungraceful client death — a node reboot/OOM our client fix can't fully cover).
  2. **`deploy-events-consumer.yml` is misnamed** — it only injects the Vault bridge token as a Swarm secret (no build, no deploy). The real deploy = hand-build on **rishi-6** (`~/yral-rishi-analytics-build`) + `docker service update --no-resolve-image`; that repo has **no image-build CI** (tags never reach GHCR). Rename it or give the repo a real build-and-deploy job before the next incident.
  3. **~30h of events permanently lost** (2026-08-05 04:25 → 2026-08-06 10:29 UTC) — aged out of Kafka's ~48h retention during the pause.
- **Standing lesson:** a paused consumer has a hard **~48h budget** before Kafka data starts dying; past that, every hour of pause is unrecoverable.

## Decisions locked
- Clean SFW subscription first; amorae/spicy = separate later rail.
- Personas: separate clean SFW set (not Mira/Nyx).
- Facebook via deep-link-to-persona (not the market filter, not `X-Market`).
- Extend `yral-billing`, not RevenueCat.
- IP-geo rejected (swarm ingress masks the client IP, and it disagrees with billing).

## Open decisions (need Rishi)
- **Per-influencer vs flat-pro** monetization model.
- **The 4 persona picks** (names + vibes).
