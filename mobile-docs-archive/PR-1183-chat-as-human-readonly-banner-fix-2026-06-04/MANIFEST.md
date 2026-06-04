# PR-1183 Chat-as-Human read-only banner fix snapshot — 2026-06-04

**PR:** [dolr-ai/yral-mobile#1183](https://github.com/dolr-ai/yral-mobile/pull/1183)
**PR title:** fix(chat): respect ChatAsHumanCreatorEnabled flag in BotAccountReadOnly branch
**Branch:** `rishi/chat-as-human-readonly-banner-fix` (HEAD `80793958`)
**Snapshot date:** 2026-06-04 (BEFORE adding Sarvesh as reviewer)

## Why this snapshot

Per `mobile-docs-archive/README.md` process: archive all at-risk mobile docs before adding Sarvesh as reviewer. Sarvesh strips non-source docs on merge.

This PR is a **single-file, single-concern, +26/-7 line fix** (Task #63) closing the discipline gap surfaced during the H2H + Chat-as-Human merge sequencing: when both feature flags are ON and a bot account opens a chat with a human participant, the `BotAccountReadOnly` branch (which fires for that exact combination) was unconditionally rendering the read-only "AI influencer view mode" banner instead of mirroring the `BotAccountPrompt` branch's flag-gated `CreatorTakeoverBar` render.

The fix is a straight mirror: add the same `if (viewState.isChatAsHumanCreatorEnabled) CreatorTakeoverBar else BotAccountConversationPrompt` shape into `BotAccountReadOnly`. When the flag is off, the read-only banner continues to render unchanged — fully backward-compatible default.

## Branch base

Off main directly, post-H2H-merge. So the branch carries:
- Root `MOBILE-EXPERT-LESSONS.md` (96 lines, P1–P8) — current
- Root `H2H-IMPLEMENTATION-PLAN.md` — added by #1178
- `docs-rishi/*` files (7 docs) — baseline carryover

## Files captured

| File | Source path | Why it matters |
|------|-------------|----------------|
| `root/H2H-IMPLEMENTATION-PLAN.md` | `yral-mobile/H2H-IMPLEMENTATION-PLAN.md` | H2H implementation plan + verification checklist. |
| `root/HANDOFF-CHAT-AS-HUMAN.md` | `yral-mobile/HANDOFF-CHAT-AS-HUMAN.md` | Phase 1.10 handoff doc — the chat-as-human flow this PR fixes a gap in. |
| `root/MOBILE-EXPERT-LESSONS.md` | `yral-mobile/MOBILE-EXPERT-LESSONS.md` | Current lessons P1–P8 including P8 ("read the exception, not just the symptom"). |
| `root/todo.md` | `yral-mobile/todo.md` | Running session-state / todo file. |
| `docs-rishi/HANDOFF-SSE-STREAMING.md` | `yral-mobile/docs-rishi/HANDOFF-SSE-STREAMING.md` | SSE streaming handoff. Baseline. |
| `docs-rishi/MOBILE-EXPERT-LESSONS.md` | `yral-mobile/docs-rishi/MOBILE-EXPERT-LESSONS.md` | Older (50-line P1–P5 era) lessons copy. Carried over. |
| `docs-rishi/PLAN-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/PLAN-CHAT-AS-HUMAN.md` | Chat-as-Human plan. Baseline. |
| `docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | Chat-as-Human postmortem. Baseline. |
| `docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | `yral-mobile/docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | SSE implementation plan. Baseline. |
| `docs-rishi/SSE-PHASE5B-PLAN.md` | `yral-mobile/docs-rishi/SSE-PHASE5B-PLAN.md` | SSE Phase 5b plan. Baseline. |
| `docs-rishi/SSE-PLANNING-NOTES.md` | `yral-mobile/docs-rishi/SSE-PLANNING-NOTES.md` | SSE planning notes. Baseline. |

## Total files in snapshot: 11

## Files NOT backed up (intentional skips)

Same as PR-1178/#1181: `README.md`, `AGENTS.md`, `build-logic/README.md` — standard repo / config files Sarvesh wouldn't strip.

## PR context (for future readers)

Task #63 sat on the pending list since 2026-05-30 when the Chat-as-Human PR landed: the `BotAccountReadOnly` state (bot account in an H2H chat) was added at the same time as H2H but the flag-gated render mirror was never applied. The fix is mechanical — copy 18 lines of the `CreatorTakeoverBar` instantiation block into the sibling branch behind the same `if (viewState.isChatAsHumanCreatorEnabled)` guard.

Rishi-tested on Motorola 2026-06-04 afternoon with overrides `ChatAsHumanCreatorEnabled = true` and `H2hChatEnabled = true` against `agent.rishi.yral.com`. Test 1 (bot account in H2H chat → CreatorTakeoverBar visible) and Test 2 (bot account in AI-influencer chat → CreatorTakeoverBar visible, unchanged) both passed.

## Companion PRs

- **#1178** (`rishi/h2h-chat`) — merged. Introduced the `BotAccountReadOnly` state this PR addresses.
- **#1163** (`rishi/chat-as-human-takeover`) — merged. Introduced the `ChatAsHumanCreatorEnabled` flag + the `CreatorTakeoverBar` component this PR mirrors.

Independent of all other in-flight PRs (#1180/#1181/#1182/#1179). Can merge in any order.
