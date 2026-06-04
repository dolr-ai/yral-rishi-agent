# PR-1181 Auth-bot-count-fix snapshot — 2026-06-04

**PR:** [dolr-ai/yral-mobile#1181](https://github.com/dolr-ai/yral-mobile/pull/1181)
**PR title:** fix(auth): stop overcounting bots from stale OAuth token claim
**Branch:** `rishi/auth-bot-count-fix` (HEAD `10c133c7` fix(auth): stop overcounting bots from stale OAuth token claim)
**Snapshot date:** 2026-06-04 (BEFORE adding Sarvesh as reviewer)

## Why this snapshot

Per `mobile-docs-archive/README.md` process: archive all at-risk mobile docs before adding Sarvesh as reviewer. Sarvesh strips non-source docs on merge.

This PR is a **single-concern, single-file, +9/-7 line defensive fix**. It does not add or delete any docs. The snapshot captures the docs that exist on this branch's tip (which equals main's docs state since the branch is one commit ahead of main with no doc touches).

## Branch base

Branched off main AFTER PR #1178 (H2H) merged, so the branch has:
- The new root-level `MOBILE-EXPERT-LESSONS.md` (96 lines, P1–P8) added by #1178
- The new root-level `H2H-IMPLEMENTATION-PLAN.md` added by #1178
- The original `docs-rishi/MOBILE-EXPERT-LESSONS.md` (50 lines, P1–P5 era) still in place — neither #1178 nor this PR removed it
- All the docs-rishi/* SSE / Chat-as-Human planning artifacts from baseline

## Files captured

Subdirectory layout (`root/` vs `docs-rishi/`) preserves origin because both locations contain a file named `MOBILE-EXPERT-LESSONS.md` with **different content**:
- `root/MOBILE-EXPERT-LESSONS.md` (96 lines) — current, includes P6/P7/P8
- `docs-rishi/MOBILE-EXPERT-LESSONS.md` (50 lines) — older, P1–P5 era

| File | Source path | Why it matters |
|------|-------------|----------------|
| `root/H2H-IMPLEMENTATION-PLAN.md` | `yral-mobile/H2H-IMPLEMENTATION-PLAN.md` | H2H feature implementation plan + verification checklist. Added on #1178, now lives on main and on this branch. |
| `root/HANDOFF-CHAT-AS-HUMAN.md` | `yral-mobile/HANDOFF-CHAT-AS-HUMAN.md` | Phase 1.10 handoff doc. Pre-existing. |
| `root/MOBILE-EXPERT-LESSONS.md` | `yral-mobile/MOBILE-EXPERT-LESSONS.md` | **Current** mobile-expert lessons P1–P8 including P8 ("read the exception, not just the symptom") which was specifically captured during the 24h audio transcription debug cycle 2026-06-03. |
| `root/todo.md` | `yral-mobile/todo.md` | Running session-state / todo file. |
| `docs-rishi/HANDOFF-SSE-STREAMING.md` | `yral-mobile/docs-rishi/HANDOFF-SSE-STREAMING.md` | SSE streaming feature handoff. Baseline. |
| `docs-rishi/MOBILE-EXPERT-LESSONS.md` | `yral-mobile/docs-rishi/MOBILE-EXPERT-LESSONS.md` | **Older** lessons (P1–P5 era, 50 lines). Not yet removed because no PR has explicitly cleaned up the docs-rishi/ duplicate. |
| `docs-rishi/PLAN-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/PLAN-CHAT-AS-HUMAN.md` | Chat-as-Human implementation plan. Baseline. |
| `docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | Chat-as-Human postmortem. Baseline. |
| `docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | `yral-mobile/docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | SSE streaming implementation plan. Baseline. |
| `docs-rishi/SSE-PHASE5B-PLAN.md` | `yral-mobile/docs-rishi/SSE-PHASE5B-PLAN.md` | SSE Phase 5b plan. Baseline. |
| `docs-rishi/SSE-PLANNING-NOTES.md` | `yral-mobile/docs-rishi/SSE-PLANNING-NOTES.md` | SSE planning notes. Baseline. |

## Total files in snapshot: 11

## Files NOT backed up (intentional skips)

Same as PR-1178/#1180: `README.md`, `AGENTS.md`, `build-logic/README.md` — standard repo / config files Sarvesh wouldn't strip.

## PR context (for future readers)

This PR closes the **"Create AI Influencer" CTA flicker on creator profile** issue diagnosed via instrumented logcat on the Motorola 2026-06-03 evening. The single change (`DefaultAuthClient.persistTokens`): drop the `updateBotCountFromPrefs()` call after `persistBotIdentitiesFromToken(idToken)`. The token-side merge still persists identities (account switcher needs delegate keys), but the canonical `botCount` flows only from `RootViewModel.refreshAccountDirectoryFromCanister` → `reconciledBots.size`, which intersects against v7's authoritative `botPrincipals` and so cannot be polluted by a stale token claim.

The underlying staleness — JWT `ext_ai_account_delegated_identities` claim listing 3 bot principals when v7 says 2 — is an **auth-issuer bug** flagged to the backend team (Anshuman). The mobile fix is a defense-in-depth, not the root fix.

## Diagnosis evidence preserved

Logcat captures from 2026-06-03 19:24 + 19:30 + 19:33 UTC documented the writer race (`existing=2 new=3 merged=3` from `persistBotIdentitiesFromToken` followed by `bots=2 reconciled=2` from `refreshAccountDirectory source=v7`). These are not preserved in this snapshot — they live in conversation history with claude-code session `ff795db2-faef-4e8b-8e1c-c6a4f862e551` and the PR description references them.

## Companion PRs

- **#1180** (`rishi/soul-file-coach`) — independent, parallel. Adds the Coach feature module.
- **#1182** (`rishi/profile-coach-entrypoint`) — stacked on #1180, independent of this PR.

This PR has no dependencies and can merge in any order against the other two.
