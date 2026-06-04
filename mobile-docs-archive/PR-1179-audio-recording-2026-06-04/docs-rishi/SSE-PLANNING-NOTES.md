# SSE streaming — planning notes (pre-spec)

**Status:** Backend SSE endpoint not yet shipped. Wire spec at `docs/SSE-PROTOCOL.md` does not exist yet. Estimated 2-3 days out.

**Purpose of this doc:** Capture the architectural *shape* of the mobile work so that when the backend contract lands, planning can move fast. Every decision below is anchored to a lesson from `POST-MORTEM-CHAT-AS-HUMAN.md` so the same mistakes don't repeat. No code paths to specific message types — those come from the wire spec.

**Critical reminder (from post-mortem theme #1):** the user side is the primary surface. The streaming user is the entire point of the feature. Plan the user experience first, then build the plumbing to support it.

---

## What I will NOT decide until the spec lands

These need the wire format before they can be specified:

- The exact event-type names (`token` / `delta` / `chunk`, `done` / `complete`, `error`)
- Whether tokens are deltas (text fragments) or cumulative content snapshots
- The `done` event payload shape (does it include the full message? just the id?)
- The `error` event shape (does it match the Phase 3.8 top-level error object?)
- Whether the SSE endpoint reuses the existing `POST /messages` path with `Accept: text/event-stream` or has a separate `/messages/stream` path
- Authentication (Bearer header presumably, but worth verifying)
- Keep-alive / heartbeat behavior
- Whether server resumability is supported (Last-Event-ID)

I will read the spec end-to-end before committing to any of these.

---

## Architecture shape

### Where the SSE client lives

**Decision:** dedicated file `shared/features/chat/data/ChatStreamingDataSource.kt`. NOT inside the existing `ChatRemoteDataSource`.

**Why:** SSE has a fundamentally different lifecycle from request/response (long-lived connection, incremental delivery, cancellation semantics). Mixing it into `ChatRemoteDataSource` would violate symmetry — every method there is one-shot `httpGet` / `httpPost`. A separate file keeps the existing one-shot shape intact and lets the streaming code be its own concern.

**Post-mortem anchor:** theme #5 (don't change primitives without tracing every consumer). Keeping streaming in its own file means I don't have to change the shape of `ChatRemoteDataSource` and chase every caller.

### Lifecycle scope

**Decision:** each streaming send creates a `Job` parented to `viewModelScope`, **not** to a global scope. The Job is tracked in `ConversationViewModel` as `private var activeStreamJob: Job?`.

**Why:**
- ViewModel-scoped Jobs are cancelled when the ConversationViewModel is cleared (user navigates away). Handles edge cases 2 and 3 (background app, screen navigation away) for free.
- Single Job slot per ViewModel forces the back-to-back send queue (edge case 4): a new send cancels and replaces the previous Job after waiting for queue order.

**Post-mortem anchor:** theme #4 (audit existing components' assumptions). `viewModelScope` is the existing pattern — using it means cancellation propagation follows the same rules as every other suspend call in the ViewModel.

### Engine prerequisites (to verify when spec lands)

- Ktor `HttpClient` must have an SSE-capable plugin installed. Need to check `shared/libs/http/` to see what's wired today and whether SSE is already supported or needs adding.
- Android engine (OkHttp) supports SSE natively.
- iOS engine (Darwin / NSURLSession) supports SSE natively but with different timeout defaults — verify both.
- This is a deferred check; the audit goes in the real plan, not here.

---

## Fallback decision tree

**Decision:** the existing non-streaming `POST .../messages` endpoint is the canonical fallback. Streaming is an enhancement; the conversation must continue to work if streaming fails for any reason.

```
sendMessage(draft):
  if shouldUseStreaming(draft):
    try { stream and assemble }
    catch (connection error before first token):
      → fall back silently to POST .../messages
    catch (error mid-stream, after at least one token):
      → render the partial message + "tap to retry" affordance
         (do NOT silently re-send; the user has seen partial output)
    on done:
      → finalize the bubble with the server-provided message id
  else:
    → existing POST .../messages flow
```

**`shouldUseStreaming(draft)` returns false when:**
- Feature flag `sseStreamingEnabled` is OFF (post-mortem lesson #6 — flag-gating the whole feature is mandatory).
- `draft.messageType != TEXT` (edge case 10 — image/audio/multimodal goes through non-streaming).
- The conversation has active takeover (edge case 8 — takeover suppresses the AI; streaming would never deliver tokens). Detected via `viewState.isHumanCreatorTakeoverActive` if known locally, or by the same check used in the takeover routing.

**Why "first-token boundary" matters:** before the first token, silent fallback is fine — the user hasn't seen anything yet. After the first token, the user has seen content; silently restarting would jerk the bubble. The retry affordance is the cleaner UX.

**Post-mortem anchor:** theme #3 (write the invariant: "the user never sees a half-message presented as complete"). Render partial-stream + retry button rather than swallowing the partial state.

---

## Streaming-bubble state model

**Decision:** extend `LocalMessage` with one new field: `streamingContent: String? = null`. When non-null, the bubble renders this content + a streaming indicator (cursor or pulse). When the stream completes, `handleSendSuccess`-equivalent replaces the Local with a Remote message at the same overlay slot.

**Why:**
- The existing `_overlay.pending` mechanism already handles "optimistic user-facing bubble." Extending it is cheaper than adding a third overlay channel.
- The streaming indicator can replace the existing "waiting" wave animation — same visual slot, same `isWaitingAssistant`-style check.
- Using `LocalMessage` (vs creating a `StreamingMessage` type) means the existing `MessageRow` `when` branches don't gain a new arm. Smaller blast radius.

**Critical invariant (theme #3):** the streaming bubble grows monotonically. Tokens are appended; previous tokens are never re-rendered or replaced mid-stream. If the wire format sends cumulative snapshots instead of deltas, the client computes the delta locally and only appends new text. This protects against jitter from token-by-token recomposition.

**Anti-jitter buffer (edge case 5):** rolling buffer of 50-100ms. Tokens received within the buffer window are coalesced into one composition pass. Prevents the "burst of 10 tokens in one TCP packet" from producing 10 separate frames.

**Post-mortem anchor:** theme #4 — I have to audit `_overlay.pending`'s assumptions. The current code assumes pending messages have static content set at creation time and are removed on send-success. Streaming changes content while the message is pending. Every component that reads pending message content (rendering, height estimation for auto-scroll, dedup) needs to handle the dynamic case.

---

## Auto-scroll: sticky-bottom heuristic

**Decision:** auto-scroll follows the growing message **only if** the user is "near the bottom" (within ~100 px or 1-2 message heights). If the user has scrolled up to read history, the viewport stays put. A "↓ new message" pill appears bottom-center to indicate fresh content below.

**Implementation sketch:**
```
val isNearBottom = listState.firstVisibleItemIndex <= 1 &&
                   listState.firstVisibleItemScrollOffset < THRESHOLD_PX
if (isNearBottom) {
  listState.scrollToItem(0)  // follow the stream
} else {
  // do not move; show the "new message" pill
}
```

**Why a state, not a one-shot:** the heuristic is re-evaluated on every token arrival. If the user scrolls up during streaming, the auto-scroll *stops following* immediately. If they scroll back to bottom, it resumes.

**Critical interaction with existing auto-scroll machinery (theme #4 — audit existing components):**
- `AutoScrollToAssistantMessage` already exists. Its current invariant (after this session's fixes) is "never move backward in time, never move past the newest visible item." That invariant is *compatible* with the streaming-follow behavior — both can coexist.
- During an active stream, the streaming bubble IS the newest item. `findLatestAssistantIndex` returns it. Auto-scroll's existing logic should work without modification — the new sticky-bottom check is purely additive.
- This is a hypothesis. I'll verify by tracing both paths in the real plan.

**Post-mortem anchor:** theme #3 (invariant-first). The rule "viewport tracks the newest content unless the user has manually scrolled away" is the invariant. The 100px threshold is one specific implementation; the rule is the invariant.

---

## Send queue for back-to-back sends (edge case 4)

**Decision:** a single-slot queue. When the user sends while a stream is active:
- The new user message is immediately added to `_overlay.pending` as a Local USER (visible right away).
- The new send waits for the active stream to complete (or fail) before starting its own request.
- If the user sends a third message while two are queued, the second is silently dropped — the user sees their bubble vanish briefly, replaced by the third. Or: queue depth > 1 → reject with toast. Open question for the real plan.

**Why not cancel-and-replace:** the user might be mid-conversation with a long reply. Cancelling produces a partial-then-disappear UX that's worse than waiting 2-3 seconds.

**Why single-slot vs unbounded:** a user spamming Send 10 times in 5 seconds shouldn't fire 10 sequential streams. Drop the middles, keep the latest.

**Post-mortem anchor:** theme #2 (steady-state observation). The bug-prone scenario isn't a single rapid send — it's the *steady state* after 10 rapid sends: are there 10 in-flight streams? Is the order preserved? Is the bubble for message 7 still showing 30 seconds later? My test plan needs to include "spam send for 30 seconds, then observe."

---

## Polling collision (edge case 9)

**Decision:** the existing 3-second `refreshHistory()` polling stays for non-streaming conversations. For conversations in active streaming, polling is skipped — same pattern I used for the active takeover's `isHumanCreatorMessageSending` check.

**On stream completion:** the `done` event carries the message id. Mobile inserts it into `loadedMessageIds` (the existing dedup set) *before* the next polling tick. When polling next fetches, the just-completed message is filtered out of the overlay merge — no duplicate.

**Why this works:** `loadedMessageIds` is the existing dedup primitive. The streaming-complete path becomes a third "writer" to that set (alongside the paging source and the optimistic-send-success path).

**Post-mortem anchor:** theme #5 (trace every consumer). Before adding `loadedMessageIds.update { it + completedId }`, I need to verify the timing — does the next polling tick fire before or after my update? If after, dedup wins. If before, brief duplicate. Test required.

---

## Routing carve-outs

These conversations / sends should bypass SSE and use the existing endpoint:

| Case | Detection | Reason |
|---|---|---|
| `messageType != TEXT` (image, audio, multimodal) | `draft.messageType` | No content to stream; binary upload is one-shot |
| Active Chat-as-Human takeover | `viewState.isHumanCreatorTakeoverActive` *and* the recently-shipped server-side takeover state | The AI is suppressed; a stream would never deliver tokens. Falling back to the existing endpoint preserves the takeover send path |
| Feature flag OFF | `viewState.isSseStreamingEnabled` | Default behavior in production until cutover |
| Previous streaming attempt failed N times in a row | Counter in ViewModel state | Degrade gracefully if streaming is broken for this device/network |

**Post-mortem anchor:** theme #4 (audit existing assumptions). The takeover send path is the one I shipped last week. Streaming must not break it. The detection check is the safety net; the test plan must include "stream during takeover → verify NON-streaming path was used" (edge case 8 from the brief).

---

## Feature flag wiring

**Flag:** `ChatFeatureFlags.Chat.SseStreamingEnabled`, default `false`.

**Where it's read:**
- At `ConversationViewModel` construction (mirrors `isChatAsHumanCreatorEnabled` pattern).
- Stored in `ConversationViewState.isSseStreamingEnabled`.
- **Critically:** carried through `resetState()` (lesson from bug 6). Add to the field-by-field rebuild at the resetState construction call site.

**Where it's checked:** inside `shouldUseStreaming(draft)`. Single check point, not scattered.

**Production behavior:** flag stays OFF until backend SSE is GA'd and the production chat URL is `agent.rishi.yral.com`. Then Firebase Remote Config flip.

---

## Pre-mapped edge cases (from the task brief)

For each: the intended handling. Detail comes in the real plan after the spec lands.

| # | Edge case | Intended handling |
|---|---|---|
| 1 | Network drops mid-stream | After first token: render partial + retry button. Before first token: silent fallback to non-streaming endpoint. |
| 2 | App backgrounded mid-stream | Stream cancelled when ViewModel pauses / scope cancels. On foreground, `refreshHistory()` picks up the final message. No zombie. |
| 3 | User navigates away mid-stream | Same as #2 — viewModelScope cancellation handles it. |
| 4 | Concurrent sends (queue) | Single-slot queue, new user bubble visible immediately, send fires after current stream completes. |
| 5 | Bursty tokens | 50-100ms rolling coalescing buffer before composition. |
| 6 | Backend error event mid-stream | Render `error.message` styled per Phase 3.8 + retry if `retryable=true`. Same UI component as non-streaming Phase 3.8 errors. |
| 7 | User scrolled up while streaming | Sticky-bottom heuristic. No auto-scroll. Optional "↓ new message" pill. |
| 8 | Takeover active | Routing carve-out — non-streaming endpoint. |
| 9 | Polling vs SSE delivery race | Disable polling during active stream + insert completed id into `loadedMessageIds` on `done`. |
| 10 | Non-text content | Routing carve-out — non-streaming endpoint. |

---

## Phase 3.8 error UX (in scope per the brief)

Backend now returns a top-level `error` object on send. Mobile renders inline:
- Bot-styled bubble (left-aligned, same avatar) — to mirror "the bot is responding"
- Greyed background, italic text, small warning icon
- `error.message` as the bubble content
- If `error.retryable === true`: "Try again" affordance on the user's preceding message

**Reuse across paths:** the same renderer handles streaming `error` events and non-streaming response-body errors. Single composable, two call sites. Honors lesson #4 (audit existing renderer's assumptions before adding a new error type — bot-styled bubble + warning icon is additive to `MessageRow`).

---

## Test plan checklist (from post-mortem section "pre-flight checklist")

When the spec lands and code is written, the Motorola test plan must include:

- [ ] Two-role separation: user side and bot side both tested. (Streaming has only a user side, so this is trivially satisfied — but the takeover-active routing test exercises both.)
- [ ] **Steady-state idle**: open a conversation, do nothing for 30 seconds. Watch for unexplained behavior. (This is the test that would have caught the polling flicker. It is NOT optional.)
- [ ] **Multi-cycle**: send 10 messages in rapid succession (queue exercise), 10 messages with 5-second pauses (back-to-back stream lifecycle), 10 in a single hour-long session.
- [ ] **Re-entry**: leave and re-enter the conversation 3 times during an active stream and at completion.
- [ ] **Cross-device**: log in on a second device, send from there, observe streaming behavior on the first.
- [ ] **Network degradation**: airplane mode toggled at every stage of the stream lifecycle (before send, before first token, mid-stream, after `done`).
- [ ] **Backgrounding**: app to background at every stage. Foreground recovery.
- [ ] **Force-kill recovery**: kill mid-stream, reopen.
- [ ] **Takeover routing**: stream attempted in a takeover-active conversation → verify NON-streaming endpoint used.
- [ ] **Multimodal routing**: send image while streaming feature is on → verify NON-streaming endpoint used.
- [ ] **Scroll behavior**: user scrolled to mid-history, sender sends → verify viewport doesn't jerk.
- [ ] **Phase 3.8 error**: known-blocked prompt → verify inline error bubble + retry.
- [ ] **Feature flag OFF**: turn flag off in Firebase Remote Config → verify all sends use the existing endpoint.

---

## Open questions for when the spec lands

1. Are tokens deltas (new text only) or cumulative (full content snapshot each event)?
2. Does the `done` event include the final saved-message id and `created_at`? (Needed for `loadedMessageIds` dedup.)
3. Is the SSE endpoint a separate path or the existing path with content negotiation?
4. What's the heartbeat / keep-alive cadence? (Affects mid-stream timeout detection.)
5. Is `Last-Event-ID` resumption supported? (If yes, network-drop handling can be more sophisticated than "retry from scratch.")
6. What's the auth model — Bearer header same as existing endpoints?
7. Are there per-event ids for client-side ordering or is event order guaranteed?
8. Does the Phase 3.8 `error` event reuse the same `{code, message, retryable}` shape? (Confirmed in the task brief; verify in the spec.)
9. What happens if the user has `Accept: text/event-stream` but the conversation is in a state that can't stream (e.g., backend-side takeover active)? Does it fall back to JSON or return 4xx?
10. Is there a max-stream-duration on the backend that the client should mirror?

These get answered by reading `docs/SSE-PROTOCOL.md` end-to-end. They are NOT engineering decisions to make speculatively now.

---

## What "ready to plan" looks like

When the backend ships, I will:
1. Read `docs/SSE-PROTOCOL.md` end-to-end and answer the open questions above.
2. Re-read this doc and update every section that the spec affects.
3. Re-read the post-mortem checklist before writing any code.
4. Produce a real `PLAN-SSE-STREAMING.md` to share with Rishi for approval.
5. Only then start implementation.

Steps 1-4 are non-optional. The previous feature shipped with bugs because step 1 was rushed past.
