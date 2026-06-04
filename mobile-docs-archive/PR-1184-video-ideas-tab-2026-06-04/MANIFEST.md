# PR-1184 Video Ideas tab (22.3c + 22.3d) snapshot — 2026-06-04

**PR:** [dolr-ai/yral-mobile#1184](https://github.com/dolr-ai/yral-mobile/pull/1184)
**PR title:** feat(profile): Phase 22.3c — Video Ideas tab + list UI + data layer (+ 22.3d Create handler)
**Branch:** `rishi/video-ideas-tab` (HEAD `249fab27` feat(profile): gate Video Ideas tab on VideoIdeasEnabled flag; on top of `c9d0825b` 22.3d Create handler + `b09e2a2c` 22.3c tab + data layer)
**Snapshot date:** 2026-06-04 (BEFORE adding Sarvesh as reviewer)

## Why this snapshot

Per `mobile-docs-archive/README.md` process: archive all at-risk mobile docs before adding Sarvesh as reviewer. Sarvesh strips non-source docs on merge.

PR #1184 ships **three commits bundled** at Rishi's explicit decision: 22.3c (third "Ideas" profile tab + 5-idea list UI + data layer mirroring CoachRemoteDataSource pattern), 22.3d (one-tap Create handler firing the existing headless video-gen pipeline + Option C "stay-on-Ideas + tappable View-in-Drafts toast"), and a follow-up flag-gate commit added after the same-day Sarvesh-add gap was caught.

All 5 Rishi-tested cases for each sub-phase passed on Motorola 2026-06-04 (10 tests total — 5 in 22.3c, 5 in 22.3d). The flag-gate commit added a 6-test retest (2 flag-ON cases + 3 flag-OFF cases + 1 implicit no-network-call check) — all 6 passed before this MANIFEST was finalised.

## Discipline gap caught + future rule

The original Phase 22.3 design doc explicitly said "no feature flag for this PR." That was wrong — Phase 22.3's data layer hits `GET/POST /api/v1/influencers/{id}/video-ideas` on `agent.rishi.yral.com`, endpoints that do not exist on the production `chat-ai` backend. Without a flag gate, pre-cutover users tapping the Ideas tab would 404.

Session 6 is saving a discipline rule going forward: "All mobile features that touch agent.rishi.yral.com endpoints (i.e., depend on the v2 backend) MUST be feature-flag-gated with defaultValue=false until cutover. Design docs that say 'no flag needed' should be challenged against this rule." Future PRs will not have this gap.

The flag-gate commit (`249fab27`) added `ChatFeatureFlags.Chat.VideoIdeasEnabled` next to `H2hChatEnabled`, plumbs it through ProfileViewModel state, and gates the third tab visibility on `isOwnProfile && isAiInfluencer && isVideoIdeasEnabled`. When the flag is off (origin / production until cutover), the bot profile shows the legacy 2-tab UX (Published + Drafts) — no lightbulb tab, no calls to the missing endpoints.

Backend dependency for 22.3c data layer: agent.rishi.yral.com PRs #274 (coach_messages.suggestions, ground for VideoIdea/Coach data-pattern parallel) + #279 (Video Ideas migration + nightly loop + 2 endpoints) + #280 (kill-switch + dashboard knob). Backend `video_idea_generation` LLM process was flipped from `internal_vllm` to `gemini`/`gemini-2.5-flash` during 22.3c testing after the cold-start path silently failed on internal_vllm; dev session also shipped PR #284 raising `max_tokens=1024→4096` to handle Devanagari (3-byte UTF-8) response sizes that were truncating mid-string. Both fixes deployed before 22.3d testing.

Backend dependency for 22.3d Create handler: the existing video-gen pipeline (POST `offchain.yral.com/api/v2/videogen/generate`) is unchanged; this PR wires `GenerateVideoUseCase` and `GetProvidersUseCase` from the `uploadvideo` module (public-visibility flip from `internal` — same shape as the already-public `PublishDraftVideoUseCase` this module was using).

## Branch base

Off post-H2H-merge main. Same surface as PR-1183 / PR-1181 / PR-1179 (post-rebase) snapshots:
- Root `MOBILE-EXPERT-LESSONS.md` (96 lines, P1–P9 by now), root `H2H-IMPLEMENTATION-PLAN.md`, root `HANDOFF-CHAT-AS-HUMAN.md`, root `todo.md`
- `docs-rishi/*` baseline (7 files)

## Files captured

Subdir layout (`root/` vs `docs-rishi/`) — both locations contain `MOBILE-EXPERT-LESSONS.md` with different content (96 lines vs 50 lines).

| File | Source path | Why it matters |
|------|-------------|----------------|
| `root/H2H-IMPLEMENTATION-PLAN.md` | `yral-mobile/H2H-IMPLEMENTATION-PLAN.md` | H2H plan + verification checklist. |
| `root/HANDOFF-CHAT-AS-HUMAN.md` | `yral-mobile/HANDOFF-CHAT-AS-HUMAN.md` | Phase 1.10 handoff. |
| `root/MOBILE-EXPERT-LESSONS.md` | `yral-mobile/MOBILE-EXPERT-LESSONS.md` | Current lessons P1–P8 (P9 may be added in a docs-only PR later). |
| `root/todo.md` | `yral-mobile/todo.md` | Running todo. |
| `docs-rishi/HANDOFF-SSE-STREAMING.md` | `yral-mobile/docs-rishi/HANDOFF-SSE-STREAMING.md` | SSE handoff. Baseline. |
| `docs-rishi/MOBILE-EXPERT-LESSONS.md` | `yral-mobile/docs-rishi/MOBILE-EXPERT-LESSONS.md` | Older 50-line lessons. |
| `docs-rishi/PLAN-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/PLAN-CHAT-AS-HUMAN.md` | Chat-as-Human plan. Baseline. |
| `docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | Chat-as-Human postmortem. Baseline. |
| `docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | `yral-mobile/docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | SSE plan. Baseline. |
| `docs-rishi/SSE-PHASE5B-PLAN.md` | `yral-mobile/docs-rishi/SSE-PHASE5B-PLAN.md` | SSE Phase 5b plan. Baseline. |
| `docs-rishi/SSE-PLANNING-NOTES.md` | `yral-mobile/docs-rishi/SSE-PLANNING-NOTES.md` | SSE notes. Baseline. |

## Total files in snapshot: 11

## Files NOT backed up (intentional skips)

Same as PR-1178/PR-1179/PR-1180/PR-1181/PR-1182/PR-1183: `README.md`, `AGENTS.md`, `build-logic/README.md`.

## PR context (for future readers)

22.3c adds the third "Ideas" tab to a creator's own AI-influencer profile, gated on `isOwnProfile && isAiInfluencer`. The data layer mirrors the Coach module pattern. Visibility decision: human-only profiles keep two tabs (Published/Drafts); AI-influencer profiles get three. 22.3d wires the Create button to `GenerateVideoUseCase` headlessly — same provider list + first-as-default approach `AiVideoGenViewModel` uses. `userId = sessionManager.userPrincipal` matches the Device Check 1 result from 2026-06-04 (videos land in whichever account fired the gen).

Option C UX (per the morning brief): on Create tap, the creator stays on the Ideas tab. Success toast surfaces with a tappable "View in Drafts" action; tap fires `VideoGenerationTracker.requestDraftsTab(userId)` which the screen's LaunchedEffect picks up and switches `selectedTab` to Drafts when the request targets the currently-rendered profile.

Global one-at-a-time lock: `VideoGenerationTracker.state.isGenerating` is collected at the SuccessContent level; when true, ALL Create buttons disable + a grey hint line appears above the list.

## Backend bug surfaced during 22.3c testing (resolved before 22.3d testing)

The 22.3c cold-start path uncovered a `max_tokens=1024` truncation bug for Devanagari-heavy bots (Hindi `system_instructions`, 3-byte UTF-8 characters). Symptom: backend returned `ideas: []` after ~6-second LLM round-trip; mobile correctly rendered the empty state. Dev session shipped PR #284 (`max_tokens 1024 → 4096` + truncation-tolerant parser) which resolved it. 22.3d testing happened against the fixed backend.

## Companion PRs

- **#1178 H2H** — merged. Provides post-merge main base.
- **#1180 / #1182 Coach** — pending Sarvesh review. The Coach pattern is the inspiration for the `VideoIdeasDataSource` shape.
- **#1181 auth fix** — pending Sarvesh review. Independent.
- **#1183 BotAccountReadOnly fix** — pending Sarvesh review. Independent.
- **#1179 audio (post-rebase + G6)** — pending Sarvesh review. Independent.
