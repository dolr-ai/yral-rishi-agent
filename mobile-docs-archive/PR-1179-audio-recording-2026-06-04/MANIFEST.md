# PR-1179 Audio recording — post-rebase + G6 snapshot — 2026-06-04

**PR:** [dolr-ai/yral-mobile#1179](https://github.com/dolr-ai/yral-mobile/pull/1179)
**PR title:** feat(chat): voice-message recording (Phase 1.7b mobile)
**Branch:** `rishi/audio-recording-mic` (HEAD `e79ae60d` fix(chat): G6 — hide mic button in H2H chats)
**Snapshot date:** 2026-06-04 (post-rebase on main + G6 commit added)

## Why this snapshot supersedes the 2026-06-03 one

The original snapshot `PR-1179-audio-recording-2026-06-03/` (9 files) was taken when the audio branch was based on pre-H2H-merge main. On 2026-06-04 the branch was rebased onto current main — which now carries PR #1178's `H2H-IMPLEMENTATION-PLAN.md` and the post-#1178 root-level `MOBILE-EXPERT-LESSONS.md` (96 lines, P1–P8). A new G6 commit was added on top (`fix(chat): G6 — hide mic button in H2H chats`, closes Task #64).

The at-risk doc surface therefore grew from 9 files to 11. This snapshot reflects the **current PR reviewer state**; the 2026-06-03 snapshot remains in the archive for historical accuracy.

## Branch base

Off post-H2H-merge main. Carries:
- Root `MOBILE-EXPERT-LESSONS.md` (96 lines, P1–P8) — current
- Root `H2H-IMPLEMENTATION-PLAN.md` — added by #1178
- Root `HANDOFF-CHAT-AS-HUMAN.md` + `todo.md` — pre-existing
- `docs-rishi/MOBILE-EXPERT-LESSONS.md` (50 lines, P1–P5 era) — older copy not yet cleaned up
- 6 other `docs-rishi/*` baseline files

## Files captured

Subdir layout (`root/` vs `docs-rishi/`) preserves origin since both locations contain `MOBILE-EXPERT-LESSONS.md` with **different content** (96 vs 50 lines).

| File | Source path | Why it matters |
|------|-------------|----------------|
| `root/H2H-IMPLEMENTATION-PLAN.md` | `yral-mobile/H2H-IMPLEMENTATION-PLAN.md` | H2H plan + verification checklist. |
| `root/HANDOFF-CHAT-AS-HUMAN.md` | `yral-mobile/HANDOFF-CHAT-AS-HUMAN.md` | Phase 1.10 handoff. |
| `root/MOBILE-EXPERT-LESSONS.md` | `yral-mobile/MOBILE-EXPERT-LESSONS.md` | Current lessons P1–P8. |
| `root/todo.md` | `yral-mobile/todo.md` | Running todo file. |
| `docs-rishi/HANDOFF-SSE-STREAMING.md` | `yral-mobile/docs-rishi/HANDOFF-SSE-STREAMING.md` | SSE handoff. |
| `docs-rishi/MOBILE-EXPERT-LESSONS.md` | `yral-mobile/docs-rishi/MOBILE-EXPERT-LESSONS.md` | Older P1–P5 lessons. |
| `docs-rishi/PLAN-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/PLAN-CHAT-AS-HUMAN.md` | Chat-as-Human plan. |
| `docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | Chat-as-Human postmortem. |
| `docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | `yral-mobile/docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | SSE plan. |
| `docs-rishi/SSE-PHASE5B-PLAN.md` | `yral-mobile/docs-rishi/SSE-PHASE5B-PLAN.md` | SSE Phase 5b plan. |
| `docs-rishi/SSE-PLANNING-NOTES.md` | `yral-mobile/docs-rishi/SSE-PLANNING-NOTES.md` | SSE notes. |

## Total files in snapshot: 11

## Files NOT backed up (intentional skips)

Same as PR-1178/#1181/#1183: `README.md`, `AGENTS.md`, `build-logic/README.md`.

## PR context (for future readers)

The audio PR opened 2026-06-02 and was rebased on 2026-06-04 to pick up the H2H merge plus add the G6 mic-hide gate (`&& !viewState.isHumanChat` appended to the existing `AudioRecordingEnabled` gate in `ChatConversationScreen.kt:717`). Voice messages aren't supported on H2H peer chats — the send route doesn't transcribe — so the mic icon now hides entirely when `viewState.isHumanChat` is true. Closes Task #64.

Pre-flight check before force-pushing: confirmed via `gh pr view 1179 --json reviews,comments,reviewRequests` that Sarvesh had zero review state (only the automated `github-advanced-security` bot had touched the PR; the human reviewer was still in the queue). Per the morning brief's Option 1 gate, rebase + force-push proceeded safely.

Rishi-test pending on Motorola (combined APK with `AudioRecordingEnabled=true` + `H2hChatEnabled=true` overrides + `CHAT_BASE_URL=agent`).

## Companion PRs

- **#1178** — merged. Provides `isHumanChat` on viewState which the G6 gate depends on.
- All other in-flight PRs (#1180/#1181/#1182/#1183) — independent.
