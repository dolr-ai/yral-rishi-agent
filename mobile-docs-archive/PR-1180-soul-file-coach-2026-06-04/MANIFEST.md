# PR-1180 Soul-File-Coach snapshot — 2026-06-04

**PR:** [dolr-ai/yral-mobile#1180](https://github.com/dolr-ai/yral-mobile/pull/1180)
**PR title:** feat(coach): Soul File Coach UI module + nav (Phase 7.5)
**Branch:** `rishi/soul-file-coach` (HEAD `63b88685` feat(coach): UX overhaul — resume, opening chips, Save CTA, Start over, receipt)
**Snapshot date:** 2026-06-04 (BEFORE adding Sarvesh as reviewer)

## Why this snapshot

Per `mobile-docs-archive/README.md` process: archive all at-risk mobile docs before adding Sarvesh as reviewer. Sarvesh strips non-source docs on merge.

This snapshot was originally staged 2026-06-04 morning after the openForBot reset (Issue 3) fix landed at `4950703e`, but Sarvesh-add was held when Session 6 reviewed the diff and noted the Coach feature was not yet user-usable — opening message + suggestion chips, Save CTA, Start over, and receipt rendering were all missing from the Feature Strategy plan. PR #1180 was extended with the UX overhaul commit `63b88685` on 2026-06-04 afternoon; the combined state was tested on Rishi's Motorola (Tests A–G, all passed). This snapshot is taken AFTER the UX overhaul commit lands to reflect the actual reviewer-state.

Backend dependency: PR #1180's UX overhaul is wired against `agent.rishi.yral.com` commits `f7f5753` (coach_messages.suggestions JSONB, PR #274) and `f582294` (resume mechanic + coach_opening + request_proposal + receipt_message, PR #275). Both deployed live before mobile testing.

## Branch base

This branch was created BEFORE PR #1178 (H2H) merged to main, so it does NOT carry the root-level `MOBILE-EXPERT-LESSONS.md` (P1–P8) or `H2H-IMPLEMENTATION-PLAN.md` introduced on #1178. Those files live in main now and in the PR-1178 snapshot. After #1180 lands, a rebase against main will pull them in — the rebase is not at risk from this snapshot.

## Files captured

Subdirectory layout preserves where each file lives on the branch (mirrors PR-1181 which has both root + docs-rishi versions of `MOBILE-EXPERT-LESSONS.md`).

| File | Source path | Why it matters |
|------|-------------|----------------|
| `root/HANDOFF-CHAT-AS-HUMAN.md` | `yral-mobile/HANDOFF-CHAT-AS-HUMAN.md` | Phase 1.10 handoff doc (Chat as Human / creator takeover). Pre-existing, carried over from main. |
| `root/todo.md` | `yral-mobile/todo.md` | Running session-state / todo file. Pre-existing, carried over from main. |
| `docs-rishi/HANDOFF-SSE-STREAMING.md` | `yral-mobile/docs-rishi/HANDOFF-SSE-STREAMING.md` | SSE streaming feature handoff. Carried over from baseline (predates this PR). |
| `docs-rishi/MOBILE-EXPERT-LESSONS.md` | `yral-mobile/docs-rishi/MOBILE-EXPERT-LESSONS.md` | **Older copy** of mobile-expert lessons (P1–P5 era, 50 lines). The newer root-level version (P1–P8, 96 lines) is NOT on this branch since #1178 hadn't merged when soul-file-coach branched. |
| `docs-rishi/PLAN-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/PLAN-CHAT-AS-HUMAN.md` | Chat-as-Human implementation plan. Carried over from baseline. |
| `docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/POST-MORTEM-CHAT-AS-HUMAN.md` | Chat-as-Human postmortem. Carried over from baseline. |
| `docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | `yral-mobile/docs-rishi/SSE-IMPLEMENTATION-PLAN.md` | SSE streaming implementation plan. Carried over from baseline. |
| `docs-rishi/SSE-PHASE5B-PLAN.md` | `yral-mobile/docs-rishi/SSE-PHASE5B-PLAN.md` | SSE Phase 5b plan. Carried over from baseline. |
| `docs-rishi/SSE-PLANNING-NOTES.md` | `yral-mobile/docs-rishi/SSE-PLANNING-NOTES.md` | SSE planning notes. Carried over from baseline. |

## Total files in snapshot: 9

## Files NOT backed up (intentional skips)

| File | Source path | Why skipped |
|------|-------------|-------------|
| `README.md` | `yral-mobile/` (root) | Standard repo doc, Sarvesh wouldn't strip |
| `AGENTS.md` | `yral-mobile/` (root) | Agents-related repo doc, expected to be preserved |
| `build-logic/README.md` | (build-logic dir) | Build infrastructure doc, part of source organization |

## PR context (for future readers)

The Soul File Coach PR adds the **creator coaching chat surface** (Phase 7.5): creator taps "Make your AI Influencer better" on their own bot profile and enters a chat with an AI coach that suggests targeted edits to the bot's `system_instructions`. The coach replies with either plain conversational text (clarifying questions) or a structured JSON proposal which the creator can **Apply** with one tap to update the bot.

This PR ships the **module** (`shared/features/coach/`): screen, viewmodel, repository, data source, DTOs, Decompose component, Koin DI, plus the UX overhaul wiring against backend PR #275 (resume by default, coach speaks first with opening message + 2–3 tappable suggestion chips, persistent "Save changes to {bot}" CTA above input bar, header hint line, Start over header action, receipt message after apply). The Coach is **reachable only via test code on this branch** — the UI entry-point on the profile screen is added in the stacked **PR #1182** (`rishi/profile-coach-entrypoint`).

The coach feature is gated by `ChatFeatureFlags.Chat.SoulFileCoachEnabled` (defaultValue=`false`); flips to `true` after backend cutover + GA.

**Backend dependency:** the coach endpoints (`POST /api/v1/creator/coach/conversations/{bot_id}`, `/messages`, `/apply`) live on `agent.rishi.yral.com` (yral-rishi-agent). Mobile points at `chat-ai.rishi.yral.com` by default in committed state; the actual cutover to agent is governed by `CHAT_BASE_URL`.

**Follow-up bugs surfaced during Motorola testing (2026-06-03 + 2026-06-04):**
- **Issue 3 (fixed in this PR's `4950703e`)**: opening Bot B's coach after Bot A's showed Bot A's messages — `openForBot` wasn't resetting `pending`/`coachConversationId`/etc., and the shared LocalViewModelStoreOwner-scoped `CoachViewModel` carried state across navigations.
- **Issue 1 (separate fix, PR #1181)**: Create-AI-Influencer CTA on creator profile flickering hidden after each session refresh — root cause is `DefaultAuthClient.persistTokens` writing `botCount` from a stale JWT claim. Mobile defensive fix shipped separately so #1180 stays single-concern.
- **Issue 2 (out-of-scope)**: Coach felt slow during testing — root cause was a stale `llm_process_config` row pointing `soul_file_coach` at `internal_vllm`. Backend fix on agent side (25.9 dashboard flip), no mobile change.
- **Empty-resume on legacy sessions (2026-06-04 testing, documented, no code change)**: sessions created in the backend before PR #275 deployed have no opening message persisted. On resume, those sessions render empty. Recovery: tap "Start over" to force a fresh session with the opening message. Per-user impact is limited to Rishi's pre-#275 test sessions; no production users affected.

## Companion PRs (stacked / parallel)

- **#1181** (`rishi/auth-bot-count-fix`) — independent fix for Issue 1, off main.
- **#1182** (`rishi/profile-coach-entrypoint`) — stacked on this PR. Adds the "Make your AI Influencer better" entry point so the coach screen is actually reachable from the profile. Will rebase against main once this PR merges.
