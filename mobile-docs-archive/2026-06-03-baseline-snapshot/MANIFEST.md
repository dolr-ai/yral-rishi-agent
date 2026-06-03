# Baseline snapshot — 2026-06-03

**Type:** Process-inception baseline (no specific PR)
**Reason:** Process for archiving mobile docs before Sarvesh review established 2026-06-03. This snapshot captures ALL at-risk docs that existed as of that date so nothing already-written is lost.

## Files captured

| File | Source path | Why it matters |
|------|-------------|----------------|
| `MOBILE-EXPERT-LESSONS.md` | `yral-mobile/docs-rishi/` | Discipline patterns (P1-P7) from mobile expert sessions. Each future mobile-expert session reads this to avoid repeating past mistakes. |
| `HANDOFF-CHAT-AS-HUMAN.md` | `yral-mobile/` (root) | Handoff doc for Phase 1.10 (Chat as Human / creator takeover) including the 3-scenario Motorola retest checklist. |
| `PLAN-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/` | Implementation plan for Phase 1.10 (Chat as Human creator takeover). |
| `POST-MORTEM-CHAT-AS-HUMAN.md` | `yral-mobile/docs-rishi/` | Post-mortem for the 3 takeover bugs caught during 2026-05-28 Motorola retest (timer reset on user activity, gap before sweep, "X has left" duplication). |
| `SSE-IMPLEMENTATION-PLAN.md` | `yral-mobile/docs-rishi/` | Implementation plan for Phase 10 (SSE streaming) mobile-side. |
| `SSE-PLANNING-NOTES.md` | `yral-mobile/docs-rishi/` | Planning notes for SSE design, including the 250ms coalescing decision and markdown re-parse fix. |
| `SSE-PHASE5B-PLAN.md` | `yral-mobile/docs-rishi/` | Phase 5b sub-plan for SSE — the `ConversationContentCache` work that caused the iOS Kotlin Native compile failure. |
| `HANDOFF-SSE-STREAMING.md` | `yral-mobile/docs-rishi/` | Handoff doc for SSE streaming feature. |
| `todo.md` | `yral-mobile/` (root) | Session-state / running todo file used by mobile expert. |

## Files NOT backed up (intentional skips)

| File | Source path | Why skipped |
|------|-------------|-------------|
| `README.md` | `yral-mobile/` (root) | Standard repo doc, Sarvesh wouldn't strip |
| `AGENTS.md` | `yral-mobile/` (root) | Agents-related repo doc, expected to be preserved |
| `build-logic/README.md` | (build-logic dir) | Build infrastructure doc, part of source organization |

## Total files in snapshot: 9
