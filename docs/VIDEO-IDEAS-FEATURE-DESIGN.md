# Daily 5 Video Ideas — Design Doc for Session 6 (Phase 22.3)

**Status:** Design locked by Rishi 2026-06-04. To be added to PROGRESS.md as expanded Phase 22.3 sub-phases. Work starts POST-CUTOVER.
**Author:** Feature Strategy session (2026-06-04), after deep exploration of both repos + Rishi UX decisions.
**Dispatch:** backend sub-phases → developer session; mobile sub-phases → mobile expert.

---

## What this is

Every AI Influencer's owner gets **5 fresh AI-generated video ideas daily**, shown in a **third tab** on the bot's profile — next to the existing Published (4-squares icon) and Drafts (folder icon) tabs. Each idea row has a **Create** button that fires the *existing* AI video generation pipeline (the same one behind the + button) with our idea text as the prompt. **Only one generation at a time:** while a video is generating, every Create button locks until the video lands in Drafts.

## Decisions locked (Rishi, 2026-06-04)

1. **No trending topics in v1** — ideas generated from archetype + Soul File + bot's recent conversations (all in the agent DB). Trending needs a video-service API that doesn't exist in the monolith; revisit post-v1.
2. **Active bots only** — nightly generation for bots with ≥1 message in last 7 days; cold-start on-demand generation on first GET for new bots.
3. **User-profile version deferred** — users have no archetype/Soul File; gate on Create-tap metrics from the bot version (~2 weeks of signal) before investing.
4. **UX: third profile tab** (not a section above the grid), vertical list of 5, Create button per row, one-at-a-time lock.
5. **Create = headless generation** — no prompt screen; the idea text goes straight into the existing generate API.

## Research findings the design relies on (verified 2026-06-04)

| Capability | Already exists? | Where |
|---|---|---|
| Tab bar with 2 tabs, own-profile only | ✅ | `ProfileTab` enum + `ProfileTabBar` in `shared/features/profile/.../ProfileMainScreen.kt` — adding a 3rd tab is small, Row auto-spaces |
| Video gen API taking a plain prompt string | ✅ | `POST offchain.yral.com/api/v2/videogen/generate` via `GenerateVideoUseCase` (`shared/features/uploadvideo/`), `upload_handling: "ServerDraft"` |
| In-flight progress tile in Drafts grid | ✅ | `VideoGeneratingCard` + 30s polling via `VideoDraftPollingManager` (`/api/v2/videogen/drafts/in-progress`) |
| Completion push notification | ✅ | `VideoUploadedToDraft` FCM push → toast with "View Drafts"; permission ask is app-level, nothing to build |
| Global "is anything generating" state | ✅ | `VideoGenerationTracker` singleton in `shared/core/.../videostate/` — `isGenerating` gives the one-at-a-time lock for free |
| Nightly background loop pattern | ✅ | `quality_scorer.scoring_loop` + `kill_switch.is_enabled` in agent monolith — clone exactly |
| LLM routing + dashboard knob | ✅ | `llm_registry.PROCESS_NAMES` — 25.9 dashboard auto-discovers new processes |

## Backend spec (yral-rishi-agent, 2 PRs)

### 22.3a — Migration `032_video_ideas.sql` (pg_dump snapshot first per Rule 9)
```sql
CREATE TABLE video_ideas (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    influencer_id TEXT NOT NULL REFERENCES ai_influencers(id) ON DELETE CASCADE,
    batch_date    DATE NOT NULL,
    rank          SMALLINT NOT NULL,              -- 1..5
    hook          TEXT NOT NULL,                  -- short punchy title shown in the row
    idea_text     TEXT NOT NULL,                  -- fuller description; doubles as the video-gen prompt
    status        TEXT NOT NULL DEFAULT 'fresh',  -- fresh | used
    used_at       TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (influencer_id, batch_date, rank)      -- guards double-generation per day
);
CREATE INDEX idx_video_ideas_influencer_recent ON video_ideas (influencer_id, created_at DESC);
```

### 22.3b — Feature PR (~350 lines; above the 100-line rule — Rishi sign-off given via this design doc)
1. `app/services/video_ideas.py` — clone of `quality_scorer.py`:
   - `generate_for_one_bot(pool, bot)` → `llm_registry.call(process="video_idea_generation", ...)`; prompt grounded in archetype + system_instructions + last ~30 conversation messages; returns JSON array of 5 `{hook, idea_text}`; parse with the tolerant `_extract_json` pattern from `services/wizard.py`
   - `generate_all_once(pool)` → active bots (≥1 message last 7 days), skip bots already holding today's batch_date
   - `video_ideas_loop()` → INITIAL_DELAY 20 min, INTERVAL 24h, gated by `is_enabled("video_ideas")`, same try/except/log shape as `scoring_loop()`
