# HANDOFF — SSE streaming for the chat client

**Reviewer:** Sarvesh
**Branch:** `rishi/sse-streaming`
**Status when this doc was written:** all phases shipped, Motorola test pass §11 pending Rishi's manual run.

## What this PR does

Adds token-by-token SSE streaming to the existing chat flow, gated behind a feature flag (`ChatFeatureFlags.SseStreamingEnabled`). When the flag is OFF, the chat client behaves byte-identically to today's `POST .../messages` endpoint — the new code is dormant. When the flag is ON and the carve-outs allow it, sends flow through `POST .../messages/stream` and tokens render incrementally.

The companion backend work is the FastAPI streaming endpoint at `chat-ai.rishi.yral.com/api/v1/chat/conversations/{id}/messages/stream` (separate PR, separate timeline). This PR is mobile-only.

## Why the URL stays at `chat-ai.rishi.yral.com`

`AppConfigurations.CHAT_BASE_URL` keeps the production chat host. The streaming endpoint is being prepared at the same host on a separate PR; once that ships, the mobile flag flip alone activates streaming with zero further mobile work. There's a local-only `agent.rishi.yral.com` override that Rishi uses against the dev backend during development — the pre-commit hook at `.git/hooks/pre-commit` rejects any commit that reintroduces that override on origin.

The same applies to `SseStreamingEnabled.defaultValue = false` — the flag stays dormant on origin until backend cutover + GA. The same pre-commit hook blocks accidental `defaultValue = true` commits.

## Commits on the branch (in order)

Phase 5a/b/c + Phase 4 + Phases 6-9 landed first, then a post-Phase-10 fix pass after Rishi exercised the build on a Motorola against the dev backend and reported a few residual UX issues. The fix pass corrected Codex-identified causes, not symptoms.

