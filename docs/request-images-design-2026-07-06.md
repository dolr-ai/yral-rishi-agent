# Request Images — Design Doc

**Date:** 2026-07-06
**Author:** Product Ideas session (D8), spawned by Session 6
**Status:** DESIGN ONLY — no code, no PR. Awaiting Rishi decisions before dispatch.
**Feature:** a third item ("Request Images") in the chat "+" menu that drops a collage of 5–8 AI-generated influencer images into the chat. Subscribed → clear + fullscreen viewer. Non-subscribed → blurred → paywall. One request per user per day. **The crown jewel: one image batch per bot per day is generated once and reused across all users — cost is one batch, revenue is per-user paywall.**

---

## Grounding (verified against current code, 2026-07-06)

- **Replicate wrapper already exists:** `app/services/replicate.py` — `generate_image()` (base, `black-forest-labs/flux-dev`, `config.py:87`) + `generate_image_with_reference()` (`black-forest-labs/flux-kontext-dev`, 9:16, guidance 2.5, 30 steps). Polls predictions API, `Prefer: wait`. Used today for avatar gen (`character_generator.py:175-183`).
- **Storage:** S3/Hetzner, presigned URLs (`config.py:71-78`, `storage.generate_presigned_url()`, `routes/chat.py:166`).
- **`ai_influencers`:** has `avatar_url` + `metadata` JSONB; **no** `reference_image_url` / LoRA column (`migrations/001_initial.sql:3-29`). `is_nsfw` at :16.
- **Messages:** `message_type IN ('text','multimodal','image','audio')`, `media_urls` JSONB (`001_initial.sql:56-85`, `models.py:137-147`). There's already a fresh-per-call image endpoint `POST /conversations/{id}/images` (`routes/chat.py:1149`) with **no caching** — our feature adds the reuse layer.
- **⚠️ Subscription gate is NOT wired in the backend.** `BILLING_URL` defined (`config.py:112`) but unused; no `is_subscribed()`, no 25/50 gate. Subscription truth today lives in **mobile IAP** (`ConversationViewModel.launchInfluencerSubscriptionPurchase`, `grantChatAccessUseCase`) + presumably `billing.yral.com`. **A backend subscription check is a prerequisite for server-side blur enforcement.**
- **Content safety is INPUT-only** (`content_safety.py` — crisis/injection/NSFW on *user messages*; NSFW skipped for NSFW bots). **No output/image safety layer exists.**
- **Rate limiting:** hot-editable Redis + `rate_limit_config` DB table (`app/rate_limiter.py`) — the pattern to mirror.
- **Background loops:** `asyncio.create_task` in `main.py` lifespan, 24h cadence, kill-switch-gated (`kill_switch.py`) — the pattern for a nightly pre-gen job.
- **Next migration = 046** (043 + 045 exist; 037/044 skipped).
- **Mobile** reuses cleanly: attachment menu = `YralContextMenuItem` list (`ConversationInputArea.kt:140-161`); fullscreen `ImagePreviewOverlay.kt`; Coil3; downsample-blur (`YralBlurThumbnail.kt`); toast (`Toast.kt`); flag pattern (`ChatFeatureFlags.kt`). Unknown message types fall back to `TEXT` (safe degradation).

---

## 1. Architecture

Three sub-problems: **reuse/race**, **theme rotation**, **generation timing**.

### 1a. The reuse pattern + the race (RECOMMENDED: reservation-row lock)

One row per bot per day is the source of truth:

```
influencer_collages
  PRIMARY KEY (bot_id, generation_date)   -- exactly one per bot per UTC day
  status IN ('generating','ready','failed')
  theme, image_urls_clear[], image_urls_blurred[], cost_usd, generated_at, error
```

A request resolves like this — the **primary key IS the lock**, so the race needs no extra machinery:

```
1. INSERT (bot_id, today, status='generating') ON CONFLICT (bot_id, generation_date) DO NOTHING
2. If I inserted the row  → I am the elected generator. Fire ONE batch. On done: UPDATE status='ready'.
3. If I did NOT insert     → a row already exists:
     - status='ready'      → serve it now
     - status='generating' → return {pending, poll GET /collage}; client polls until ready
     - status='failed'     → serve yesterday's ready set (fallback) OR retry-elect
```

