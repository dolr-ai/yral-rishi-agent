# AI Influencer Discovery Feed — FINAL Design + Build Dispatch (v2)

**Status:** FINAL DRAFT — design only, ready to hand to Session 6 for build
coordination. No code written here. No prod touched.
**Author:** Planning session (Session 6 lineage)
**Date:** 2026-06-16 (rev 8 — FINAL: runpod-vLLM vision fix for avatar classification,
swappable registry routing, Caddy edge-cache dropped for v1, mobile spec companion)
**Companion:** `docs/discovery-feed-mobile-spec-2026-06-16.md` — detailed,
ship-it-yourself mobile instructions (Rishi can run without Sarvesh).
**Service is LIVE (100% prod since 2026-06-15) — every change below is ADDITIVE,
flag-gated, and coexists with Ansuman's feed. Nothing may degrade live UX.**

---

## 0. The system in one mental model

Rank by **what type of influencer a user likes** (category / interest / "people
like you") — **never by gender.** Gender is only a **cold-start guardrail** so a
brand-new user sees a comprehensive *mix of types* and YRAL doesn't read as an
AI-girlfriend app; it fades the moment we learn the user's taste, and then we
**follow their preference (gender included) and never claw it back.** The feed is
**always-fresh** — like opening TikTok, you keep seeing *new* influencer profiles —
bounded by the realities of a finite catalog (§3). Everything is precomputed into
Redis → the page is instant.

---

## 1. Locked decisions (Rishi, 2026-06-16)
| # | Decision |
|---|---|
| Path | **Full rebuild in v2.** Only v2 chat + (later) owner-activity signals. No canister/ClickHouse video metrics. |
| "Preference established" | After **≥5 conversations OR ≥1 in-depth chat** with a clear type. Until then, cold-start breadth rules. |
| Gender | **Cold-start guardrail only** (soft, *not* 50/50). Not a ranking axis, no permanent quota. |
| Type taxonomy | I finalized **8 clean types** (§4). Admin-overridable. |
| `owner_active` | **Deferred** — build later, not at launch. |
| NSFW | **No filter — show all.** Only Tara is NSFW; everything else is SFW, so no gating needed. |
| Discovery vs chat list | Discovery **leads with new/unseen** profiles; a user's existing bots stay in the **chat list**, not the discovery feed. |
| Freshness | **High but pragmatic** — lead with fresh; occasional repeats are fine at our catalog scale (§3). |
| **LLM cost** | **ALL intelligence on Saikat's runpod vLLM (`runpod_vllm`), NEVER Gemini.** Only LLM use = one-time bot classification; all ranking + personalization is **pure SQL → zero ongoing model cost.** Routed via a **new registry process** (swappable to another provider anytime via DB/env override). |
| **Vision** | Saikat's runpod vLLM **does support vision** — so the classifier sends the **profile photo + text** for better gender/type tagging. Requires a one-line registry fix (§4). |
| rumik.ai / pricing | Dropped (written by mistake). |
| Mobile | Rishi takes the mobile change spec to Sarvesh; built + tested on Motorola first. |
| Autonomy | Session 6 dispatches backend → developer session, mobile → mobile expert, fast iterate on Motorola, then PR → mobile repo → live. |

---

## 2. Speed — instant, logged in or out
Backend answers in **one Redis read (~3–8 ms)** whether or not you're signed in
(both the global and the per-user feed are precomputed Redis blobs). The user feels
the **phone's ~80–250 ms round-trip**, not our 8 ms — so "really fast" is won on the
device: **stale-while-revalidate** (paint last feed instantly), **prefetch on app
open**, **small first payload (top ~10)**. These are mobile-side (see companion spec).

**On "Caddy edge cache" — plain English, and why we're dropping it for v1 (you asked):**
Caddy is the "front door" server every request passes through before reaching our
app. "Edge caching" means Caddy *remembers* the answer to a popular request (the
logged-out feed, identical for everyone) for a few seconds and hands back the saved
copy without bothering the app — a little faster, a little less load. **But it's an
extra moving part** (you'd have to reason about when the cached copy goes stale), and
our app is *already* answering in ~5 ms from Redis. **Recommendation: skip it for v1.**
It buys a couple of milliseconds the user can't feel, against real added complexity.
We can add it later if traffic ever demands it — it's a pure optimization, not a
requirement. (Dropping it removes one item from your ops plate.)

---

## 3. Always-fresh discovery — simple version for our scale

**Goal:** lead each visit with *new* profiles so users meet more types of bots —
but kept simple (Rishi: occasional repeats are fine at our scale, so we skip the
heavyweight bloom-filter machinery for now).

- **Lightweight recency down-weight:** track a per-user/session "recently served"
  set in Redis (`seen:<uid>`, short TTL ~3 days) and **down-weight** (not hard-
  exclude) those bots, so fresh profiles lead but a good bot can recur after a bit.
- **Novelty weighting:** `newness` + `momentum` weighted up; `serve_penalty` rotates
  over-shown bots out. The discovery lane leads the feed.
- **Per-session shuffle** so two users don't see an identical order.

**Honest note (no surprise later):** TikTok has infinite videos; we have a finite
catalog, so once an active user has seen most bots they'll start seeing repeats —
which you've said is fine. The real lever for sustained freshness is **more bots**,
so we expose "unseen-pool depth per type" on the admin dashboard as a
**creator-acquisition signal**. Code keeps it fresh; supply keeps it infinite.

---

## 4. Ranking — type/interest backbone (8-type taxonomy)

**Taxonomy (revised 2026-06-16 PM by Rishi — orthogonal two-axis model):**

- **`archetype` (5 fixed):** `companion · advisor · entertainer · educator · creator`
  — psychology-collapsed personas that drive HOW a bot talks (Soul File prompt
  template + LLM temperature). Same set already wired in `ARCHETYPE_PROMPTS`.
- **`category` (free-form):** the WHAT axis — food, travel, weather, fitness,
  entrepreneurship, anime, gaming, lifestyle, etc. Grows organically as creators
  invent new topics. Mobile shows this verbatim; matched in ranking via
  `pg_trgm` similarity.

Earlier rev-8 single 8-value `bot_type` muddled these axes; replaced.

One cheap background LLM pass tags **`archetype` (5-value enum) + `gender`** from the
bot's **profile photo + name + system prompt + description**. **Session 6 verified
empirically 2026-06-16:** runpod_vLLM is loaded with `Qwen/Qwen3.6-35B-A3B-FP8`
(`GET /v1/models`), multimodal smoke green (`prompt_tokens=341` with image vs ~100
text-only), **3.4 sec/bot** with `chat_template_kwargs.enable_thinking=False` (10×
latency win vs reasoning-mode default). Both labels are manually overridable. **This
is the ONLY LLM call in the whole feature, runs on `runpod_vllm`, never Gemini** —
one-time per bot + on create, so cost is negligible.

**Required registry fix (dev, prerequisite to M1):** `runpod_vllm` is currently
`"supports_vision": False` (`llm_registry.py:188`, a leftover from the H12 text-only
era). Saikat's pod now does vision → **flip it to `True`, set the vision-capable model
id, and register a new `influencer_classification` process** in `PROCESS_NAMES` +
`LLM_DEFAULTS` (provider `runpod_vllm`, in `ASYNC_PROCESSES_NEVER_GEMINI`). The H12
capability guard then permits the image-bearing classification call. Routing stays
**swappable** via the normal DB/env override, per Rishi.

**Stage A — global scores (offline, ~15 min):**
```
engagement = 0.40·popularity(conv,msg,users) + 0.25·depth_ratio + 0.20·quality + 0.15·streak
discovery  = 0.45·newness + 0.30·momentum + 0.15·underexposure + 0.10·quality   # weighted toward FRESH
```
**Stage B — personalize (offline, on chat-send + sweep) — ALL pure SQL, zero LLM:**

**REVISED 2026-06-16 PM (Rishi):** `type_affinity` (was: count over 8-value
`bot_type`) replaced with two orthogonal signals matching the new schema:

```
personal = w_arch·archetype_affinity + w_cat·category_affinity
         + w_collab·collab_score    + w_skill·skill_affinity      # gender absent on purpose
```
`archetype_affinity` = counting the user's chats by the 5 archetypes (small
set, exact match, fast). `category_affinity` = trigram match between user's
chat-history category distribution and this bot's free-form `category` via
`pg_trgm` (rich coverage of new topics without schema change). `collab_score`
= bot-bot co-engagement (SQL on the replica); `skill_affinity` =
`user_skill_state` join. **No model calls.** `interest_match` (semantic embeddings) is **DEFERRED** —
it would need Gemini (our embeddings run on `gemini-embedding-001`), which the cost
rule excludes, and runpod doesn't serve a matching embedding model. The three SQL
signals above are plenty for v1; revisit embeddings only if Saikat serves an
embedding model on runpod (§13 dependency).
Cold-start (history below the ≥5-conv / 1-deep-chat threshold) → personal=0.

**Stage C — finalize at request (in-memory):** live overlay (`live_chatters`),
runtime admin pins (`trending_overrides`), **seen-set dedup**, per-session shuffle.

**Post-launch upgrade:** contextual bandit on impression→chat-start (the system
*learns* which types convert for whom). Staged after the deterministic version.

---

## 5. Composer — breadth first, then follow the user
- **Cold-start / pre-threshold:** maximize **bot-type + category diversity** (show
  the breadth of YRAL), guarantee **≥3 skilled bots across ≥3 skills** on screen 1,
  and apply the **soft gender guardrail** (no single gender dominating the first
  screens). Only stage where gender is enforced.
- **Post-threshold:** personalization drives the mix toward the user's real
  type/category taste (**gender included**); keep a **light adjacent-type discovery
  injection** for continued exploration. No gender quota.
- Composer runs **inside the offline jobs** that build `feed:global` / `feed:user`,
  so the balanced slate is precomputed — **zero latency on the request path.**

---

## 6. Architecture (v2-native)
```
                  ┌────────────────────────────────────────────────────┐
 Land on feed ───▶│ GET /api/v2/discovery/influencer-feed (rishi-4/5)   │
  (logged in/out) │  (no edge cache in v1 — app is already ~5ms)        │
                  │  app: 1. GET feed:user:<uid> | feed:global (Redis)  │
                  │       2. MGET live:* pins seen:<uid> (Redis)        │
                  │       3. live overlay + pins + seen-dedup (in-mem)  │
                  │       4. emit FeedResponse (top-10 first page)      │
                  └───────────────┬────────────────────────────────────┘
                       p95 < 100ms (≈5–8ms server; edge ≈1–3ms)
       ┌───────────────────────────┼─────────────────────────────────────┐
       ▼                           ▼                                      ▼
 ┌──────────────┐   ┌────────────────────────────────┐     ┌──────────────────────────┐
 │ Redis         │   │ v2 background jobs (main.py)     │     │ Postgres (Patroni)        │
 │ feed:global   │◀──│ A. global scores ~15 min         │◀────│ ai_influencers(+gender,   │
 │ feed:user:<u> │   │ B. bot_similarity hourly (REPLICA)│     │   +bot_type)              │
 │ live:chat:*   │   │ C. per-user feed on chat-send     │     │ conversations / messages  │
 │ seen:<uid>    │   │ D. classify type+gender (runpod   │     │ bot_quality_scores        │
 │ pins:trending │   │    vLLM) on create + batched sweep│     │ user_skill_state          │
 │ serve:count:* │   │ → jobs A/C run the COMPOSER       │     │ + new tables (§7)         │
 └──────────────┘   │   (no Gemini, no request-path LLM)│     │                           │
                    └────────────────────────────────┘     └──────────────────────────┘
```
**Safety:** the heavy `bot_similarity` co-engagement job runs against the **rishi-6
read replica**, never the primary; the classify sweep is **batched + throttled**;
all jobs degrade to no-op if Redis is down (existing pattern). No request-path LLM
or heavy PG. The feed endpoint is **new and additive** — Ansuman's keeps serving
mobile until the flag flips.

---

## 7. Data model (additive — Rule 9: pg_dump BEFORE, Rishi applies manually)

**REVISED 2026-06-16 PM (Rishi):** Drop the single 8-value `bot_type` taxonomy.
It conflated WHAT a bot is about (food, travel, weather, entrepreneurship,
anime…) with HOW it talks (companion, advisor, entertainer, educator,
creator) — two orthogonal axes that need to stay separate. 8 buckets
couldn't fit the topic diversity AND can't grow with new topics without
schema changes. **Two columns, orthogonal:**

- `category` — **UNCHANGED.** Free-form `VARCHAR(100)`, user-facing, what
  mobile shows ("Food & Drink", "Travel", "Lifestyle", etc.). Grows
  organically as creators invent new topics — no code change needed.
- `archetype` — **NEW real column** with 5 fixed values matching the
  existing `ARCHETYPE_PROMPTS` keys EXACTLY: `companion · advisor ·
  entertainer · educator · creator`. Stays at 5 — these are the
  small psychology-collapsed personas that drive both Soul File composition
  AND discovery-feed style matching. Promoting from "derived at chat
  time" to a real column fixes a silent production bug: 93% of bots
  (3427/3684 active) currently miss the magic-string match in
  `soul_file.py:274` and run with NO archetype prompt layer + default
  temp. Classifier fills it; admin can override.

Discovery-feed ranking uses BOTH columns: `category_affinity` (trigram
match between user's chat-history category distribution and this bot's
category — free-form so use `pg_trgm`) + `archetype_affinity` (counting
the user's most-engaged archetypes). Both are pure SQL; zero LLM at
ranking time.

```sql
ALTER TABLE ai_influencers ADD COLUMN gender    VARCHAR(10) DEFAULT 'unknown';  -- male|female|neutral|unknown
ALTER TABLE ai_influencers ADD COLUMN archetype VARCHAR(32);                    -- 5 enum: companion|advisor|entertainer|educator|creator (NULL until classified)
CREATE INDEX idx_ai_influencers_archetype ON ai_influencers(archetype);
-- pg_trgm for category trigram match (already enabled cluster-wide):
CREATE INDEX idx_ai_influencers_category_trgm ON ai_influencers USING gin (LOWER(category) gin_trgm_ops);

CREATE TABLE bot_similarity (bot_id VARCHAR(255), neighbor_id VARCHAR(255), similarity REAL NOT NULL,
    PRIMARY KEY (bot_id, neighbor_id));
CREATE INDEX idx_bot_similarity_bot ON bot_similarity(bot_id, similarity DESC);
-- bot_embedding table DEFERRED (semantic match needs Gemini embeddings — excluded by cost rule).
CREATE TABLE trending_overrides (influencer_id VARCHAR(255) PRIMARY KEY, pinned_rank SMALLINT NOT NULL,
    note TEXT, expires_at TIMESTAMPTZ, created_by VARCHAR(255), created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE feed_ranking_config (id TEXT PRIMARY KEY DEFAULT 'default', weights JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now());
```
Squawk migration-linter must pass (ADD COLUMN with constant default is metadata-only
on PG11+ — safe, no table rewrite). Per-user feeds, seen-sets, live signals,
composed slates = **Redis only**.

---

## 8. API — mirror Ansuman's envelope (zero mobile change at cutover)
`GET /api/v2/discovery/influencer-feed?offset=&limit=&with_metadata=&session_id=`
(JWT optional; `session_id` enables anonymous freshness dedup). Byte-compatible
`FeedResponse{influencers[], total_count, offset, limit, has_more,
feed_generated_at}`; `with_metadata` carries our signals (gender, bot_type,
momentum, live, rank_source). Admin: pin/unpin, gender+type override, weights editor.
Cutover = Remote-Config base-URL flag (`default=false`).

---

## 9. Open questions — RESOLVED (2026-06-16)
1. **NSFW:** ✅ no filter, show all (only Tara is NSFW; rest SFW).
2. **Own bots in discovery:** ✅ discovery leads with new/unseen; existing bots stay
   in the chat list, not the discovery feed.

All product decisions are locked. Only the §13 dependencies remain to start the build.

---

## 10. BUILD DISPATCH — ready to paste to Session 6

Session 6 coordinates two parallel tracks. **Backend leads; mobile consumes.**
Standing rules apply: feature branches only, **one PR per concern (<400 lines)**,
deploy process (PR → CI green + Codex → Rishi "merge it" → merge → deploy), **pg_dump
before any migration (Rishi applies)**, **all agent-API mobile features flag-gated
(`defaultValue=false`) until cutover**, **no mobile PR opens until Rishi's Motorola
pass + explicit "go."** Nothing may degrade the live v2 service.

### Track A — Developer session (backend), build in this order, each a small PR:
- **M0 — Admin pins:** `trending_overrides` table + `POST /api/v2/admin/discovery/
  pin|unpin` (X-Admin-Key) + dashboard control.
- **M1 — Classification:** `gender` + `bot_type` columns + background classify job +
  admin override. **New process routed to `runpod_vllm` (Saikat's
  `saikat-llm-medium-fast.yral.com`) in `LLM_DEFAULTS`, and added to
  `ASYNC_PROCESSES_NEVER_GEMINI`** so it can never leak to Gemini. Text-only prompt
  (name + system prompt + description), batched + throttled, one-time backfill + on-create.
- **M2 — Global feed + composer:** `GET /api/v2/discovery/influencer-feed` returning
  Ansuman-compatible `FeedResponse` from a precomputed `feed:global`; type/category/
  skill diversity + soft cold-start gender guardrail + seen-set dedup + per-session
  shuffle. Extend the trending matview with momentum/depth/newness. ⚠️ split into
  2–3 PRs.
- **M3 — Live signals:** `INCR live:chat:<bot>` on chat-send (short TTL) + request-path
  overlay.
- **M4 — Personalization core (ALL pure SQL, zero LLM):** `type_affinity` +
  `category_affinity` + `skill_affinity` + nightly/hourly `bot_similarity`
  collaborative job **on the rishi-6 replica** + `feed:user:<uid>`. Gate personalization
  on the **≥5-conversation / ≥1-in-depth-chat** threshold (in-depth = ≥10 user messages
  in one conversation). ⚠️ split.
- **M7 — Cutover prep:** shadow-log v2 vs Ansuman; expose `?debug_source=v2` for Rishi's Motorola.
- **(Deferred: semantic-embedding match (M5), owner_active, bandit — all post-launch.)**

### Track B — Mobile expert (or Rishi solo), in parallel once M2's contract is stable:
**Full step-by-step is in the companion spec `docs/discovery-feed-mobile-spec-2026-06-16.md`**
— written so Rishi can direct or ship it himself if Sarvesh is unavailable. Summary:
- **Phase 1 (the safe minimum — ships the feature):** add a Remote-Config flag
  `discovery_feed_v2_enabled` (`defaultValue=false`); when ON, point the existing feed
  call at `agent.rishi.yral.com/api/v2/discovery/influencer-feed`. **The response shape
  is byte-identical to Ansuman's, so no parsing code changes** — flag OFF = today's
  behavior, exactly. This is the "nothing breaks" guarantee.
- **Phase 2 (polish, optional, additive):** prefetch on app open, stale-while-revalidate,
  small first payload + infinite scroll (offset/limit/has_more), pass stable `session_id`.
- **Gate:** local Motorola test (flip flags LOCAL-ONLY, revert before commit) →
  **Rishi tests on his Motorola** → explicit "go" → archive at-risk docs → add Sarvesh
  reviewer → PR → mobile repo → build → flag flip live.

### Cutover (after both tracks green + Rishi sign-off):
Flag flip mobile → v2 (alpha team → %). Rollback = flip back to Ansuman (instant, no
deploy). Decommission Ansuman's influencer-feed after sustained green (leave his
*video* recsys alone; coordinate with Ansuman).

---

## 11. Pushback summary (my calls — flag any you disagree with)
1. Don't over-invest in backend ms; "fast" is device cache + prefetch (mobile).
2. Gender = cold-start guardrail only, never a ranking axis or permanent quota.
3. **"Always-new" is bounded by catalog size** — sustained freshness needs more
   creators; we'll dashboard the unseen-pool depth. Not a code problem.
4. `owner_active` deferred — non-core, don't delay launch.
5. Bot-type taxonomy kept to 8 clean, overridable values.
6. Diversity targets can't manufacture a catalog — surface gaps as creator-acquisition
   signals, don't force quotas that repeat weak bots.
7. Collaborative job runs on the **replica** — protects the live primary.

---

## 12. Verified vs. assumed
- **Verified (v2 code):** ranking signals derivable; Redis/edge cache; matview
  refresher `main.py:201`; replica on rishi-6 for heavy reads; **`runpod_vllm`
  provider already wired** (`llm_registry.py:170` → `saikat-llm-medium-fast.yral.com`);
  `ASYNC_PROCESSES_NEVER_GEMINI` guardrail exists; embeddings run on Gemini
  (`embeddings.py`) → reason semantic match is deferred.
- **Net-new (confirmed):** `gender`/`bot_type` + classifier (on runpod vLLM);
  `bot_similarity`; lightweight seen-set freshness; (deferred) semantic match,
  `owner_active`, bandit.
- **Verified (Ansuman):** mirrored envelope + dropped video signals —
  [[project_ansuman_recsys_facts]].
- **No prod state touched.** Read-only research only.

---

## 13. What's still needed to start the build (status)
1. **runpod vLLM vision model id** for the classification process — the **one external
   input**. Provider/URL already wired; dev needs the **vision-capable served model id**
   from Saikat (or `/v1/models`) + flips `supports_vision: True` (§4). *Blocks M1 only.*
2. **pg_dump + migration apply:** ✅ **Rishi + Session 6 do this together** (Session 6
   has the experience). Snapshot before M0/M1/M2 migrations.
3. **Caddy edge cache:** ✅ **Dropped for v1** (§2) — unnecessary complexity; app is
   already ~5 ms. Revisit only if traffic ever demands it.
4. **Phase number:** ✅ **Session 6 files the whole plan as a new phase** in
   PROGRESS.md / DAILY-LOG.md.
5. **Semantic-embedding match:** ✅ **Deferred — added to PROGRESS as a later item.**
   Needs Gemini embeddings (excluded) or Saikat serving an embedding model on runpod.
6. **Mobile:** see the **companion spec** `docs/discovery-feed-mobile-spec-2026-06-16.md`
   — detailed enough for Rishi to direct/ship without Sarvesh; flag-gated so nothing
   breaks.

Ready to dispatch (§10).
```
