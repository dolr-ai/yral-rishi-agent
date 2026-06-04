# SSE streaming + Phase 3.8 error UX — implementation plan

**Status:** DRAFT for Rishi's approval — DO NOT IMPLEMENT YET.
**Date:** 2026-05-29
**Reference docs:** `~/Claude Projects/yral-rishi-agent/docs/SSE-PROTOCOL.md` (real wire spec), `POST-MORTEM-CHAT-AS-HUMAN.md` (what NOT to repeat), `SSE-PLANNING-NOTES.md` (architecture sketch from pre-spec).

This plan is written to a real, deployed backend contract. Every design decision is tagged with the post-mortem lesson it addresses.

---

## What I learned from reading the actual spec

Five things changed (or got confirmed) vs my pre-spec sketch:

1. **Event types matched the brief's guess** — `token` / `done` / `error`. Payload details differ (see §2 below), but the names are real.
2. **Tokens are pure deltas** — `data: {"text": "..."}` to append to a buffer. Not cumulative snapshots. (Was open question #1.)
3. **`done` carries the full `assistant_message`** — including the persisted server `id` and `created_at`. Critical for `loadedMessageIds` dedup. The spec also instructs: **replace the in-progress buffer with `assistant_message.content`** as the final state — server-side truth, handles any post-processing. (Was open question #2.)
4. **Backend has its own kill switch** — `ENABLE_SSE_STREAMING` env-flag. When OFF, the streaming endpoint returns **`404`**. Mobile must treat 404 as "streaming not available, fall back silently" — same effect as our own mobile flag being off.
5. **NSFW influencers are NOT streamable today** — the endpoint exists for them but returns a `NO_PROVIDER` error event. The mobile DTO does NOT currently expose `is_nsfw`, so we cannot detect this up front. Strategy: route through streaming, and catch `NO_PROVIDER` mid-stream as a fallback signal (same as `TRANSIENT` from a UX perspective, but log it differently for telemetry). (Anchored to lesson #4 — audit existing assumptions; "every conversation can stream" is wrong, the carve-out is implicit.)

**Phase 3.8 status check:** backend `SendMessageResponse` already has `error: Optional[AssistantError]` (see `app/models.py:193-196`). Mobile DTO does NOT have it yet. We add it as part of this PR — small additive change to the existing `SendMessageResponseDto` + a domain `AssistantError` + a mapper + a renderer. Single error renderer reused by both SSE and non-streaming paths.

---

## 1. Architecture

### 1.1 New files (all under `shared/features/chat/`)

| File | Purpose |
|---|---|
| `data/ChatStreamingDataSource.kt` | Owns the Ktor SSE call. Exposes a single `suspend fun streamMessage(conversationId, request): Flow<StreamEvent>` that emits typed events and completes on `done` or `error`. **Lifecycle: cancellation of the consuming coroutine cancels the connection.** |
| `data/models/StreamEvent.kt` | Sealed class: `Token(text: String)`, `Done(assistantMessage: ChatMessageDto, provider: String, tokens: Int, blocked: Boolean)`, `Error(error: AssistantErrorDto)`. |
| `data/models/AssistantErrorDto.kt` | `{code: String, message: String, retryable: Boolean}`. |
| `domain/models/AssistantError.kt` | Domain model. `code` as `AssistantErrorCode` enum (`BLOCKED_CONTENT`, `TRANSIENT`, `NO_PROVIDER`, `UNKNOWN`). |
| `domain/usecases/StreamAssistantReplyUseCase.kt` | Suspend use case returning `Flow<StreamEvent>`. Wraps the data source. Same pattern as `SendMessageUseCase` but Flow-typed. |
| `ui/conversation/AssistantErrorBubble.kt` | Composable for the inline Phase 3.8 error bubble (greyed, italic, warning icon). Reused by streaming `error` events and non-streaming response-body errors. |
| `ui/conversation/StreamingCursorIndicator.kt` | Small composable: a subtle pulsing block or cursor character appended to the streaming bubble while it grows. Replaces the existing waiting-wave during streaming. |

### 1.2 Modified files

| File | Change |
|---|---|
| `gradle/libs.versions.toml` | Add `ktor-client-sse = { module = "io.ktor:ktor-client-sse", version.ref = "ktor" }`. |
| `shared/libs/http/build.gradle.kts` | Add the new dependency. |
| `shared/libs/http/HttpClientFactory.kt` | Install the `SSE` plugin. **Critical:** the existing `HttpTimeout` config sets `requestTimeoutMillis = 30000`. Streaming requests can exceed this. We override per-call with `request { timeout { requestTimeoutMillis = HttpTimeout.INFINITE_TIMEOUT_MS } }` rather than globalize the change (preserves the 30s default for one-shot requests). |
| `shared/features/chat/data/models/SendMessageResponseDto.kt` | Add `error: AssistantErrorDto? = null`. Defaulted to null so legacy responses parse unchanged. |
| `shared/features/chat/data/models/Mappers.kt` | Add `AssistantErrorDto.toDomain()`, extend `SendMessageResponseDto.toDomain()` to carry the error. |
| `shared/features/chat/domain/models/SendMessageResult.kt` | Add `error: AssistantError? = null`. |
| `shared/features/chat/data/ChatDataSource.kt` | Add `streamMessage` declaration (return type: `Flow<StreamEvent>` or a custom `StreamHandle` — see §3). |
| `shared/features/chat/data/ChatRepositoryImpl.kt` | Add `streamMessage` delegation. |
| `shared/features/chat/domain/ChatRepository.kt` | Add the function. |
| `shared/features/chat/di/ChatModule.kt` | Register `ChatStreamingDataSource` and `StreamAssistantReplyUseCase`. Inject both into `ConversationViewModel` (which is already on the explicit `viewModel { ... }` block). |
| `shared/features/chat/viewmodel/ConversationViewModel.kt` | Significant changes — see §3. |
| `shared/features/chat/ui/conversation/ChatConversationScreen.kt` | Sticky-bottom heuristic + new-message pill. See §6. |
| `shared/features/chat/ui/conversation/ConversationMessagesList.kt` | Detect streaming-mode `Local` items and render with the cursor indicator instead of the waiting wave. Detect `AssistantError` items and route to `AssistantErrorBubble`. |
| `shared/features/chat/ui/conversation/ConversationScrollHelpers.kt` | One additional invariant to layer on top of the forward-only rule: when a stream is mid-token, sticky-bottom decides whether to follow. The existing forward-only guard stays as-is. |
| `shared/libs/feature-flag/src/commonMain/kotlin/com/yral/featureflag/ChatFeatureFlags.kt` | Add `SseStreamingEnabled: FeatureFlag<Boolean>` default `false`. |
| `shared/features/chat/src/commonMain/composeResources/values/strings.xml` | Strings for error bubble, retry button, blocked-content hint, "new message" pill. |

### 1.3 Lifecycle and cancellation contract

- **One slot:** `ConversationViewModel` holds `private var activeStreamJob: Job?`. Only one stream at a time per conversation.
- **Scope:** the Job is parented to `viewModelScope`. When the user navigates away, `viewModelScope` is cancelled, the Job is cancelled, the Ktor SSE collector throws `CancellationException`, the SSE plugin sends `cancel()` to the underlying connection. **Verified:** Ktor 3.x SSE plugin honors structured concurrency.
- **App-backgrounded:** Compose's `LifecycleOwner` doesn't cancel viewModelScope on background — the Job survives. But the OS will eventually kill the socket. **Decision:** explicitly cancel the active stream when `Lifecycle.STARTED → CREATED` (app backgrounded) and restart from the persisted message on foreground via a single `refreshHistory()` call. This avoids zombie sockets that the OS would kill anyway, and provides a clean recovery point. Implemented via a `LaunchedEffect` keyed to a Lifecycle state observer.
- **Re-entry:** if a stream is in flight and the user re-enters the conversation, the new ViewModel instance has no active job. The persisted (partial) message from the backend will not yet be saved (server only saves on `done`). The user re-entering will see only the user message + history — no partial bubble. This matches "no zombies" expectation. (Edge case 2 + 3.)

---

## 2. Wire protocol → data shapes

### 2.1 DTOs

```kotlin
@Serializable
data class AssistantErrorDto(
    @SerialName("code") val code: String,      // "BLOCKED_CONTENT" | "TRANSIENT" | "NO_PROVIDER"
    @SerialName("message") val message: String,
    @SerialName("retryable") val retryable: Boolean,
)

@Serializable
data class StreamTokenPayloadDto(
    @SerialName("text") val text: String,
)

@Serializable
data class StreamDonePayloadDto(
    @SerialName("assistant_message") val assistantMessage: ChatMessageDto,
    @SerialName("provider") val provider: String,         // "gemini" | "content_safety" | …
    @SerialName("model") val model: String? = null,
    @SerialName("tokens") val tokens: Int = 0,
    @SerialName("blocked") val blocked: Boolean = false,  // true for content-safety overrides
)
```

### 2.2 Parsed event stream

```kotlin
sealed class StreamEvent {
    data class Token(val text: String) : StreamEvent()
    data class Done(
        val assistantMessage: ChatMessage,
        val provider: String,
        val blocked: Boolean,
    ) : StreamEvent()
    data class Error(val error: AssistantError) : StreamEvent()
}
```

### 2.3 ChatStreamingDataSource shape

```kotlin
class ChatStreamingDataSource(
    private val httpClient: HttpClient,
    private val json: Json,
    private val preferences: Preferences,
    private val chatBaseUrl: String,
) {
    fun streamMessage(
        conversationId: String,
        request: SendMessageRequestDto,
    ): Flow<StreamEvent> = flow {
        val idToken = getIdToken()
        httpClient.sse(
            request = {
                method = HttpMethod.Post
                url {
                    host = chatBaseUrl
                    path("api/v1/chat/conversations", conversationId, "messages/stream")
                }
                headers {
                    append(HttpHeaders.Authorization, "Bearer $idToken")
                    append(HttpHeaders.Accept, "text/event-stream")
                }
                setBody(request)
                contentType(ContentType.Application.Json)
                timeout { requestTimeoutMillis = HttpTimeout.INFINITE_TIMEOUT_MS }
            },
        ) {
            incoming.collect { event ->
                when (event.event) {
                    "token" -> emit(StreamEvent.Token(parseToken(event.data)))
                    "done"  -> emit(StreamEvent.Done(parseDone(event.data)))
                    "error" -> emit(StreamEvent.Error(parseError(event.data)))
                    else    -> { /* spec doesn't define heartbeats; ignore unknowns. */ }
                }
            }
        }
    }
}
```

**Why Flow<StreamEvent> and not a callback API:** matches existing kotlinx patterns in the codebase, plays naturally with `viewModelScope.launch { stream.collect { … } }`, cooperates with cancellation automatically.

**Idle-timeout strategy:** Ktor's `requestTimeoutMillis = INFINITE` disables the request timeout. We add an explicit per-stream idle watchdog in the ViewModel — a `kotlinx.coroutines.withTimeoutOrNull(IDLE_TIMEOUT)` around the collect, restarted after each event. **Decision:** 30 seconds of no events = treat as `TRANSIENT`. This matches the spec's "if the stream closes without `done` or `error`, treat as `TRANSIENT`" recommendation.

---

## 3. ConversationViewModel changes

### 3.1 New state

Extends `LocalMessage` (the existing optimistic-message type) with one optional field:

```kotlin
data class LocalMessage(
    /* existing fields */,
    val streamingBuffer: String? = null,   // null = not streaming. non-null = grow this content.
)
```

When `streamingBuffer` is non-null:
- `MessageRow` renders `streamingBuffer` as the bubble content + the `StreamingCursorIndicator` to its right.
- `isWaitingAssistant()` returns false (wave animation suppressed — replaced by the cursor).

When the stream completes (`done`), the Local is removed and a Remote `ChatMessage` is added to `_overlay.sent` using the server-truth `assistant_message.content` (NOT our locally accumulated buffer). Per spec §"Client behavior recommendations".

### 3.2 New routing decision

```kotlin
private fun shouldStream(draft: SendMessageDraft, viewState: ConversationViewState): Boolean =
    viewState.isSseStreamingEnabled
    && draft.messageType == ChatMessageType.TEXT
    && draft.mediaAttachments.isEmpty()
    && draft.audioAttachment == null
    && !viewState.isHumanCreatorTakeoverActive
    && consecutiveStreamFailures < STREAM_FAILURE_CIRCUIT_BREAKER
```

- `STREAM_FAILURE_CIRCUIT_BREAKER = 3` consecutive stream-side failures → fall back to non-streaming for the rest of the session. Reset on success. Telemetry will track it.
- The takeover check is the only one that varies per turn (other inputs are static). All others are cheap to re-evaluate per send.

### 3.3 sendMessage rewrite (high level)

```kotlin
fun sendMessage(draft: SendMessageDraft) {
    val convId = conversationId ?: return
    // user side identical to today — optimistic Local USER added immediately
    enqueueOptimisticUser(draft, ...)

    if (shouldStream(draft, _viewState.value)) {
        startStreamingAssistant(convId, draft)
    } else {
        // existing non-streaming path
        legacySendAndHandleResult(convId, draft)
    }
}
```

`startStreamingAssistant`:
1. Add a streaming `LocalMessage(role=ASSISTANT, streamingBuffer="")` to `_overlay.pending`.
2. `activeStreamJob = viewModelScope.launch { … }`. Inside:
   - Watchdog wraps `streamingFlow.collect { … }` with 30s rolling idle timeout.
   - On `Token(text)`: append to local accumulator, schedule a coalesced update to `streamingBuffer` (50-100ms debounce — see §5).
   - On `Done(msg, blocked, _)`: replace pending streaming Local with a Remote `SentMessage(insertedAtMs=now, message=msg.toDomain())`. Insert `msg.id` into `loadedMessageIds` (poll-dedup). Reset failure counter. If `blocked == true`, attach a small "safety override" indicator? **Decision:** for v1 no special UI — the message reads naturally. Telemetry only.
   - On `Error(err)`: see §4.
3. On `CancellationException` (navigation away / background): silently drop the streaming Local. No banner. The user will see the conversation as if the stream never happened. On re-entry, `refreshHistory()` will not pick up anything (the backend didn't persist). This is intentional — partial AI replies are not promoted to user-visible.
4. On idle timeout: treat as `TRANSIENT` error path.

### 3.4 Send queue (edge case 4)

```kotlin
private val pendingSendQueue = mutableListOf<SendMessageDraft>()

fun sendMessage(draft: SendMessageDraft) {
    enqueueOptimisticUser(draft, ...)        // user bubble appears IMMEDIATELY
    pendingSendQueue.add(draft)
    drainQueue()
}

private fun drainQueue() {
    if (activeStreamJob?.isActive == true) return
    if (currentNonStreamingSendJob?.isActive == true) return
    val next = pendingSendQueue.removeFirstOrNull() ?: return
    if (shouldStream(next, _viewState.value)) startStreamingAssistant(...) else legacySend(...)
    // each branch calls drainQueue() in its terminal callback
}
```

- The user always sees their message instantly (post-mortem lesson #1 — user side).
- The AI reply for message N waits for message N-1's reply to finish. (No 5-streams-in-parallel chaos.)
- Queue depth is unbounded but visually bounded by the user's typing speed. **Decision:** no hard limit for v1; observe behavior.

### 3.5 `resetState()` carry-over

`isSseStreamingEnabled` is read at ViewModel construction. **Must be carried through `resetState()`** alongside the other flag-derived fields. (Direct anchor to post-mortem bug 6.)

I will:
- Add the field to `ConversationViewState` with default `false`.
- Set it in the initial `_viewState` construction via `flagManager.get(...)`.
- Add it to the `current.isSseStreamingEnabled` carry-over inside `resetState()`.
- Grep `ConversationViewState(` to confirm no third constructor exists. (Post-mortem checklist item #5.)

Additionally, `resetState()` must cancel any active stream job and clear `streamingBuffer` state. Today it already calls `stopTakeoverPolling()` and `stopCountdownTicker()` — same pattern.

---

## 4. Error handling — Phase 3.8 + SSE errors unified

Both the streaming `error` event and the non-streaming response-body error use the same `AssistantError` domain type. Rendering is unified.

### 4.1 Error → UI mapping

| `error.code` | Visual | Behavior |
|---|---|---|
| `BLOCKED_CONTENT` | Inline bubble, bot-styled (left-aligned, avatar), greyed background, italic text, ⚠ icon. Text is `error.message`. Below the bubble: "Try rephrasing" hint (small, tappable to focus the input). | `retryable=false` so no retry button on the user's message. The conversation continues; the user can type something else. |
| `TRANSIENT` | Same bubble styling. Text is `error.message`. | `retryable=true` → "Try again" affordance attached to the user's message. Tapping re-runs the original send. |
| `NO_PROVIDER` | Same bubble styling. Text is `error.message`. | `retryable=false`. If this surfaces during a stream (NSFW conversation hitting the streaming endpoint), the failure counter increments → next sends in this session go non-streaming. |
| (anything else / parse failure) | Same as `TRANSIENT`. | `retryable=true`. Defensive default. |

### 4.2 Inline bubble structure

```kotlin
@Composable
internal fun AssistantErrorBubble(
    error: AssistantError,
    onRephraseHint: () -> Unit,
    modifier: Modifier = Modifier,
) {
    // Box: same left-alignment + avatar slot as normal ASSISTANT bubble
    // Inner card: Neutral800.copy(alpha=0.5f) background, RoundedCornerShape(16.dp)
    // Row: ⚠ icon (16dp), Text(error.message, italic, Color.White.copy(alpha=0.8f))
    // Below row: when code == BLOCKED_CONTENT, a tiny "Try rephrasing →" link
}
```

### 4.3 Retry affordance on the user's message

When `error.retryable == true`, the user's preceding bubble gets a small "↻ Try again" button below it. Tapping re-sends the same draft. Implementation: extend `LocalMessage` with `retryDraft: SendMessageDraft?` (already present today as `draftForRetry`; we reuse the field). For a Remote sent message that needs retry, we keep the retry button on a separate `LastFailedSend` overlay slot. **Decision (defer for v1):** for messages that have transitioned Local→Remote (success path) but then got an error on the assistant side, the retry attaches to the *most recent USER message in overlay*. Bounded scope; we can iterate later.

### 4.4 Non-streaming path

When `legacySend` returns a `SendMessageResult` with `error != null`, exactly the same UI flow fires. Single error renderer, both call sites.

### 4.5 Telemetry

`chatTelemetry.assistantError(...)` emits with code + provider + whether it happened during streaming. Helps us see whether NSFW hits cluster or are evenly distributed.

---

## 5. Anti-jitter buffer (edge case 5)

Token events can arrive in TCP-aligned bursts (10 tokens in one packet). Naive recomposition on every token = jerky text.

**Strategy:** coalesce token appends within a 60ms window before committing to `streamingBuffer`.

```kotlin
private var pendingTokens = StringBuilder()
private var flushJob: Job? = null

private fun handleToken(text: String) {
    pendingTokens.append(text)
    if (flushJob?.isActive == true) return
    flushJob = viewModelScope.launch {
        delay(60)
        val flushed = pendingTokens.toString()
        pendingTokens.clear()
        _overlay.update { state ->
            state.copy(
                pending = state.pending.map { msg ->
                    if (msg.localId == streamingLocalId) {
                        msg.copy(streamingBuffer = (msg.streamingBuffer.orEmpty()) + flushed)
                    } else msg
                },
            )
        }
    }
}
```

- 60ms = ~16 frames at 60fps. Imperceptible delay; eliminates the burst-jitter.
- On `done`, force-flush any pending buffer before swapping to the final Remote message.
- On cancellation, drop pending buffer with no commit.

---

## 6. Auto-scroll: sticky-bottom + "↓ new message" pill

The existing `AutoScrollToAssistantMessage` machinery is preserved. We add **one orthogonal** behavior for streaming.

### 6.1 Streaming-aware sticky-bottom

```kotlin
val isNearBottom by remember {
    derivedStateOf {
        listState.firstVisibleItemIndex <= 1 &&
        listState.firstVisibleItemScrollOffset < STICKY_BOTTOM_THRESHOLD_PX
    }
}

// Inside a LaunchedEffect tied to a streaming-content "tick":
LaunchedEffect(streamingBufferLength) {
    if (isNearBottom && streamingBufferLength > 0) {
        runCatching { listState.scrollToItem(0) }
    }
}
```

- `STICKY_BOTTOM_THRESHOLD_PX ≈ 120px` (a couple of bubble heights).
- If the user has scrolled away from the bottom, **no auto-scroll occurs during streaming.** The "↓ new message" pill appears bottom-center; tapping it scrolls to bottom and dismisses itself.
- The existing forward-only invariant in `AutoScrollToAssistantMessage` continues to apply for the non-streaming send path and for the final `done` swap. (Post-mortem lessons #3 + #4 — invariants over paths, audit existing components.)

### 6.2 "↓ new message" pill

A small floating pill with a down arrow and "New message" label. Shown when `!isNearBottom && streamingBufferLength > 0`. Tap to scroll. Auto-dismisses 3s after the stream completes if the user hasn't interacted.

---

## 7. Polling dedup (edge case 9)

The existing 3-second `refreshHistory()` polling fires from a `ChatUnreadRefreshSignal` and from the takeover polling loop (not active here). For non-takeover user-AI chats, `refreshHistory()` does NOT fire on a 3-second timer today — it fires on send-success and screen entry. So the polling-collision risk on the user side is much smaller than I worried about in the planning notes.

But the principle stands: when the stream emits `done`, **insert `assistantMessage.id` into `loadedMessageIds` BEFORE swapping pending → sent**. The combine filter (`overlayState.sent.filterNot { it.message.id in loadedIds }`) will then dedup any subsequent paging fetch that loads the same message. (Post-mortem lesson #5 — trace every consumer.)

---

## 8. Carve-outs (routing tests)

| Scenario | Routing | Detection point |
|---|---|---|
| Feature flag OFF | non-streaming | `viewState.isSseStreamingEnabled == false` |
| Image / audio / multimodal | non-streaming | `draft.messageType != TEXT` OR media attachments present |
| Chat-as-Human takeover active | non-streaming | `viewState.isHumanCreatorTakeoverActive == true` |
| Backend returned 404 (backend kill switch) | fall back this turn + circuit-break next 3 turns | inside `ChatStreamingDataSource.streamMessage` HTTP error handler |
| Backend returned `NO_PROVIDER` (NSFW influencer) | fall back this turn + circuit-break next 3 turns for this conversation | inside the stream's `Error` event handler |
| 3 consecutive stream-side failures (any code) | non-streaming for the rest of this ViewModel's lifetime | `consecutiveStreamFailures >= 3` |

**Critical:** the 404 handling MUST be silent fallback. The user doesn't see "streaming unavailable." They see "AI responded normally, just took longer." No banner, no error bubble. (Post-mortem lesson #1 — user side perfection.)

---

## 9. Feature flag

```kotlin
val SseStreamingEnabled: FeatureFlag<Boolean> =
    boolean(
        keySuffix = "sseStreamingEnabled",
        name = "SSE token streaming",
        description = "When ON, text-only AI replies stream token-by-token via the new SSE endpoint. When OFF, uses the existing one-shot POST /messages endpoint.",
        defaultValue = false,
    )
```

Stored in `ConversationViewState.isSseStreamingEnabled`. Read at construction and carried through `resetState()`. (Anchored to post-mortem bug 6.)

---

## 10. Edge case matrix — final intended handling

The 10 user-side edge cases from the task brief, mapped to the design above. Each row also names the test that proves it.

| # | Edge case | Handling | Proof test |
|---|---|---|---|
| 1 | Network drops mid-stream | Idle watchdog → treat as `TRANSIENT` → render error bubble + retry button on user's message | Send → airplane mode mid-stream → see error bubble within 30s → tap retry → message re-sent |
| 2 | App backgrounded mid-stream | Lifecycle observer cancels stream; on foreground call `refreshHistory()` once | Send → home button → wait → reopen app → conversation shows user msg + history, no zombie partial bubble |
| 3 | User navigates away mid-stream | `viewModelScope` cancellation propagates → connection closed | Send → tap back → re-enter → conversation shows user msg + history |
| 4 | Concurrent sends | User bubbles appear immediately; assistant streams queue serially | Spam-tap send 5x in 3s → all 5 user bubbles visible → AI replies stream one at a time in order |
| 5 | Bursty tokens | 60ms coalescing buffer | Slow network simulation → words appear smoothly, not in chunks |
| 6 | Backend error event | `Error(BLOCKED_CONTENT/TRANSIENT/NO_PROVIDER)` → inline `AssistantErrorBubble` + retry per `retryable` | Use a known-blocked test prompt → see Phase 3.8 error bubble |
| 7 | User scrolled up | Sticky-bottom heuristic: no auto-scroll if `!isNearBottom`; "↓ new message" pill shown instead | Send → scroll up to read history while streaming → viewport stays put → pill appears → tap pill → scrolled to bottom |
| 8 | Takeover active | `shouldStream` returns false → non-streaming endpoint | Verified manually + telemetry should show 0 stream attempts for takeover-active conversations |
| 9 | Polling collision | `loadedMessageIds += assistantMessage.id` on `done`, before pending → sent swap | Send → on `done`, immediately force a `refreshHistory()` → bubble should not duplicate |
| 10 | Image/audio/multimodal | `shouldStream` returns false | Send image → telemetry shows non-streaming path used |

---

## 11. Test plan (post-mortem-anchored)

These are MUST-pass before opening the PR. They map directly to the checklist in `POST-MORTEM-CHAT-AS-HUMAN.md`.

### 11.1 Functional happy path
- [ ] Send "hi" to a text-only influencer → tokens stream in word-by-word. First word in <500ms.
- [ ] Send several messages in succession → each streams cleanly, in order.
- [ ] Send to a different influencer (e.g., Tara) → still streams.

### 11.2 Steady-state observation (post-mortem theme #2)
- [ ] Send a message, wait for `done`, then **sit idle for 30 seconds**. No flicker. No spontaneous scrolls. No log spam.
- [ ] Open a conversation with no streaming in progress, **sit idle for 30 seconds**. Should be visually identical to before this PR.

### 11.3 Multi-cycle (post-mortem checklist #8)
- [ ] Send 5 messages in rapid succession → user bubbles appear instantly; AI replies stream serially in correct order; no leakage.
- [ ] Send 10 messages over 10 minutes → no zombie state, no accumulating memory (rough subjective check).

### 11.4 Re-entry (post-mortem bug 6 + 8 territory)
- [ ] Send → leave conversation mid-stream → re-enter → no partial bubble; AI reply absent (server didn't persist).
- [ ] Send → wait for `done` → leave → re-enter → AI reply is present (persisted).
- [ ] Feature flag toggled via Firebase Remote Config → re-enter conversation → behavior matches new flag value (proves `resetState()` carry-over works).

### 11.5 Network degradation (edge case 1)
- [ ] Send → airplane mode immediately → before first token: silent fallback to non-streaming (still gets a reply after airplane mode off).
- [ ] Send → airplane mode AFTER 3 tokens streamed → idle watchdog fires after 30s → `TRANSIENT` error bubble + retry.

### 11.6 Backgrounding
- [ ] Send → home button after 2 tokens → wait 60s → reopen app → no zombie streaming bubble; conversation in clean state.

### 11.7 Force-kill recovery
- [ ] Send → swipe-kill the app mid-stream → reopen → conversation in clean state; partial bubble absent.

### 11.8 Carve-outs
- [ ] Send during active Chat-as-Human takeover → telemetry shows non-streaming endpoint used (verify via logcat).
- [ ] Send image with caption → non-streaming endpoint used.
- [ ] Force feature flag OFF locally → all sends go through non-streaming.

### 11.9 Scroll
- [ ] Scroll up mid-stream → viewport stays put → "↓ new message" pill appears → tap pill → scrolled to bottom.
- [ ] Stream completes while user scrolled up → pill stays for 3s → auto-dismisses if untapped.

### 11.10 Phase 3.8 error rendering
- [ ] Use a known-blocked prompt → see inline error bubble with `BLOCKED_CONTENT` message + "Try rephrasing" hint, no retry button.
- [ ] Simulate `TRANSIENT` (e.g., interrupt mid-stream) → see error bubble + retry button on user message → tap retry → original draft re-sent.

### 11.11 Cross-screen
- [ ] Verified separately that nothing in the Inbox, Wall, profile, or media upload flows is affected.

### 11.12 PR #1172 (Chat as Human) regression check
- [ ] Switch to a bot profile → open inbox → tap conversation → takeover bar still renders correctly.
- [ ] Toggle takeover ON → countdown starts; system banner appears.
- [ ] Send as creator → message appears; uses non-streaming endpoint.
- [ ] User side: send message during active takeover → no scroll backward, no hidden behind input.

---

## 12. Files-touched summary

**Added (8):**
- `ChatStreamingDataSource.kt`
- `StreamEvent.kt`
- `AssistantErrorDto.kt`
- `AssistantError.kt`
- `StreamAssistantReplyUseCase.kt`
- `AssistantErrorBubble.kt`
- `StreamingCursorIndicator.kt`
- (possibly) a small `StreamingEventBus` helper if the SSE plugin wiring needs it

**Modified (~14):**
- Gradle: `libs.versions.toml`, `shared/libs/http/build.gradle.kts`
- HTTP: `HttpClientFactory.kt`
- Chat data: `SendMessageResponseDto.kt`, `Mappers.kt`, `ChatDataSource.kt`, `ChatRepositoryImpl.kt`, `ChatRepository.kt`
- Chat domain: `SendMessageResult.kt`
- Chat DI: `ChatModule.kt`
- Chat VM: `ConversationViewModel.kt` (significant)
- Chat UI: `ChatConversationScreen.kt`, `ConversationMessagesList.kt`, `ConversationScrollHelpers.kt`
- Strings: `strings.xml`
- Feature flag: `ChatFeatureFlags.kt`

**Estimated lines:** ~1100 new lines, ~150 modified. About 80% of the new lines are the streaming data source, ViewModel changes, and the error/cursor composables.

---

## 13. Risks I'm tracking

1. **Ktor 3.3 SSE plugin maturity.** Documentation says it's stable, but I haven't used it in this codebase before. Will validate with a smoke test on Day 1 before building the rest. If it falls short, fallback is `client.preparePost(...).execute { … }` and manual SSE parsing — uglier but well-trodden territory.

2. **iOS Darwin engine SSE behavior.** Darwin's NSURLSession handles SSE differently from OkHttp under the hood. Both should work, but I want to verify on iOS at least via the iOS simulator build before opening the PR. If iOS misbehaves, the carve-out is "Android-only streaming for v1" — but I'd flag that to Rishi before shipping it that way.

3. **Memory: 60ms coalescing + viewModelScope retention.** The StringBuilder for pending tokens accumulates and clears. The `streamingBuffer` is a String inside an immutable LocalMessage that's re-created on each update — short-lived per-token allocations. For replies up to a few KB, no concern. For unusually long replies (10s of KB), worth a profiling check on Motorola.

4. **Existing forward-only auto-scroll invariant interacting with streaming auto-scroll.** Both layers should compose, but they share the same `listState`. I'll add a test where I trigger both simultaneously (send during a queue-pending state) to verify no conflict.

5. **Telemetry coverage of streaming failures.** If fallbacks happen silently, we lose visibility. Need to emit `assistantStreamFallback(reason)` events for each fallback path. Add to `ChatTelemetry`.

---

## 14. What I'm explicitly NOT doing in this PR

- iOS-side platform-specific testing beyond compilation. Sarvesh handles real iOS verification.
- Server-resumable SSE (`Last-Event-ID`). Spec doesn't support it.
- Streaming for image / audio / NSFW. Carved out per spec.
- Streaming the user's own message back. Pointless.
- Token-level analytics (per-token latency etc). Out of scope.

---

## 15. Open questions for Rishi before I start writing code

1. **Streaming cursor visual.** I'm planning a subtle pulsing block ("▌") at the end of the streaming bubble. Acceptable, or want something specific? (Alt: small dot animation; the existing wave; nothing at all.)
2. **"Try rephrasing" hint for `BLOCKED_CONTENT`.** I'm planning a small inline text link below the error bubble that focuses the input on tap. Acceptable or skip for v1?
3. **Send-during-active-stream behavior.** I'm planning to queue (user bubble appears, assistant reply waits). Confirm this is what you want — alternative is "cancel the in-progress stream and start the new one."
4. **NSFW influencer streaming fallback.** Mobile can't detect `is_nsfw` from the existing DTOs. I'll catch `NO_PROVIDER` mid-stream and fall back. Acceptable, or do you want to ask the backend session to expose `is_nsfw` on `ConversationResponse`?
5. **`done` with `blocked: true` (content-safety override).** I'm planning to render this as a normal AI reply — no special UI. The content itself reads as a safety message ("I'm here to help, but…"). Acceptable, or do you want a small "Safety override" subtle label?

---

## 16. Approval gate

Per your direction: **I do not start writing code until you have read this plan and approved it.** When you approve, my first three actions will be:

1. Add the Ktor SSE dependency and smoke-test it with a tiny standalone integration test against `agent.rishi.yral.com`. Confirms the protocol decode round-trip before I build anything on top.
2. Wire the feature flag end-to-end with `defaultValue = false`. Verify on Motorola that flag-off behavior is byte-for-byte identical to today.
3. Build the SSE data source + StreamEvent flow + ViewModel send routing. Round-trip a single `Hello! ` token stream into a visible bubble before adding any of the edge-case handling.

Implementation is at least 2-3 days of focused work + a full Motorola test pass before the PR opens. I'll quote a sharper ETA after the smoke test in step 1.
