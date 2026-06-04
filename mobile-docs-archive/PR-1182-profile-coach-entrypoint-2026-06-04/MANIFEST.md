# PR-1182 Profile-Coach-Entrypoint snapshot — 2026-06-04

**PR:** [dolr-ai/yral-mobile#1182](https://github.com/dolr-ai/yral-mobile/pull/1182)
**PR title:** feat(profile): wire "Make your AI Influencer better" entry point (stacked on #1180)
**Branch:** `rishi/profile-coach-entrypoint` (HEAD `38471d96` feat(profile): gate Coach CTA on SoulFileCoachEnabled flag + Beta label; on top of `efd22280` feat(profile): wire entry point)
**Base branch:** `rishi/soul-file-coach` at HEAD `63b88685` (PR #1180 has been extended with the UX overhaul commit since this PR was opened; #1182's own HEAD remained at `efd22280` — GitHub recomputes the PR diff against the base's new tip without rebasing)
**Snapshot date:** 2026-06-04 (BEFORE adding Sarvesh as reviewer)

## Why this snapshot

Per `mobile-docs-archive/README.md` process: archive all at-risk mobile docs before adding Sarvesh as reviewer. Sarvesh strips non-source docs on merge.

Because this PR is **stacked on #1180**, its branch tip docs are identical to #1180's. The snapshot is captured separately so each PR has its own MANIFEST per archive convention.

The combined PR #1180 + #1182 + #1181 state was tested on Rishi's Motorola on 2026-06-04 afternoon (Tests A–G of the Coach UX overhaul, all passed). The entry-point button is what makes the Coach reachable in the test APK; without this PR, the Coach feature merged in #1180 would be invisible to users even after backend cutover.

After the morning Feature Strategy review, this PR was extended with commit `38471d96` to add (a) feature-flag gate on `ChatFeatureFlags.Chat.SoulFileCoachEnabled` (matching the pattern of H2H/Audio/SSE/Chat-as-Human PRs), and (b) "(Beta)" suffix on the button label. Rishi-tested + passed both gating behavior (button hidden when flag off, visible when on) and the label change on Motorola 2026-06-04 afternoon.

## Branch base

Inherits from `rishi/soul-file-coach` which was branched BEFORE PR #1178 (H2H) merged. Therefore this branch does NOT carry the root-level `MOBILE-EXPERT-LESSONS.md` (P1–P8) or `H2H-IMPLEMENTATION-PLAN.md` introduced on #1178. After #1180 merges and this PR rebases against main, those files will land via the rebase (not at risk from this snapshot).

## Files captured

| File | Source path | Why it matters |
|------|-------------|----------------|
| `root/HANDOFF-CHAT-AS-HUMAN.md` | `yral-mobile/HANDOFF-CHAT-AS-HUMAN.md` | Phase 1.10 handoff doc. Pre-existing. |
| `root/todo.md` | `yral-mobile/todo.md` | Running session-state / todo file. |
| `docs-rishi/HANDOFF-SSE-STREAMING.md` | `yral-mobile/docs-rishi/HANDOFF-SSE-STREAMING.md` | SSE streaming handoff. Baseline. |
| `docs-rishi/MOBILE-EXPERT-LESSONS.md` | `yral-mobile/docs-rishi/MOBILE-EXPERT-LESSONS.md` | **Older** copy of lessons (P1–P5 era, 50 lines). The root P1–P8 version is not on this branch. |
| `docs-rishi/PLAN-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/PLAN-CHAT-AS-HUMAN.md` | Chat-as-Human plan. Baseline. |
| `docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | Chat-as-Human postmortem. Baseline. |
| `docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | `yral-mobile/docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | SSE implementation plan. Baseline. |
| `docs-rishi/SSE-PHASE5B-PLAN.md` | `yral-mobile/docs-rishi/SSE-PHASE5B-PLAN.md` | SSE Phase 5b plan. Baseline. |
| `docs-rishi/SSE-PLANNING-NOTES.md` | `yral-mobile/docs-rishi/SSE-PLANNING-NOTES.md` | SSE planning notes. Baseline. |

## Total files in snapshot: 9

## Files NOT backed up (intentional skips)

Same as PR-1180/#1181: `README.md`, `AGENTS.md`, `build-logic/README.md`.

## PR context (for future readers)

This PR makes the Coach feature **reachable from the UI**. PR #1180 shipped the coach screen + viewmodel + nav module, but nothing in the profile screen pushed `Config.Coach` onto the stack — the screen was reachable only via dev-test code paths.

This PR adds the missing wiring:
1. `RootComponent.openCoach` + the `Child.Coach` nav route — already in #1180.
2. Threads `openCoach` through `HomeComponent → ProfileComponent → ProfileMainComponent` (mirrors the existing `openConversation` shape).
3. Adds `:shared:features:coach` as a dependency of the profile module so `OpenCoachParams` is accessible.
4. Renders a "Make your AI Influencer better" button on `ProfileMainScreen` below `AccountInfoView`, gated on `state.isOwnProfile && state.isAiInfluencer`.

The button fires `component.openCoach(OpenCoachParams(botId, botName, avatarUrl))` using `accountInfo.userPrincipal` as the botId. No feature-flag gating in v1 — the profile-identity check is sufficient for Phase 7.5.

## Out of scope (deferred to v1.2 Coach UX overhaul)

- Resuming the most-recent coach session (currently always fresh per tap — backend side will introduce a `fresh=true` query param later)
- Coach speaks first / suggestion chips
- Persistent "Save changes to {bot}" button above input
- Streaming coach replies, before/after diff view, deep links

These land in a separate mobile PR after the backend Coach UX PRs deploy to `agent.rishi.yral.com`.

## Companion PRs

- **#1180** (`rishi/soul-file-coach`) — base of this PR. Must merge first.
- **#1181** (`rishi/auth-bot-count-fix`) — independent, parallel. No coupling.
