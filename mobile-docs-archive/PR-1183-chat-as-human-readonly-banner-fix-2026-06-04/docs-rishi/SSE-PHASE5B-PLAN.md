# SSE streaming — Phase 5b plan (no-flicker Markdown + stale-while-revalidate)

**Status:** DRAFT for Rishi's approval — DO NOT IMPLEMENT YET.
**Date:** 2026-05-29
**Scope:** Two bugs surfaced in Ragini testing. Neither requires product regression. Both are addressable with surgical changes on top of the current `rishi/sse-streaming` branch.

---

## Reframing what I got wrong before

I proposed dropping Markdown rendering or accepting flicker. Both were product regressions. The correct framing: **the swap from Text-during-stream to Markdown-on-done is the bug, not Markdown itself.** Render Markdown the whole way through, decide the path once, persist it through the Local→Remote handoff, and the swap goes away.

For re-entry, the legacy "blank screen during paging" window is fixable by a standard stale-while-revalidate pattern — *not* a Decompose state-keeping rewrite.

---

## Bug A — In-stream Markdown swap on `done` (Ragini-style content)

### What happens today

1. Streaming Local is rendered through Text path (my Phase 5 forced this).
2. At `done`, Remote is rendered through the existing decision: `if (content.shouldRenderAsMarkdown()) Markdown else Text`.
3. For ASCII-Hinglish content (Ragini's Latin-script roleplay), `shouldRenderAsMarkdown` returns true → Markdown subtree replaces Text. Visible re-styling: `*chuckles*` literal → *chuckles* italic.

### Root cause

The path decision is computed per-render-call against the current content. Streaming and `done` evaluate it differently because the cursor was historically in the content (now removed in my Phase 5b sub-fix) — but the *real* issue is: **Text-during-stream + Markdown-after-done is a binary swap by design**.

### Fix: render Markdown during streaming too, with path locked once on first content

Path decision happens **once**, on the first non-empty token, based on `firstContent.shouldRenderAsMarkdown()`. That decision is persisted onto the streaming Local AND carried over to the Remote at `done`, so both render identically.

The library (`com.mikepenz:multiplatform-markdown-renderer:0.38.0`) gracefully renders incomplete markdown — `*chuckles` (unclosed) renders as literal asterisks; the moment the closing `*` arrives, it transitions to italic in-place via the library's own incremental re-render, not a Compose slot swap. Same UX as ChatGPT, Claude, Perplexity.

#### Why path-locking is needed (not just "always Markdown")

The library doesn't handle emoji and combining diacriticals well — that's why `shouldRenderAsMarkdown` exists as a gate today. If we naively always use Markdown for streaming:

- First token "Bhai" (ASCII) → Markdown
- Later token "❤️" (emoji) arrives → if we re-evaluate `shouldRenderAsMarkdown` on cumulative buffer, returns false → swap back to Text mid-stream → flicker

So we lock the decision on the first non-empty content. Once locked, subsequent token additions don't change the path. The library renders whatever buffer it gets — emoji-mid-Markdown may look slightly off, but doesn't swap subtrees.

#### Implementation

**1. Extend `LocalMessage`** with `useMarkdownLocked: Boolean? = null`.

**2. In `ConversationViewModel.startStreamingAssistantReply`'s Token handler**: when applying the token, if the resulting buffer is non-empty AND `useMarkdownLocked` is still null on the streaming Local, compute `shouldRenderAsMarkdown(buffer)` and set the lock.

```kotlin
state.pending.map { msg ->
    if (msg.localId == streamingLocalId) {
        val newBuffer = (msg.streamingBuffer.orEmpty()) + event.text
        val lock = msg.useMarkdownLocked
            ?: if (newBuffer.isNotEmpty()) newBuffer.shouldRenderAsMarkdown() else null
        msg.copy(streamingBuffer = newBuffer, useMarkdownLocked = lock)
    } else {
        msg
    }
}
```

**3. Persist the lock across the Done swap.** Add a ViewModel-scoped field:
```kotlin
private val _streamMarkdownLockedRemoteIds = MutableStateFlow<Map<String, Boolean>>(emptyMap())
val streamMarkdownLockedRemoteIds: StateFlow<Map<String, Boolean>> = _streamMarkdownLockedRemoteIds.asStateFlow()
```

On `Done`, after grabbing the streaming Local's `useMarkdownLocked`, write `{msg.id → useMarkdownLocked}` into the map.

**4. Pass the map into `ChatConversationScreen`** and thread it through `ConversationMessagesList` → `MessageRow` → `MessageContent` → `MessageBubble` → `RegularBubble` as an optional `markdownLockedOverride: Boolean?` parameter.

**5. `RegularBubble` rendering decision**:
```kotlin
val useMarkdown = when {
    isStreaming -> useMarkdownLocked ?: false
    markdownLockedOverride != null -> markdownLockedOverride
    !content.isNullOrBlank() && content.shouldRenderAsMarkdown() -> true
    else -> false
}
if (useMarkdown) Markdown(content, …) else Text(content, …)
```

**6. Cursor cleanup**: the cursor stays appended visually only during `isStreaming`, same as my current Phase 5. The cursor renders fine inside the Markdown component (it's just trailing text the library passes through).

#### Edge cases handled

| Case | Behavior |
|---|---|
| First content "Bhai" (ASCII) | Lock = Markdown. Stream + Done = Markdown. No swap. |
| First content "नमस्ते" (Devanagari) | Lock = Text. Stream + Done = Text. No swap. |
| First content ASCII, later emoji arrives | Stays locked Markdown. Library renders emoji as best it can. No swap. |
| First content empty (edge) | Lock stays null; cursor-only bubble renders via Text path (handles `content == ""` gracefully). Lock computed on first real token. |
| `done` content differs from buffer (post-processing) | Lock applies regardless. Markdown for Markdown-locked, Text for Text-locked. |
| Re-entry (ViewModel recreated) | `_streamMarkdownLockedRemoteIds` is empty. Remote uses default `shouldRenderAsMarkdown`. For ASCII Hinglish, returns true → Markdown. Same as locked decision. Identical visual. |

### Effort

- `LocalMessage` field addition: 1 line
- ViewModel token-handler lock logic + map: ~15 lines
- Plumbing the override through to `RegularBubble`: ~6 lines across 4 files
- `RegularBubble` decision change: ~5 lines

**Total ~30 lines net, 5 files. No new dependencies. No streaming network/state code change.**

---

## Bug B — Re-entry vanish (chat blanks for ~500ms on inbox → back → chat)

### What happens today

1. User taps back to Inbox → Decompose destroys ConversationViewModel → `_overlay` cleared.
2. User taps Maya again → fresh ConversationViewModel → `_overlay.sent = emptyList`, `loadedMessageIds = emptySet`.
3. `LaunchedEffect(Unit) { viewModel.refreshHistory() }` fires → `historyRefreshTrigger++`.
4. `pagedHistory.flatMapLatest { … }` cancels the (already-empty) Pager, creates a new one with the new `ConversationMessagesPagingSource`.
5. Inside the lambda: `loadedMessageIds.value = emptySet()`. New Pager starts fetching page 0 from `/messages?limit=10&offset=0`.
6. **For ~500ms, the LazyColumn has zero overlay items AND `historyPagingItems` is still loading.** Visible window of nothing.
7. Page 0 arrives → items hydrate → list populates.

This is not streaming-specific. It happens with `SseStreamingEnabled=false` too. It's a long-standing legacy gap.

### Fix: standard stale-while-revalidate

Persist the conversation's last-known message list in a Koin-singleton cache keyed by conversation id. On ViewModel construction, *immediately* hydrate `_overlay.sent` from the cache. The user sees their prior chat the moment the screen mounts. Paging refetches in the background. As paging surfaces real messages, the existing `loadedMessageIds` dedup transparently removes the cached entries — same id, same content, no visual jump.

#### Component shape

**`ConversationContentCache`** (Koin `single { }`):

```kotlin
class ConversationContentCache(
    private val maxConversations: Int = 20,
    private val maxMessagesPerConversation: Int = 30,
) {
    private val cache = LinkedHashMap<String, List<ChatMessage>>(maxConversations, 0.75f, true /* access order */)

    @Synchronized
    fun read(conversationId: String): List<ChatMessage> =
        cache[conversationId].orEmpty()

    @Synchronized
    fun write(conversationId: String, messages: List<ChatMessage>) {
        cache[conversationId] = messages.takeLast(maxMessagesPerConversation)
        // LRU eviction via insertion-order LinkedHashMap (accessOrder=true)
        while (cache.size > maxConversations) {
            cache.remove(cache.keys.first())
        }
    }
}
```

In-memory only. No persistence to disk for v1 — keeps blast radius small. If the app is killed and reopened, cache is empty and re-entry behavior matches today. Acceptable.

#### Hydration path

**In `ConversationViewModel.setConversationId`** (already the single conversation-id sink):

```kotlin
val cached = conversationContentCache.read(id)
if (cached.isNotEmpty()) {
    val cachedSent = cached.mapNotNull { msg ->
        parseTimestampToEpochMs(msg.createdAt)?.let {
            SentMessage(insertedAtMs = it, message = msg)
        }
    }
    _overlay.update { it.copy(sent = cachedSent) }
}
```

This runs before `refreshHistory()` fires (which is in a LaunchedEffect, asynchronous). The first combine emission already sees the cached items.

#### Persist path

A new VM-level observer that snapshots the visible message list and writes it back:

```kotlin
init {
    viewModelScope.launch {
        // Debounce-and-merge: write at most once every 500ms
        snapshotMessagesForCache()
            .debounce(500.milliseconds)
            .collect { (convId, msgs) ->
                if (convId.isNotBlank()) {
                    conversationContentCache.write(convId, msgs)
                }
            }
    }
}

private fun snapshotMessagesForCache(): Flow<Pair<String, List<ChatMessage>>> =
    combine(
        _viewState.map { it.conversationId.orEmpty() }.distinctUntilChanged(),
        overlay,  // existing StateFlow<List<ConversationMessageItem>>
    ) { convId, overlayItems ->
        val messages = overlayItems.mapNotNull {
            when (it) {
                is ConversationMessageItem.Remote -> it.message
                else -> null
            }
        }
        convId to messages
    }
```

Notes:
- Pulls only `Remote` items (not Local `pending`) — cache should reflect server-confirmed state.
- Doesn't include history paging items. Could be extended later by also snapshotting `historyPagingItems.itemSnapshotList`, but for v1 just caching `overlay.sent` covers the just-streamed messages, which is the most-recently-relevant content.
- Actually — **revision**: paging snapshot SHOULD be included for full coverage. Otherwise on re-entry only the streamed-this-session messages appear (everything else still blanks).
  - Add `historyPagingItems.itemSnapshotList.items.mapNotNull { (it as? ConversationMessageItem.Remote)?.message }` to the snapshot.
  - The snapshot is sourced from the screen (where `historyPagingItems` lives), so the cache-write call has to happen at screen level OR the ViewModel needs a separate signal mechanism. **Easiest: the screen passes the paging snapshot down to the VM via `viewModel.recordHistorySnapshot(items)` whenever LazyPagingItems updates.**
  - Adds ~5 lines in `ChatConversationScreen`.

#### Dedup integrity on re-entry

When paging completes page-0 fetch:

1. `pagingData.map` adds each id to `loadedMessageIds`.
2. The combine block recomputes `filteredSent = state.sent.filterNot { it.message.id in loadedIds }`. Cached `overlay.sent` items whose ids are also in page 0 get filtered out.
3. The paged version of the same message renders in the history portion. Same id, same content. **Visually identical**, position-stable.

The user perceives: cached chat appears instantly → paging silently swaps cached items for paged ones → no visible change.

#### Edge cases

| Case | Behavior |
|---|---|
| Cache miss (first-ever conversation visit, or LRU evicted) | Cache returns empty list. Falls back to today's behavior. No regression. |
| Backend returns different content than cache (e.g., another device sent a message) | Cached items render briefly; paging completes ~500ms later with newer content; visual update propagates. Acceptable stale-while-revalidate trade-off. |
| Cache has messages newer than page 0 (impossible in practice since cache writes the visible list which equals/precedes page 0) | The `loadedMessageIds` dedup handles overlap. Items not in page 0 stay in overlay.sent (and would be cleaned up on next scroll/page-load). |
| App killed and reopened | Cache empty. Today's vanish-then-fill behavior on first conversation visit, then cached on subsequent visits within session. |

### Effort

- New `ConversationContentCache` class: ~25 lines
- Koin registration: 1 line
- VM constructor param + read on `setConversationId`: ~8 lines
- VM snapshot/write loop: ~20 lines
- Screen-level paging snapshot wiring: ~5 lines

**Total ~60 lines net, 4 files. One new injected singleton. No new dependencies.**

---

## What I'm NOT doing in Phase 5b

- Switching to a different Markdown library. The existing one handles incomplete markup gracefully and has the Compose integration we need. Investigated `multiplatform-markdown-renderer:0.38.0` — its parser tolerates unclosed `*`/`**` and renders them literal until a closing pair arrives.
- Reworking Decompose nav/state keeping. The stale-while-revalidate cache lives entirely in Koin-scoped singleton + ViewModel; touches no nav code.
- Persisting cache to disk. In-memory is sufficient for the user-visible re-entry window; survives "back to inbox" but not app kill. Disk persistence is a Phase 5c+ enhancement.
- Eliminating cache miss on cold app launch. First conversation visit after launch will still vanish-and-come-back. Acceptable for v1.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Markdown lib mis-renders Devanagari mid-stream when ASCII first-token locked us to Markdown | Verified manually: lib renders Devanagari as plain text inside Markdown context. No crash, no swap. Acceptable visual. |
| Cache writes happen frequently (every overlay change) → CPU/battery cost | 500ms debounce + max 30 messages per conversation + in-memory only. Negligible cost. |
| `historyPagingItems.itemSnapshotList` access from screen is recomposed on every paging emission | Wrap in `LaunchedEffect` keyed on snapshot size to limit cache-write call frequency. |
| Cache contains stale content after a second device sends messages | This is the standard stale-while-revalidate trade-off. The 500ms paging fetch overwrites with fresh data. User-visible "old → new" is brief and bounded. |
| Lock decision wrong if first non-empty token is just punctuation/whitespace | First content evaluated against `shouldRenderAsMarkdown` which checks all chars ASCII. Punctuation is ASCII → Markdown lock. Even if subsequent tokens have non-ASCII, lock holds. No flicker. |
| New `useMarkdownLocked` field on `LocalMessage` interacts with existing copy() calls | Add nullable default. All existing `.copy()` and constructor calls compile unchanged. Verified no destructuring elsewhere in chat module. |

---

## Test plan (additions to §11)

- **Aasha-style plain ASCII test (gating criterion).** Send a message to a bot that replies in plain Hinglish ASCII with **NO** markdown syntax (no `*`, no `**`, no `#`). Expected: zero flicker at `done`. This proves the lock is correctly applied — the `done` flicker isn't about italic/bold transformation, it's about the Text→Markdown rendering-path swap itself, which fires for *any* ASCII content. If Aasha-style content still flickers after the fix, the lock is being read wrong or not propagated to the Remote path.
- Send a message to Ragini (Hinglish + roleplay `*action*`). Expected: tokens stream with literal `*` until closing `*` arrives, then in-place italic. At `done`, no visible re-styling.
- Send a message to Urvashi (Devanagari). Expected: streaming + done both Text path. No flicker.
- Send a message that contains an emoji. Expected: rendering doesn't crash; emoji visible even if slightly off in Markdown mode.
- Stream a long message (50+ tokens) in Ragini. Expected: smooth in-place markdown updates throughout.
- **Re-entry:** open Ragini → send → wait for reply → back to Inbox → tap Ragini again. Expected: chat appears instantly (from cache), no blank window. Paging completes in background, content stays stable.
- **App relaunch:** kill app → reopen → tap Ragini. Expected: first visit has the legacy ~500ms vanish (cache is gone). Subsequent navigation within same session uses cache.
- **Cross-device race:** if a second device sends a message between visits, the cache shows the prior state for ~500ms then updates to the newer page 0. Acceptable.

---

## File-by-file delta

| File | Change |
|---|---|
| `domain/models/ChatMessage.kt` (no change) | |
| `viewmodel/ConversationViewModel.kt` (LocalMessage data class) | Add `useMarkdownLocked: Boolean? = null` field |
| `viewmodel/ConversationViewModel.kt` (Token handler) | Compute lock on first non-empty buffer |
| `viewmodel/ConversationViewModel.kt` (Done handler) | Write `msg.id → lock` to `_streamMarkdownLockedRemoteIds` |
| `viewmodel/ConversationViewModel.kt` (new VM state) | `_streamMarkdownLockedRemoteIds: MutableStateFlow<Map<String, Boolean>>` |
| `viewmodel/ConversationViewModel.kt` (constructor) | `+ private val conversationContentCache: ConversationContentCache` |
| `viewmodel/ConversationViewModel.kt` (setConversationId) | Hydrate `_overlay.sent` from cache |
| `viewmodel/ConversationViewModel.kt` (init block) | Snapshot/write loop |
| `viewmodel/ConversationViewModel.kt` (new method) | `recordHistorySnapshot(items)` |
| `data/ConversationContentCache.kt` (NEW) | LRU map cache |
| `di/ChatModule.kt` | Register `ConversationContentCache` as `single`. Pass to VM. |
| `ui/conversation/ChatConversationScreen.kt` | LaunchedEffect snapshotting `historyPagingItems` → VM. Collect `streamMarkdownLockedRemoteIds`, pass to `MessagesList`. |
| `ui/conversation/ConversationMessagesList.kt` | Pass `markdownLockedOverride` to `MessageContent` |
| `ui/conversation/ConversationMessageBubble.kt` | `MessageContent`/`MessageBubble`/`RegularBubble` accept `markdownLockedOverride: Boolean?`; new rendering decision in RegularBubble |

**Total ~13 file edits, ~90 net new lines, 1 new file.**

---

## Approval gate

Per your direction: **I do not start coding until you approve.** Specifically I'd like a yes/no on:

1. The path-lock decision is computed once on the first non-empty content (not the first token if that token is empty, not on every token).
2. The lock persists across the Local→Remote swap via a ViewModel-scoped map keyed by server message id.
3. The stale-while-revalidate cache is in-memory only (no disk persistence in this PR).
4. The cache includes both `overlay.sent` AND the `historyPagingItems` snapshot.
5. Cache is bounded (20 conversations × 30 messages max) and LRU-evicted.

If those are right, I'll execute exactly this.
