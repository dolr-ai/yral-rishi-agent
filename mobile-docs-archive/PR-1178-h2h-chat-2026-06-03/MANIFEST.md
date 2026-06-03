# PR-1178 H2H-chat snapshot — 2026-06-03

**PR:** [dolr-ai/yral-mobile#1178](https://github.com/dolr-ai/yral-mobile/pull/1178)
**PR title:** feat(chat): H2H (Human-to-Human) chat — dormant behind feature flag
**Branch:** `rishi/h2h-chat` (HEAD `d3efb415` docs(lessons): P8)
**Snapshot date:** 2026-06-03 (BEFORE adding Sarvesh as reviewer)

## Why this snapshot

Per `mobile-docs-archive/README.md` process: archive all at-risk mobile docs before adding Sarvesh as reviewer on any PR. Sarvesh strips non-source docs on merge; without this backup, future mobile-expert sessions lose access to institutional knowledge (lessons, plans, post-mortems, handoffs) that informs ongoing work.

## Files captured

| File | Source path | Size | Why it matters |
|------|-------------|------|----------------|
| `H2H-IMPLEMENTATION-PLAN.md` | `yral-mobile/` (root) | 21,885 B | **NEW on this branch.** Implementation plan for the Human-to-Human chat feature. Contains the 6-step verification checklist Rishi has been driving against the Motorola, the §H3 test case that caught the inbox-visibility bug (P6 lesson), the §4.1 anticipatory note about the combined inbox endpoint that was wrong and prompted the dev session hand-off (#58), and the planned §6.2 mic-button hide gate (Task #64, deferred to post-merge rebase commit). |
| `HANDOFF-CHAT-AS-HUMAN.md` | `yral-mobile/` (root) | 28,539 B | Pre-existing handoff doc for Phase 1.10 (Chat as Human / creator takeover) including the 3-scenario Motorola retest checklist. Carried over from main without modification. |
| `MOBILE-EXPERT-LESSONS.md` | `yral-mobile/` (root) | 17,809 B | **NEW on this branch.** Discipline patterns P1-P8 from mobile-expert sessions. P1-P5 were already in the baseline snapshot under `docs-rishi/`; this branch adds them to the root location and appends three new lessons that originated in the H2H build: **P6** ("the wire is wired ≠ the wire carries data" — the H2H inbox-visibility miss from 2026-05-30), **P7** ("permissive deserialization defaults are load-bearing during backend evolution" — the `ignoreUnknownKeys=true` observation post-PR-#228), **P8** ("read the exception, not just the symptom — a broad `except Exception` is a structural amplifier of opaque failure" — captured during the audio transcription debug cycle 2026-06-03, but applicable across the codebase). |
| `todo.md` | `yral-mobile/` (root) | 1,786 B | Pre-existing session-state / running todo file. Carried over from main without modification. |

## Total files in snapshot: 4

## Divergence from baseline snapshot

The baseline snapshot (`2026-06-03-baseline-snapshot/`) captured 9 files, all from `yral-mobile/docs-rishi/*` plus 2 from root. **This snapshot has only 4 files** — a meaningful difference:

| What changed | Reason |
|--------------|--------|
| `docs-rishi/` directory does not exist on `rishi/h2h-chat`. The 7 baseline files from there (`HANDOFF-SSE-STREAMING.md`, `PLAN-CHAT-AS-HUMAN.md`, `POST-MORTEM-CHAT-AS-HUMAN.md`, the three SSE planning docs, and `MOBILE-EXPERT-LESSONS.md` at its old location) are absent. | Those files were untracked when the baseline was taken and existed only in working tree state. Subsequent operations (the SSE PR #1173 merge to main, or routine git operations) cleaned the untracked `docs-rishi/` directory. Their content lives in the baseline snapshot; they are not at risk from THIS PR's merge because they don't exist on this branch. |
| `H2H-IMPLEMENTATION-PLAN.md` NEW at root | Added by the H2H feature work. Tracked in git. |
| `MOBILE-EXPERT-LESSONS.md` NEW at root (P1-P8) | The H2H build chose to track lessons at root rather than under `docs-rishi/`. Includes the three new lessons added during this cycle (P6/P7/P8). |
| `HANDOFF-CHAT-AS-HUMAN.md` | Unchanged from baseline. |
| `todo.md` | Unchanged from baseline. |

If a future session needs the baseline's `docs-rishi/` content (SSE plans, Chat-as-Human plan/postmortem, original lessons), refer to `2026-06-03-baseline-snapshot/`. This snapshot covers only what's at risk on PR #1178 specifically.

## Files NOT backed up (intentional skips)

| File | Source path | Why skipped |
|------|-------------|-------------|
| `README.md` | `yral-mobile/` (root) | Standard repo doc, Sarvesh wouldn't strip |
| `AGENTS.md` | `yral-mobile/` (root) | Agents-related repo doc, expected to be preserved |
| `build-logic/README.md` | (build-logic dir) | Build infrastructure doc, part of source organization |

## PR context (for future readers)

The H2H PR adds **Human-to-Human direct messaging** to the chat surface — peer-to-peer 1:1 chat alongside the existing AI influencer chats, accessed via a new **Send Message** button on other users' profiles. The feature ships **dormant behind `H2hChatEnabled` (defaultValue = `false`)** and only activates after (a) backend cutover from `chat-ai.rishi.yral.com` to `agent.rishi.yral.com` and (b) Firebase Remote Config flip. Same gate pattern as `SseStreamingEnabled`, `ChatAsHumanCreatorEnabled`, and (separately) `AudioRecordingEnabled` (PR #1179).

Branch is 11 commits ahead of `main`: 8 feature/scaffold commits, 1 fix-bundle from the Motorola test pass ("five fixes for H2H end-to-end correctness"), and 3 docs commits (P6, P7, P8 lessons + H2H plan).

**Companion backend PRs** (already deployed to `agent.rishi.yral.com`, not blockers for this PR's merge since the feature is dormant):
- `#228` — unified inbox returns interleaved AI + H2H rows (pre-existing).
- `#241` — `_can_access_conversation` allows `participant_b_id` for H2H recipients.
- `#245` — H2H `unread_count` subquery branches on `conversation_type`.
- `#247` — `count_unread` + `mark_as_read` viewer-principal-scoped (Part B sweep finding).
- `#257` — image multimodal fix (orthogonal, surfaced during the same combined-features test).

**Known follow-up Task #64**: G6 mic-hide on H2H chats. The audio PR #1179 already has the `onMicClick` gate at `ChatConversationScreen.kt:701-714` with an inline comment marking the spot. Whichever of #1178 or #1179 lands second on main needs the 5-character `&& !viewState.isHumanChat` addition to that gate to close the loop. No new PR — just one commit on the rebase-second branch.