This is the same `INSERT … ON CONFLICT DO NOTHING` idiom used in the ETL (Rishi-approved pattern). Five simultaneous requests → exactly **one** generation fires; the other four attach to it. No Redis lock, no 5-way waste, idempotent, crash-safe (a stuck `generating` row older than N minutes is reclaimable by a watchdog).

**Alternatives considered:** (A) Redis distributed lock — more moving parts, redundant given the PK. (B) fire-5-pick-1 — wastes 4× generation cost, violates the crown-jewel economics. **Reservation-row wins.**

### 1b. Theme rotation (RECOMMENDED: per-bot theme table, day-index cycle, hot-editable)

```
influencer_collage_themes (bot_id, idx, theme_prompt, active)
day_index = floor(epoch_seconds / 86400) mod (count of active themes for bot)
```

- **Why a table, not hardcoded config or LLM-generated:** Rishi's ADHD-observability rule — every knob is *hot-editable, not buried in code*. A table lets Rishi read/edit/reorder Tara's themes ("Capri beach volleyball" → "NYC fashion week" → "Eiffel Tower tease") in one place, no deploy. LLM-generated themes are a *fast-follow* (auto-fill new themes when the list runs low), not v1 — a user-facing tease line generated wrong is worse than a curated one.
- **Phase 0:** seed a single hardcoded theme for Tara to validate the pipeline.

### 1c. Generation timing (RECOMMENDED: HYBRID)

| Option | Latency | Cost | Verdict |
|---|---|---|---|
| **On-demand** (first requester triggers, others wait) | first requester waits 30–90s | pays only for bots someone asks about | cheapest, worst first-UX |
| **Pre-generated** (nightly 04:00 UTC, all active bots) | zero wait | burns budget on bots nobody asks about | best UX, wasteful at scale |
| **Hybrid** ✅ | zero wait for hot bots; long-tail first-requester waits once | pays for proven-hot + actually-requested | **best of both** |

**Hybrid = pre-generate at 04:00 UTC for *verifiably hot* bots** (≥ N chats OR ≥ M image-requests over the last 7 days), **on-demand-with-reservation-lock for everyone else.** "Active bot" (chatted in last 24h OR one request today) governs *whether* on-demand is allowed at all, so inactive bots never burn budget. For **v1 (Tara only)** she's hot → effectively pre-generated → users never wait. But we still build the on-demand path (it's the first-run + long-tail fallback).

**Recommended architecture: HYBRID.**

---

## 2. Face + body consistency — the crown jewel

Consistency is non-negotiable: day 0 Tara ≠ day 1 Tara → churn. Tara is a **named character we reuse indefinitely** — the textbook case for a **trained LoRA**, not a one-shot reference.

### Model options on Replicate (2026), for photorealistic Indian-descent female, varied poses/scenes

| Approach | Model(s) | Consistency | Per-bot setup | Cost / 8-img batch | Latency | Notes |
|---|---|---|---|---|---|---|
| **Trained FLUX LoRA** ✅ | train `ostris/flux-dev-lora-trainer` → infer `flux-dev` + LoRA | **Highest, durable across days** (learns identity, not a pose) | 15–30 curated images + one train run (~$1–3, minutes) | ~$0.16–0.32 ($0.02–0.04/img) | ~30–90s | Best face+body lock; the whole business rests on this |
| Reference-based, no training | `black-forest-labs/flux-2-pro` (up to 8 ref imgs) | ~85%, **drifts across very different scenes/days** | none (just ref images) | ~$0.44 ($0.055/img) | fast | Great for bootstrapping *new* bots |
| Zero-shot likeness | `runwayml/gen4-image` | strong likeness, weaker scene coherency | none | mid | mid | Replicate's own top pick for photo likeness |
| ❌ Kontext Pro | `flux-kontext-pro` | "face artifacts frequently unusable" | none | $0.04/img | 5s | No — face-critical feature |

### Recommendation