2. `app/kill_switch.py` — add `"video_ideas": "ENABLE_VIDEO_IDEAS_LOOP"` to `_PER_LOOP_KEYS`
3. `app/services/llm_registry.py` — add `"video_idea_generation"` to `PROCESS_NAMES` + `LLM_DEFAULTS` (cheap background provider). Dashboard knob appears automatically.
4. `app/repositories/video_idea_repo.py` — `insert_batch` / `latest_batch_for_bot` / `mark_used` (same style as `quality_score_repo.py`)
5. `app/routes/influencers.py` — two endpoints, ownership check inline via `parent_principal_id` (same as `update_system_prompt`):
   - `GET /api/v1/influencers/{id}/video-ideas` — owner-only, today's 5; cold-start: no batch → generate on-demand once, then return
   - `POST /api/v1/influencers/{id}/video-ideas/{idea_id}/used` — owner-only; mobile calls it when generation fires. **This is the Create-tap metric gating the future user-profile version.**
6. `app/main.py` — register `asyncio.create_task(video_ideas_loop())` alongside the other loops

## Mobile spec (yral-mobile)

### 22.3c — Third "Ideas" tab + list UI
- `ProfileViewModel.kt` — add `Ideas` to `enum class ProfileTab` (selectTab() already generic)
- `ProfileTabBar` in `ProfileMainScreen.kt` — third `ProfileTabItem` with new `ic_ideas_selected/unselected.xml` drawables (lightbulb)
- **Visibility:** tab bar already gated on `isOwnProfile`; show the Ideas tab only when `isAiInfluencer` too → bot profiles get 3 tabs, human own profile keeps 2
- Content branch `selectedTab == ProfileTab.Ideas` → `IdeasListContent`: header "✨ Today's ideas — fresh 5 every day"; 5 rows of rank + hook (bold) + idea_text (2 lines ellipsized) + Create button
- Row states: `fresh` → Create enabled · `used` → inert "✓ Created" chip · **any generation in flight → ALL Create buttons disabled** (collect `VideoGenerationTracker.state.isGenerating`)
- Data layer: `getVideoIdeas(botId)` + `markIdeaUsed(botId, ideaId)` following the `CoachRemoteDataSource` pattern (agent backend base URL + Bearer token)

### 22.3d — One-tap Create (headless generation)
- On tap: guard `!VideoGenerationTracker.state.isGenerating` → `startGenerating()` → `GenerateVideoUseCase(GenerateVideoParams(prompt = idea.ideaText, providerId = default provider, tokenType = FREE, userId = sessionManager.userPrincipal, uploadHandling = "ServerDraft"))`
- Success: `videoDraftPollingManager.onGenerationSubmitted(userId)` + `markIdeaUsed` + toast "Creating video — check Drafts"; row flips to ✓; Drafts tab shows the existing progress tile; polling + push completion all unchanged
- Failure (429 / credits exhausted — server enforces): clear tracker, toast error, buttons re-enable. The + flow's client-side credit UI is NOT duplicated in v1.
- Default provider: same providers use case `AiVideoGenViewModel` uses; take its default
- DI: `uploadVideoModule` already exposes the use case + polling manager via Koin

### ⚠️ Verify on device BEFORE building 22.3d
1. **Whose drafts does the video land in** when generating from the bot's profile — bot account or human account? (`user_id = sessionManager.userPrincipal` depends on account-switcher state.) One Motorola test settles it.
2. Confirm the `VideoUploadedToDraft` push fires on the test device.

## PROGRESS.md rows (for Session 6 to insert, replacing the current 22.3 row)

| # | Sub-phase | Owner | Status | Est. days |
|---|-----------|-------|--------|-----------|
| 22.3a | Migration 032 `video_ideas` table (pg_dump first per Rule 9) | Developer session | ⏳ Post-cutover | 0.5 |
| 22.3b | Nightly loop + repo + 2 endpoints + `video_idea_generation` registry process + kill-switch | Developer session | ⏳ Post-cutover | 1.5 |
| 22.3c | Mobile: third "Ideas" profile tab + 5-idea list UI + data source | Mobile expert | ⏳ Post-cutover | 2 |
| 22.3d | Mobile: one-tap Create → headless generation + global one-at-a-time lock (verify drafts-account question on device first) | Mobile expert | ⏳ Post-cutover | 1 |
| 22.3e | Motorola end-to-end verification (see below) | Rishi + mobile expert | ⏳ Post-cutover | 0.5 |

Sequencing: 22.3a → 22.3b (backend, can run parallel to 22.3c) → 22.3d → 22.3e. Starts only after Phase 21 cutover + Coach v1.1 ship.

## Verification (22.3e)

- **Backend (curl):** GET for a fresh bot → cold-start generates 5; repeat GET → same batch; non-owner JWT → 403; POST /used → status flips; `ENABLE_VIDEO_IDEAS_LOOP=false` → loop skips; dashboard shows the `video_idea_generation` knob.
- **Motorola:** bot profile shows 3rd tab with 5 ideas; tap Create → toast → Drafts shows progress tile → all other Create buttons greyed → draft lands → buttons re-enable + idea shows ✓; second Create during flight → blocked; human own profile still shows 2 tabs; next day → 5 new ideas.

## Future (explicitly out of v1)
- Trending topics input (needs video-service API)
- User-profile ideas (gated on Create-tap metrics from this version)
- Client-side credit UI in the Ideas tab (server enforcement only in v1)
- Idea dismissal / regenerate-one