1. `feat(chat): SSE 5a — core streaming plumbing (ChatStreamingDataSource, StreamEvent Flow API)` — adds the Ktor SSE data source, DTOs, the `StreamEvent` Flow contract, the feature flag, and the HTTP client SSE plugin install. Flag off by default.
2. `feat(chat): SSE rendering pipeline — path lock + cursor isolation + coalescing` — the verified flicker fix. Phase 5b (path lock) and Phase 5c (cursor-out-of-content + 250ms coalescing) ship together because Phase 5b alone left a known-buggy intermediate state.
3. `docs(chat): SSE planning, post-mortem, and mobile expert lessons` — `SSE-IMPLEMENTATION-PLAN.md`, `SSE-PHASE5B-PLAN.md`, `SSE-PLANNING-NOTES.md`, `POST-MORTEM-CHAT-AS-HUMAN.md`, and the append-only `MOBILE-EXPERT-LESSONS.md`.
4. `feat(chat): SSE Phase 4 — typed AssistantError plumbing for stream errors` — replaces the inline `LegacyErrorPayloadDto` with a typed `AssistantError` domain model. Sets up Phase 6.
5. `feat(chat): SSE Phase 6 — AssistantErrorBubble + retry off the typed error` — the visible error bubble. Rendered when an SSE `error` event fires. Retry button is inside the bubble (deviates from spec §4.3's "retry on user message" — rationale in the bubble's KDoc).
6. `feat(chat): SSE Phase 7 — idle watchdog + single-stream send queue` — 30s idle watchdog synthesizes a TRANSIENT error so a stalled stream stops looking alive. Send queue enforces "at most one SSE collect in flight per conversation"; queued sends drain FIFO on the active stream's terminal event. User Locals still appear in overlay instantly (post-mortem rule: user sees their message instantly).
7. `feat(chat): SSE Phase 8 — streaming-aware sticky-bottom auto-scroll` — original Phase 8; superseded by commit 11 below.
8. `feat(chat): SSE Phase 9 — consecutive-failure circuit breaker` — three consecutive SSE failures (Failed event, idle timeout, or connection error) silently force the rest of the session onto the non-streaming legacy endpoint. Reset on Done. Counter zeros on conversation switch.
9. `docs(chat): SSE handoff doc for Sarvesh review` — initial version of this document (test results filled in later by commit 14).
10. `fix(chat): SSE Phase 7 — atomic add of user + streaming placeholder` — Phase 7's split overlay update was producing a sporadic one-frame "big pink box" flash on Soma sends (LazyColumn measure pass briefly let the unconstrained user bubble take its max width). Restored atomic update for the no-active-stream case; queue still handles the active-stream case.
11. `fix(chat): drop Phase 8 per-buffer scrollToItem (Codex H3, primary Soma jerkiness)` — Codex's independent diagnosis (verified against code, not my earlier H1/H4 ranking) identified the Phase 8 LaunchedEffect's per-buffer-length scroll as the primary cause of streaming jerkiness. Deleted the block entirely; `reverseLayout=true` LazyColumn anchors the bottom naturally.
12. `fix(chat): drop duplicate screen-side refreshHistory trigger` — two refresh triggers were firing on conversation entry (screen-side `LaunchedEffect(Unit)` + VM-side `pagedHistory` rebuild on conversationId change). Removed the screen-side trigger; VM-side is the canonical signal. Soma re-entry double-flicker reduced from 2 to ≤1.
13. `fix(chat): disable send button while AI reply is in flight (match chat-ai)` — production chat-ai disables the send button during bot replies; v2 was letting users fire another send during streaming. New `isReplyInProgress` flow in VM is reference-counted (so a Done that immediately drains a queued send keeps count > 0 and the button doesn't flicker enabled-then-disabled). UI ORs it into the existing `hasWaitingAssistant` flag.
14. `docs(chat): SSE handoff test results from the Phase 10 Motorola pass` — this commit. §11 results filled in; commit list expanded to include 10-13.

## Files added

- `data/ChatStreamingDataSource.kt`
- `data/models/StreamTokenPayloadDto.kt`
- `data/models/StreamDonePayloadDto.kt`
- `data/models/AssistantErrorDto.kt`
- `data/ConversationContentCache.kt`
- `domain/models/StreamEvent.kt`
- `domain/models/AssistantError.kt`
- `ui/conversation/AssistantErrorBubble.kt`

## Files modified

- `domain/ChatRepository.kt` + `data/ChatRepositoryImpl.kt` — new `streamMessage(...)` returning `Flow<StreamEvent>`
- `data/di/ChatModule.kt` — DI for the new data source + cache
- `viewmodel/ConversationViewModel.kt` — most of the new behavior (sendMessage routing, SSE collect, coalescing, path lock, cache hydration, error state, send queue, idle watchdog, circuit breaker)
- `ui/conversation/ConversationMessageBubble.kt` + `ConversationMessagesList.kt` + `ChatConversationScreen.kt` — rendering pipeline + sticky-bottom scroll
- `libs/http/HttpClientFactory.kt` — Ktor SSE plugin install
- `libs/feature-flag/ChatFeatureFlags.kt` — new `SseStreamingEnabled` flag (default `false`)

## The Phase 5b ↔ 5c rendering contract (do not regress)

Documented verbatim in `MOBILE-EXPERT-LESSONS.md` and the `RegularBubble` KDoc. Summary:

- **Path lock**: `markdownLockedOverride` pins Markdown vs Text per stream. NEVER let the path switch mid-stream or at done.
- **Cursor isolation**: the streaming cursor must be a sibling composable, never appended to the content string the renderer parses.
- **Coalesce window**: 250ms is shipped. Don't tune without re-measuring on a Motorola.
- **Flush ordering**: `flushJob?.cancelAndJoin()` MUST run before any code that removes the streaming Local.

If a future session touches `RegularBubble`, `MessagesList`, or `startStreamingAssistantReply`, those four lines are load-bearing.

## Hard-won lessons file

`MOBILE-EXPERT-LESSONS.md` is append-only and stored with the rest of these Rishi/SSE handoff docs. It captures P1-P5 from the flicker-debugging arc (multi-flavor install hazard, Gradle build cache, macOS `strings` tooling, claims-vs-code drift, premature theorizing).

## Test plan — §11 from `SSE-IMPLEMENTATION-PLAN.md`

Twelve sub-sections. The Phase 10 Motorola pass against the dev backend covered the user-visible cases needed to gate the PR. Items marked "Not exercised in this pass" are still covered by code-level review or are deferred to the post-cutover smoke pass:

| # | Sub-section | Status |
|---|---|---|
| 11.1 | Functional happy path (Aasha, Ragini, Urvashi, Soma, Priya, Monika) | **PASS** — tokens stream in coalesced batches; first word in <500ms; no flicker post-fix |
| 11.2 | Steady-state observation (30s idle, no flicker, no spontaneous scrolls) | **Implicit PASS** — extended session use; no idle artifacts observed |
| 11.3 | Multi-cycle (5 in succession, 10 over 10 minutes) | **PASS** for 5-in-succession (Soma); 10-over-10min not explicitly timed but session use covered it |
| 11.4 | Re-entry (leave mid-stream, leave after done, flag toggle) | **PASS** — Soma re-entry double-flicker was 2, now ≤1 after dropping the redundant screen-side `refreshHistory()` trigger |
| 11.5 | Network degradation (airplane mode pre-token vs mid-token) | Not exercised in this pass. Code paths covered: pre-token failure routes through circuit breaker → legacy fallback after 3 fails; mid-token stall routes through 30s idle watchdog → TRANSIENT error bubble + retry |
| 11.6 | Backgrounding (home button → 60s → resume) | Not exercised in this pass. No explicit lifecycle observer ships; idle watchdog covers the "no events for 30s" path |
| 11.7 | Force-kill recovery | Not exercised in this pass. No streaming state is persisted, so cold-start renders the last server-persisted history only |
| 11.8 | Carve-outs (takeover, image+caption, flag off) | **Code-reviewed PASS** — `shouldStream()` returns false for active takeover, media attachments, audio attachments, flag off, and post-circuit-breaker state. Not exercised end-to-end on device |
| 11.9 | Scroll (scroll up mid-stream; no "↓" pill in this PR — deferred polish) | **PASS** — scroll-up mid-stream keeps the viewport pinned (no auto-jump). Pill is deferred polish |
| 11.10 | Phase 3.8 error rendering (BLOCKED_CONTENT, TRANSIENT + retry) | Not exercised in this pass — no synthetic error was triggered. Plumbing is verified at code level: `StreamEvent.Failed` → `AssistantError` → `AssistantErrorBubble` → retry through the same queue path |
| 11.11 | Cross-screen (Inbox, Wall, profile, media upload unaffected) | Not exercised in this pass. The PR only touches `shared/features/chat/` (modulo the SSE flag declaration); other features can't reach the new code paths |
| 11.12 | PR #1172 (Chat as Human) regression check | Not exercised in this pass. `shouldStream()` returns false during active takeover, so the takeover path keeps using `sendMessageUseCase` exactly as PR #1172 shipped it |

**Recommended post-cutover smoke pass (out of scope for this PR):** §11.5 (airplane-mode), §11.6 (backgrounding), §11.7 (force-kill), §11.10 (synthetic error trigger), §11.12 (full takeover regression).

## Known limitations / deferred work

- **No "↓ new message" pill** (Phase 8 polish). Sticky-bottom auto-scroll works; the pill for when-user-scrolled-up is a UX polish that can be added without revisiting the underlying scroll math.
- **No background-pause lifecycle observer** (Phase 7). Compose Multiplatform lacks a unified Lifecycle primitive. The spec's "wait 30s in background" test is covered by the idle watchdog firing on return. If we add the observer later, the hook is `activeStreamJob?.cancel()` from a screen-side `DisposableEffect`.
- **No per-error telemetry events** (Phase 9). `chatTelemetry.assistantError(code, provider, isStreaming)` from spec §4.5 should be designed alongside other Phase 12 observability work, not inline.
- **No NSFW conversation carve-out** (Phase 9). Waits on the backend exposing `is_nsfw` on the conversation DTO.
- **No 404-immediate fallback** (Phase 9). The circuit breaker provides a graceful 3-strikes fallback to the legacy endpoint. A faster path is possible but adds an exception-type dispatch in the streaming data source that isn't warranted yet.

## Companion docs

- `SSE-IMPLEMENTATION-PLAN.md` — phased plan + the §11 test matrix
- `SSE-PHASE5B-PLAN.md` — path-lock + stale-while-revalidate sub-plan with the Aasha gating test
- `SSE-PLANNING-NOTES.md` — running notes from planning rounds
- `MOBILE-EXPERT-LESSONS.md` — the append-only mobile-debugging lessons file
- `POST-MORTEM-CHAT-AS-HUMAN.md` — the prior feature's post-mortem (points at the lessons file)