- **v1 (Tara): trained FLUX LoRA.** Strongest, most durable consistency at the lowest per-image cost, and Tara is generated forever so the one-time training amortizes to nothing. Optionally stack a face adapter (InstantID-style) for extra lock, but LoRA alone is likely sufficient — validate in Phase 0.
- **Adding future bots:** same LoRA pipeline (curate 15–30 refs → train → store weights). For the *long tail* where per-bot LoRA training isn't worth it, fall back to **FLUX.2 multi-reference** (no training, ~$0.055/img) — accept slightly lower consistency for zero setup. Document both; default flagship/hot bots to LoRA.
- **Storage:** reference images + trained LoRA weights → S3 (same Hetzner bucket as avatars). New on `ai_influencers`: `reference_image_url TEXT`, `lora_weights_url TEXT`, `lora_version INT` (or nest under `metadata` JSONB to avoid a column churn — recommend real columns for queryability + symmetry with `avatar_url`).
- **Cost math at scale:** at ~$0.25/bot/day, 1,000 hot bots = ~$250/day generation — but that's *one batch serving all requesters*, so it's flat regardless of user count. Cap via the "active bot" gate + a daily budget ceiling (Risk 2).
- **Latency:** ~30–90s/batch — invisible under pre-gen; the on-demand first-requester sees it once (loading state, §3).

---

## 2.5. Content-safety on the crown-jewel (new requirement)

