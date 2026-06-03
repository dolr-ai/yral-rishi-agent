# PR-1179 audio-recording snapshot — 2026-06-03

**PR:** [dolr-ai/yral-mobile#1179](https://github.com/dolr-ai/yral-mobile/pull/1179)
**PR title:** feat(chat): voice-message recording (Phase 1.7b mobile)
**Branch:** `rishi/audio-recording-mic` (commits `032d1ac8` feature + `22cac36e` flag gate)
**Snapshot date:** 2026-06-03 (BEFORE adding Sarvesh as reviewer)
**Snapshot HEAD:** `22cac36e`

## Why this snapshot

Per `mobile-docs-archive/README.md` process: archive all at-risk mobile docs before adding Sarvesh as reviewer on any PR. Sarvesh strips non-source docs on merge; without this backup, future mobile-expert sessions lose access to institutional knowledge (lessons, plans, post-mortems, handoffs) that informs ongoing work.

## Files captured

| File | Source path | Why it matters |
|------|-------------|----------------|
| `MOBILE-EXPERT-LESSONS.md` | `yral-mobile/docs-rishi/` | Discipline patterns (P1-P5) from mobile-expert sessions. Each future session reads this to avoid repeating past mistakes. Note: P6/P7/P8 land via the H2H PR #1178 (`rishi/h2h-chat` branch); they are NOT in this snapshot because they don't exist on `rishi/audio-recording-mic`. PR #1178's own snapshot will capture them. |
| `HANDOFF-CHAT-AS-HUMAN.md` | `yral-mobile/` (root) | Handoff doc for Phase 1.10 (Chat as Human / creator takeover) including the 3-scenario Motorola retest checklist. |
| `PLAN-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/` | Implementation plan for Phase 1.10 (Chat as Human creator takeover). |
| `POST-MORTEM-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/` | Post-mortem for the 3 takeover bugs caught during 2026-05-28 Motorola retest (timer reset on user activity, gap before sweep, "X has left" duplication). |
| `SSE-IMPLEMENTATION-PLAN.md` | `yral-mobile/docs-rishi/` | Implementation plan for Phase 10 (SSE streaming) mobile-side. |
| `SSE-PLANNING-NOTES.md` | `yral-mobile/docs-rishi/` | Planning notes for SSE design, including the 250ms coalescing decision and markdown re-parse fix. |
| `SSE-PHASE5B-PLAN.md` | `yral-mobile/docs-rishi/` | Phase 5b sub-plan for SSE — the `ConversationContentCache` work that caused the iOS Kotlin Native compile failure. |
| `HANDOFF-SSE-STREAMING.md` | `yral-mobile/docs-rishi/` | Handoff doc for SSE streaming feature. |
| `todo.md` | `yral-mobile/` (root) | Session-state / running todo file used by mobile expert. |

## Total files in snapshot: 9

## Relationship to baseline snapshot

This snapshot captures **the same 9 files** as `2026-06-03-baseline-snapshot/` (the process-inception baseline taken earlier on the same date). The audio recording PR `rishi/audio-recording-mic` was branched off `main` and did not modify any docs files in its two commits (`032d1ac8` adds Kotlin source + manifest entry; `22cac36e` adds the flag gate). Content of all 9 files is byte-identical to the baseline copies.

Snapshot is still created per the process — the discipline is that *every* pre-Sarvesh state gets recorded, not just the ones that diverge. This guarantees future readers can locate the exact at-risk-doc state at the moment of any given PR's Sarvesh-add without needing to compute branch-vs-baseline diffs.

**Audio PR #1179 does NOT modify or remove any of these docs.** If Sarvesh strips them on merge to main, only the source code (Kotlin + manifest + strings + feature flag) reaches main from this PR.

## Files NOT backed up (intentional skips)

| File | Source path | Why skipped |
|------|-------------|-------------|
| `README.md` | `yral-mobile/` (root) | Standard repo doc, Sarvesh wouldn't strip |
| `AGENTS.md` | `yral-mobile/` (root) | Agents-related repo doc, expected to be preserved |
| `build-logic/README.md` | (build-logic dir) | Build infrastructure doc, part of source organization |

## PR context (for future readers)

The audio recording PR adds **in-chat voice-message capture**, dormant behind `AudioRecordingEnabled` feature flag (`defaultValue = false`). Mic button → recording bar → preview bar → send pipeline. Backend transcribes via Gemini and the AI replies based on the transcription. Three backend PRs (#254 storage_key resolve, #255 audio fetcher fork, #257 image multimodal) are deployed to agent.rishi.yral.com and prerequisites for end-to-end activation — but not blockers for the PR's merge since the feature stays dormant until the flag is flipped.

**Known follow-up Task #64:** G6 mic-hide gate on H2H chats — once H2H lands on main and this branch rebases, AND `!viewState.isHumanChat` into the gate. Inline comment at `ChatConversationScreen.kt:701-714` and PR description's "Known follow-up" section document this so future-me / reviewer / rebaser sees the intent.