Rishi's own theme examples ("skimpy travel," "teasing in low blouse at the Eiffel Tower") deliberately ride the SFW/NSFW line, and Tara is `is_nsfw=true`. **There is no output/image safety layer today.** Two needs:
- **Keep it inside store limits:** these images render *in the app* (unlike the spicy-chat design, which pushed NSFW to a separate web brand — see `project_spicy_chat_gate_design`). So the collage must stay **suggestive-but-clothed** — no explicit nudity in the app. The theme prompts must be authored to that line, and we should run a **cheap output check** (Replicate's safety flag if available for the model + an optional vision classifier) before caching a batch. If a batch trips, don't cache it; alert.
- This intersects the spicy-gate work: if we ever want *explicit* images, they belong on `amorae.ai`, not the app collage.

---

## 3. UX flow

1. **Tap "+"** → menu now shows **Camera · Photo library · Request Images** (`ConversationInputArea.kt` — add a third `YralContextMenuItem`, gated by `RequestImagesEnabled`).
2. **Tap "Request Images"** → immediate optimistic **placeholder collage message** with a loading shimmer ("Tara is putting together some photos… 📸"). This keeps the chat responsive whether the batch is cached (instant swap) or generating (30–90s).
3. **When images arrive** → the placeholder is replaced by a **`collage` message** (5–8 images in a 2–3 row grid). Recommended injection: a real assistant message `message_type='collage'` persisted to the conversation, so it survives reload and appears in history. If the user backgrounded the app during an on-demand wait, a **push notification** ("Your photos from Tara are ready") re-engages (reuse existing push; new `data.type`).
4. **Subscribed** → collage is **clear**; tap → **fullscreen 1-at-a-time viewer** (extend `ImagePreviewOverlay.kt` into a swipeable pager).
5. **Non-subscribed** → collage is **blurred** (server-serves pre-blurred blobs, §5); tap anywhere → routed to the **subscription flow** (`InfluencerSubscriptionCard` / `launchInfluencerSubscriptionPurchase`). On successful purchase → refetch → images unblur in place.
6. **Rate-limit UI (already requested today):** grey the menu item + on tap show a **small toast** ("New photos from Tara tomorrow ✨"). Window = **one per (user, bot) per UTC calendar day**, resetting 00:00 UTC (aligns with the collage's `generation_date`; simpler than rolling-24h and matches "today's set"). *(Per-user vs per-user-bot and UTC-vs-rolling are Rishi calls — see open questions.)*
7. **Error path (generation failed after tap):** serve **yesterday's cached ready set** silently if one exists (user still gets images, consistency holds); if none exists (brand-new bot), show a soft toast ("Couldn't fetch photos, try again shortly") and **do not consume the user's daily quota**.

---

## 4. Backend changes

### New endpoints (v2)
- **`POST /api/v1/influencers/{id}/request-images`** — the user's request. Checks rate limit + active-bot gate → resolves via the reservation-row logic (§1a). Returns one of: `{status:'ready', collage}` · `{status:'pending', collage_id}` (poll) · `{error:'already_requested_today', resets_at}`.
- **`GET /api/v1/influencers/{id}/collage`** — fetch today's collage: `{theme, image_urls[], generated_at, blurred: bool}`. **Server decides clear-vs-blurred URLs by the caller's subscription status** (§5). Used for polling + reload.
- **`GET /api/v1/admin/collage-generation-jobs`** — observability (today's batches, status, cost, failures) → dashboard + daily email (Rishi's ADHD-observability rule).

### New tables (migration **046**)
```
influencer_collages
  (bot_id, generation_date) PK, status, theme,
  image_urls_clear JSONB, image_urls_blurred JSONB, cost_usd, generated_at, error
user_image_requests
  (user_id, bot_id, request_date) PK  -- rate limit "1 per user-bot per UTC day" + audit
  requested_at, collage_bot_id, collage_date  -- FK to the served collage
influencer_collage_themes
  (bot_id, idx) PK, theme_prompt, active
```
pg_dump before applying (Rule 9). Additive only.

### Existing tables touched
- `ai_influencers`: `+ reference_image_url TEXT`, `+ lora_weights_url TEXT`, `+ lora_version INT` (or `metadata` JSONB — recommend real columns).
- `messages`: add `'collage'` to the `message_type` CHECK constraint (trivial alter). Collage image list rides existing `media_urls`; `metadata` carries `{theme, collage_date, blurred}`.

### New service modules
- `services/image_collage.py` — orchestrates: rate-check → reservation-lock → theme select → generate batch (LoRA) → blur variants → cache → serve. Caches by `(bot_id, date)`.
- Extend `services/replicate.py` — add a `generate_batch(prompt, lora_weights_url, n)` + a `train_lora(reference_images)` helper (thin, reuses existing polling).

### New background loop
- Nightly **04:00 UTC** pre-gen (`asyncio.create_task` in `main.py`, kill-switch-gated): iterate hot bots (≥N chats/requests over 7d) → generate *tomorrow's* collage. Same shape as `streak_loop` / `video_ideas_loop`.

### Prerequisite (NOT free)
- **Backend subscription check** — a `billing.is_subscribed(user_id, bot_id)` calling `billing.yral.com`, cached briefly. Required for server-side blur (§5). **This does not exist yet** and must be built or stubbed. Flagged as a gating decision.

---

## 5. Mobile changes (coordinate DTO with Sarvesh)

- **New message type:** add `COLLAGE("collage")` to `ChatMessageType.kt`; collage images ride existing `mediaUrls` on `ChatMessageDto` (+ optional `collageMeta`). Unknown-type fallback to `TEXT` already exists (old clients degrade safely). **Sarvesh alignment REQUIRED** before mobile ships (the message contract changes).
- **Collage component:** new `CollageImageGrid.kt` (5–8 images, 2–3 rows) rendered in `ConversationMessageBubble`. Tap handler: subscribed → fullscreen pager (extend `ImagePreviewOverlay.kt`); non-subscribed → subscription flow.
- **Blur = server-side (recommended).** The backend serves **pre-blurred image blobs** to non-subscribers (a blurred copy generated at batch time, stored in S3). Client just renders what it's given. **Why server-side:** a client-side blur (downsampling `YralBlurThumbnail`) still ships the *clear* pixels to the device → a debug tool extracts them → the paywall is defeated and the crown jewel leaks. Server-side blur means non-subscribers never receive clear pixels. (Client-side blur is cheaper to serve but insecure — not acceptable for a paywalled asset.)
- **"+" menu item:** string "Request Images", an icon, `onRequestImagesClick` → calls the endpoint.
- **Rate-limit state:** grey the item + small toast (`Toast.kt`, `ToastStatus.Info`).
- **Feature flag:** `RequestImagesEnabled`, **`defaultValue=false`** in `ChatFeatureFlags.Chat` (HARD RULE — `feedback_all_agent_features_need_flags_until_cutover`). Checked at the menu item + the message rendering + the request call.
- **HARD GATE:** no mobile PR opens until **Rishi tests on his Motorola + explicit "go"** (`feedback_mobile_no_pr_without_rishi_motorola_pass`).

---

## 6. Risks

1. **Consistency failure (day 0 Tara ≠ day 1 Tara) — biggest business risk.** Mitigation: trained LoRA (best available lock) + a fixed **seed + locked LoRA weights per bot** so days are reproducible; **Phase 0 human-eye QA gate** (Rishi eyeballs day 0 vs day 1 before GA); if the chosen model regresses on a future run, the reservation-row watchdog + "serve yesterday's set" fallback prevents a bad batch from shipping, and we alert. Keep a manual "reject + regenerate" admin lever.
2. **Cost explosion when many bots go active.** Mitigation: the "active bot" gate (no chat/request in 24h → no generation) + a **daily generation budget ceiling** (hot-editable, like `rate_limit_config`); when hit, stop pre-genning cold bots and log what was skipped (no silent cap). One batch serves all users, so cost scales with *bots*, not users — the ceiling is predictable.
3. **Subscription paywall race (sub expires mid-view).** Mitigation: the clear-vs-blurred decision is **evaluated server-side per fetch**, so an expired sub simply returns blurred URLs on the next `GET /collage`; we do **not** claw back an image already rendered in a live session (bad UX, negligible cost). Short server-side cache of subscription status bounds the window.
4. **Content safety on outputs (Eiffel-Tower-lingerie drift).** Mitigation: theme prompts authored to *suggestive-but-clothed*; run Replicate's safety flag (if the model exposes one) + optional vision classifier before caching; a tripped batch isn't cached and alerts. Explicit imagery belongs on `amorae.ai`, never the in-app collage (store risk — ties to `project_spicy_chat_gate_design`). **No output-safety layer exists today — this is net-new.**
5. **Model rights / licensing.** Verify the chosen Replicate model's commercial-use + output-ownership terms (FLUX dev vs pro licensing differs; LoRA trained on our own Tara reference images is our derivative, but the base model license governs commercial output). Confirm before GA; document the license in the doc. Also: Tara's *reference likeness* must be one we own the rights to (AI-generated original, not a real person's photo) — confirm the reference set's provenance.

---

## 7. Rollout plan

- **Phase 0 — Tara only, YRAL team, hardcoded theme.** Single theme ("Tara at Capri beach volleyball"), trained LoRA, end-to-end pipeline: request → generate → blur variants → cache → collage message → viewer/paywall. **Human QA gate:** Rishi eyeballs consistency (day 0 vs a re-run) before proceeding. Backend subscription check can be stubbed to a test cohort. Flag on for team only.
- **Phase 1 — 5 hot bots, a week of themes prepped by Rishi.** Themes seeded in `influencer_collage_themes`; nightly pre-gen live for these 5; real backend subscription check wired to `billing.yral.com`; dashboard + daily cost email on. Cohort rollout via Remote Config.
- **Phase 2 — all active bots, dynamic theme rotation.** Day-index cycle across each bot's theme list; hybrid timing (pre-gen hot + on-demand long-tail); budget ceiling enforced; LLM theme-auto-fill fast-follow when a bot's list runs low.

**Standing gates:** pg_dump before 046; flag `defaultValue=false` until cutover; Rishi Motorola pass before any mobile PR; deploy pipeline never bypassed; every knob (themes, budget ceiling, rate limit, flag) hot-editable with dashboard + daily email.

---

## Decision log (Rishi, 2026-07-06) ✅

1. **Subscription check = STUB for Phase 0, build real before GA.** Phase 0 (Tara, YRAL team) hardcodes/test-cohorts subscription to validate the pipeline; wire the real `is_subscribed()` → `billing.yral.com` before Phase 1 real users. ✅
2. **Blur = SERVER-SIDE pre-blurred blobs.** Non-subscribers never receive clear pixels; backend generates + stores a blurred copy of each image at batch time, serves blurred URLs to non-subs. Secure against extraction. ✅
3. **Rate limit = per user-bot, UTC calendar day.** One request per bot per UTC day (resets 00:00 UTC, aligns with the collage's `generation_date`). `user_image_requests` PK `(user_id, bot_id, request_date)`. ✅
4. **Consistency = trained FLUX LoRA per bot (v1 Tara).** Best/durable lock, cheap inference. FLUX.2 multi-reference stays the documented long-tail path for future bots. Needs 15–30 rights-owned Tara reference images (see remaining item). ✅

**Remaining minor items (not blockers — pick at dispatch):**
- (a) **Image count** per batch — recommend **6** (generous, bounds cost/latency). Rishi to confirm 5/6/8.
- (b) **Reference images for the LoRA** — need 15–30 curated Tara images we own the rights to (AI-generated originals, not a real person). Source: reuse/extend her existing avatar-gen pipeline. Rishi to confirm provenance.
- (c) **Daily budget ceiling** — value + who gets the "ceiling hit" alert (recommend: hot-editable like `rate_limit_config`, alert to Rishi's daily email + dashboard).
