# Handoff — Chat as Human (Creator Takeover)

**For:** Sarvesh
**From:** Rishi (built with Claude assistance)
**Date:** 2026-05-28
**Branch:** `rishi/test-v2-chat-url` (local only — not pushed)
**Backend:** v2 (`agent.rishi.yral.com`)
**Build target:** `prod debug` APK installed on Motorola `ZN52225232`

---

## TL;DR

A human creator who owns AI influencers (max 3) can now **take over** any conversation a user is having with their bot — reply as a real human from inside the bot's avatar/name. The user sees system banners letting them know a human has joined / left.

This is **additive only**. The existing user-to-AI chat flow is unchanged. The takeover UI replaces a previously inactive "switch to your human profile" prompt (`BotAccountConversationPrompt`) that was only shown to bot-account creators in their own conversations.

**Critical architectural decision:** This feature uses **REST polling**, not WebSocket. The mobile app currently has zero WebSocket infrastructure (no Ktor WebSocket plugin, no event bus, no reconnection layer). Rather than build that out as a prerequisite for this one feature, we extended the existing REST pattern. Backend continues to emit WebSocket events for future use; mobile ignores them. **No new mobile infrastructure was added.**

---

## What's in scope

### Three UI changes

1. **Creator-side: takeover bar inside the conversation view** (`CreatorTakeoverBar.kt`)
   - Visible only when `viewState.isBotAccount == true` (i.e., creator is operating one of their bot profiles).
   - Replaces the old `BotAccountConversationPrompt` ("Switch to your human profile to chat") that used to occupy this slot.
   - Toggle OFF (default): just shows a "Take over as [Creator Name]" button. No input.
   - Toggle ON: shows "Release control" + countdown timer (`M:SS`) + reused `ChatInputArea` for typing.

2. **Countdown timer**
   - Initial value comes from the backend's `remaining_seconds` field (POST takeover / GET status).
   - Decrements locally each second via a coroutine ticker.
   - Reconciled with the server every ~6 seconds via the status poll (so it doesn't drift, and so it picks up resets when the user sends a new message).
   - Visual states:
     - Hidden when toggle is OFF.
     - Normal style (white, 14sp) when remaining > 30 seconds.
     - Prominent style (`YralColors.Red300`, 18sp, bold) when remaining ≤ 30 seconds.
   - At 0 seconds, the next poll auto-flips the toggle OFF.

3. **User-side system messages** (`SystemBannerMessage.kt`)
   - Rendered inline in the existing message list when a message has `role="system"`.
   - Centered, italic, 13sp, lighter background (`YralColors.Neutral800 @ 0.45 alpha`).
   - Backend already persists takeover_started/ended as `role="system"` messages in the `messages` table (`app/routes/creator_takeover.py:63-70` and `:107-114`). So they come through the normal `GET /messages` paging — no extra mobile work was needed beyond rendering.

---

## Polling cadence

While the creator is on a conversation screen with `isBotAccount=true` AND has takeover ON:

| What | Interval | Implementation |
|---|---|---|
| Message refresh (pick up new user messages) | 3s | `ConversationViewModel.startTakeoverPolling()` — calls `refreshHistory()` which resets the existing paging source |
| Takeover status reconciliation | ~6s (every 2nd message-refresh tick) | Same loop calls `GetHumanCreatorTakeoverStatusUseCase` |
| Countdown display tick | 1s | Separate `takeoverCountdownJob` decrements `humanCreatorTakeoverRemainingSeconds` in state |

The polling jobs are scoped to `viewModelScope` and stop on `onCleared()` AND in `resetState()` AND when the toggle goes OFF AND when the status poll returns `active=false`.

**No polling happens** when:
- The creator is not in bot-account mode
- The toggle is OFF
- The conversation screen is not the active ViewModel
- The app is backgrounded (viewModelScope is paused — Android lifecycle behavior)

When the app foregrounds, the `LaunchedEffect(viewState.isBotAccount, viewState.conversationId)` in `ChatConversationScreen.kt` calls `refreshHumanCreatorTakeoverStatus()` to reconcile, which restarts polling if still active.

---

## API endpoints used

All on `agent.rishi.yral.com`:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/creator/conversations/{id}/human-creator-takeover` | Toggle ON. Returns `{ status, started_at, user_last_message_at, remaining_seconds }`. |
| POST | `/api/v1/creator/conversations/{id}/human-creator-release` | Toggle OFF (manual). Returns `{ status: "released" }`. |
| POST | `/api/v1/creator/conversations/{id}/human-creator-messages` | Send a message as the creator. Body `{ "content": "..." }`. Returns a `ChatMessage`. |
| GET | `/api/v1/creator/conversations/{id}/human-creator-takeover-status` | Status reconciliation. Returns `{ active, started_at, user_last_message_at, remaining_seconds }`. |
| GET | `/api/v1/creator/conversations/{id}/messages?limit=50&offset=0&order=desc` | Creator-side mirror of the user's `/messages` endpoint, used for polling. Same response shape. |

All require Bearer auth (existing ID token from preferences).

---

## Files changed (10) and added (10)

### Modified
| File | Change |
|---|---|
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/domain/models/Conversation.kt` | Added `SYSTEM("system")` to `ConversationMessageRole` enum + handle it in `fromApi`. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/models/Mappers.kt` | Added mappers for `HumanCreatorTakeoverStatusDto` and `StartHumanCreatorTakeoverResponseDto` → domain. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/ChatDataSource.kt` | Added 5 new endpoint methods to the interface (4 takeover endpoints + 1 creator-side message list). |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/ChatRemoteDataSource.kt` | Implementation of the 5 new endpoints + 5 new path constants. Follows existing `httpGet`/`httpPost` pattern verbatim. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/domain/ChatRepository.kt` | Added 5 new function signatures. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/ChatRepositoryImpl.kt` | Pass-through delegation of all 5 functions to the data source. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/di/ChatModule.kt` | Registered 4 new use cases. **Switched `ConversationViewModel` from `viewModelOf(::…)` to explicit `viewModel { ConversationViewModel(...) }`** because Koin's `viewModelOf` caps at 22 constructor params and the new constructor has 23. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/viewmodel/ConversationViewModel.kt` | Added 4 use cases to constructor. Added 5 state fields to `ConversationViewState`. Added 4 public methods (`startHumanCreatorTakeover`, `releaseHumanCreatorTakeover`, `sendAsHumanCreator`, `refreshHumanCreatorTakeoverStatus`). Added private polling job + countdown ticker job. Hooked into `resetState()` and `onCleared()`. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/ui/conversation/ConversationMessagesList.kt` | Added detection for `role == SYSTEM` in `MessageRow` — routes to `SystemBannerMessage` instead of `MessageContent`. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/ui/conversation/ChatConversationScreen.kt` | Replaced the `BotAccountPrompt` branch in `bottomAreaState` with `CreatorTakeoverBar`. Added one `LaunchedEffect` that calls `refreshHumanCreatorTakeoverStatus()` when entering a conversation as a bot account. |
| `shared/features/chat/src/commonMain/composeResources/values/strings.xml` | Added 4 string resources: `takeover_toggle_inactive`, `takeover_toggle_active`, `takeover_timer_label`, `takeover_input_placeholder`. |

### New files
| File | Purpose |
|---|---|
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/models/HumanCreatorTakeoverStatusDto.kt` | DTO for `GET .../human-creator-takeover-status`. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/models/StartHumanCreatorTakeoverResponseDto.kt` | DTO for `POST .../human-creator-takeover`. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/models/ReleaseHumanCreatorTakeoverResponseDto.kt` | DTO for `POST .../human-creator-release`. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/models/SendHumanCreatorMessageRequestDto.kt` | Request body DTO for `POST .../human-creator-messages`. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/domain/models/HumanCreatorTakeoverStatus.kt` | Domain model exposed to viewmodels. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/domain/usecases/StartHumanCreatorTakeoverUseCase.kt` | Standard `SuspendUseCase` wrapper. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/domain/usecases/ReleaseHumanCreatorTakeoverUseCase.kt` | Same. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/domain/usecases/SendHumanCreatorMessageUseCase.kt` | Same, with `(conversationId, content)` Params. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/domain/usecases/GetHumanCreatorTakeoverStatusUseCase.kt` | Same. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/ui/conversation/SystemBannerMessage.kt` | Centered italic banner composable for `role="system"` messages. |
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/ui/conversation/CreatorTakeoverBar.kt` | Toggle + countdown + reused `ChatInputArea`. |

---

## Symmetry notes (the SYMMETRY rule)

Everything follows the existing chat-feature patterns:
- **DTOs** are simple `@Serializable data class` with `@SerialName` — identical shape to `CreateConversationRequestDto`, `SendMessageRequestDto`, etc.
- **Use cases** all extend `SuspendUseCase<Param, Result>` with `exceptionType = ExceptionType.CHAT.name` and use `appDispatchers.network` — identical shape to `CreateConversationUseCase`, `MarkConversationAsReadUseCase`.
- **Data source methods** use the existing `httpGet` / `httpPost` helpers with the same Bearer-auth header pattern.
- **Path constants** live in the existing private `companion object` block.
- **Repository delegation** is one-liner pass-throughs (matches the existing pattern).
- **DI registration** uses `factoryOf(::…)` for use cases, same as every other use case.

The only file that deviates structurally is `ChatModule.kt` — it uses an explicit `viewModel { ConversationViewModel(...) }` block instead of `viewModelOf`. This is forced by Koin's 22-param cap on `viewModelOf`. The block listing is verbose but the alternative is bundling unrelated dependencies into a holder object, which would be worse for symmetry.

---

## Manual test plan on Motorola (`ZN52225232`)

### Setup
- **Account A (you, Rishi):** human creator who owns at least one AI influencer (e.g., "ChattyBot").
- **Account B:** a regular user who has at least one ongoing conversation with ChattyBot. (You can use a second device, or sign out/in to swap.)
- App is the `prod debug` APK already installed.

### Scenario 1 — Regression: existing chat still works (RUN THIS FIRST)
1. Sign in as Account B (regular user).
2. Open conversation with ChattyBot. Send "hi". Expect: AI reply arrives as normal, no visible UI change vs. before.
3. Inbox loads normally, no system banners visible in any existing conversation that has no takeover history.

**Pass criterion:** zero behavior change for the regular user path.

### Scenario 2 — Creator-side takeover happy path
1. Sign in as Account A.
2. Switch to ChattyBot profile via the existing profile switcher (this is unchanged — I didn't touch it).
3. App routes you to Inbox (existing behavior — `DefaultChatHomeComponent.kt:37`).
4. Tap a conversation. Expect: existing read-only message list, BUT instead of the old "Switch to your human profile to chat" prompt, you now see the **CreatorTakeoverBar** at the bottom: a single button labelled "Take over as ChattyBot" (the bot's display name).
5. Tap the button. Expect:
   - Toggle button changes to "Release control" with yellow border, yellow text.
   - A countdown appears next to it: "2:00" (or whatever remaining_seconds the backend returned).
   - A text input appears below with placeholder "Reply as ChattyBot…".
6. Type "hello, real human here" and tap send. Expect: message appears in the message list attributed to ChattyBot (same avatar/name flip as before).
7. Wait 90s without sending anything. Expect: countdown counts down to ~0:30 then switches to red+larger font.
8. Wait another 30s. Expect: at 0:00 (next status poll), the toggle auto-flips OFF, input disappears.

### Scenario 3 — Manual release
1. From step 5 above (takeover ON), tap "Release control".
2. Expect: button immediately flips back to "Take over as ChattyBot", input disappears, countdown disappears.
3. A backend system message "ChattyBot has left the chat" gets written, but you won't see it on the creator side until the next message refresh (or screen reopen).

### Scenario 4 — User-side system messages
1. Switch the Motorola to Account B (or use a second device).
2. Open the conversation with ChattyBot that you used in Scenario 2.
3. Expect: a centered italic banner "📣 ChattyBot, the human creator behind ChattyBot, has joined the chat. You're now talking to them directly." (the display name fields end up identical because the backend uses `inf_display_name` for both — see backend note below).
4. Expect: any messages sent by the creator during takeover appear as normal ChattyBot messages (same avatar, same name on the left).
5. Expect: a second banner "ChattyBot has left the chat" after the creator releases.

### Scenario 5 — Two-device reconciliation
1. Take over from device A.
2. Open the same conversation on device B (as the same creator). Expect: the CreatorTakeoverBar already shows the ON state with the remaining timer.
3. Release from device B. Expect: device A's next poll (within 6 seconds) flips its toggle OFF.

### Scenario 6 — Network failure on toggle
1. Turn airplane mode ON.
2. Tap "Take over as ChattyBot". Expect: button shows "starting" briefly then reverts to OFF. No crash. No partial state.

---

## Known limitations / things to improve later

1. **`refreshHistory()` every 3 seconds may cause a brief LazyColumn refresh flicker.** The existing `refreshHistory()` mechanism resets the paging source, which is heavier than strictly necessary. If you see flicker in testing, the targeted fix would be to fetch new messages directly via `chatRepository.getCreatorConversationMessagesPage(...)` and merge into the existing overlay (similar to how `systemOverlayMessagesFlow` works). I deliberately did NOT do this to keep the diff smaller and stick to one mechanism.

2. **Backend display-name quirk.** The backend uses `inf_display_name` for both the human creator and the bot in the join banner (see `app/routes/creator_takeover.py:57-62`). The banner reads "[BotName], the human creator behind [BotName], has joined the chat" — which is a backend bug, not a mobile bug. Worth a separate ticket to use `inf_parent_principal_id`'s display name once that's available.

3. **No optimistic UI for `sendAsHumanCreator`.** Today the creator types, hits send, the network round-trips, then the message shows up after `refreshHistory()` completes. The existing `_overlay.pending` pattern (used in `sendMessage` for regular users) could be reused. Out of scope for v1.

4. **Battery / bandwidth.** Polling at 3s + 6s only happens while the creator is on the conversation screen with takeover ON. Not while browsing inbox. Not while backgrounded. Should be fine for the expected usage pattern (occasional creator stepping in for a few minutes).

5. **Profile switcher untouched.** The existing profile switcher (in `ProfileMainScreen.kt`) is unchanged. The takeover feature relies on `sessionManager.isBotAccount` flipping correctly, which the existing switcher already does.

6. **WebSocket events ignored.** The backend continues to emit `human_creator_takeover_started`, `human_creator_takeover_ended`, and `new_user_message_during_takeover` via WebSocket. Mobile ignores them. When mobile gets a WebSocket layer (likely as part of Phase 2.7 SSE streaming), the polling loop in `ConversationViewModel.startTakeoverPolling()` can be swapped for event subscription — the rest of the takeover code stays unchanged.

---

## Build + install commands

```bash
cd ~/Claude\ Projects/yral-mobile
./gradlew :androidApp:assembleDebug --no-configuration-cache

# APK paths:
#   prod (agent.rishi.yral.com):  androidApp/build/outputs/apk/prod/debug/androidApp-prod-debug.apk
#   staging:                       androidApp/build/outputs/apk/staging/debug/androidApp-staging-debug.apk

adb install -r androidApp/build/outputs/apk/prod/debug/androidApp-prod-debug.apk
```

If you hit the Gobley/Rust-NDK error on first build, the NDK needs to be symlinked into the SDK path Gradle looks at:
```bash
ln -s ~/android-sdk/ndk ~/Library/Android/sdk/ndk
mkdir -p ~/.cargo/bin && ln -sf "$(rustup which rustc)" ~/.cargo/bin/rustc && ln -sf "$(rustup which cargo)" ~/.cargo/bin/cargo
```

---

## Branch state

- Branch: `rishi/test-v2-chat-url`
- Local commits: just the one-line `CHAT_BASE_URL` change to `agent.rishi.yral.com` from before this work. The takeover code is **uncommitted** in the working tree, ready for you to review and shape into commits as you see fit.
- Never pushed to origin.

When you're ready to commit, I'd suggest:
1. One commit for the data layer (DTOs, mappers, ChatDataSource, ChatRemoteDataSource, ChatRepository, ChatRepositoryImpl, use cases, DI).
2. One commit for the ViewModel changes.
3. One commit for the UI (`SystemBannerMessage`, `CreatorTakeoverBar`, `ConversationMessagesList`, `ChatConversationScreen`, strings.xml).

Total diff is ~10 modified files, ~10 new files, ~600 lines of strict code.

---

## Bug fix round 1 — 2026-05-28 (after first Motorola test)

Rishi tested the original build and surfaced 3 real bugs. Full RCA + fixes summarized below. **Only one of these is a mobile change; the other two are owned by the backend session** (see `~/Claude Projects/yral-rishi-agent/PROGRESS.md` "OPEN BUGS — Phase 1.10" section for backend details).

### What Rishi observed
1. Timer counted down even while the creator was actively typing replies — felt like the wrong reference point.
2. Timer hit 0:00 but creator could still send messages, and the toggle didn't flip OFF immediately.
3. "Anastasia Ivanova (Hindi) has left the chat" banner appeared **3 times** in a single conversation when viewed from the user side.

### Bug 1 — Timer semantics (owned by backend)

**Root cause:** backend's `remaining_seconds` is computed from `user_last_message_at`, not `creator_last_message_at`. So the timer measured **user silence**, not **creator silence**.

**Decision (Rishi):** Option A — timer represents the **creator's** response window. Resets only on creator messages. If creator goes silent for 2 minutes, AI takes back over.

**Mobile change:** none. Mobile reads whatever `remaining_seconds` the backend returns and treats it as opaque. When backend ships the semantics swap, mobile behavior automatically follows.

### Bug 2 — Timer at 0:00 but creator could still chat (mobile + backend)

**Root cause:** three compounding gaps —
- Mobile local ticker decremented `remainingSeconds` to 0 but did NOT flip `isHumanCreatorTakeoverActive` to false. That only happened on the next ~6s status poll.
- Backend auto-release sweep ran every 30 seconds, so server could be 0-30s late to set `takeover_active = FALSE`.
- During that gap, `POST .../human-creator-messages` accepted sends because `takeover_active` was still TRUE on the server.

**Mobile fix (shipped in this APK):** `ConversationViewModel.startCountdownTicker()` — when the local timer reaches 0, mobile now immediately calls `releaseHumanCreatorTakeover()`. This:
- Fires `POST .../human-creator-release` to the backend (no waiting for the sweep)
- Flips local `isHumanCreatorTakeoverActive = false` synchronously, so the input area and timer disappear within ~1 frame
- Calls `refreshHistory()` so the "left" system banner shows up in the creator's own view too

**Backend fixes (owned by backend session):**
- Drop sweep interval from 30s → 5s
- Reject `POST .../human-creator-messages` if `creator_last_message_at` is already older than 2 minutes (defense in depth, in case mobile's release call drops)

### Bug 3 — "X has left the chat" duplicated 3× (owned by backend)

**Root cause A:** `takeover_repo.activate()` uses `user_last_message_at = COALESCE(user_last_message_at, NOW())`. A fresh takeover started right after a previous one (with the user still silent) inherits the stale timestamp → auto-release sweep fires within 30s → writes another "left" message. Each cycle accumulates one more banner.

**Root cause B:** "left" system message is written from TWO backend paths (`creator_takeover.py:107-114` manual release + `main.py:135-142` sweep). Neither is idempotent — both write a fresh row with a unique ID, so mobile-side dedup can't merge them.

**Mobile change:** none. Mobile faithfully renders every `role="system"` message the backend returns. Once backend stops emitting duplicates, the banners stop duplicating.

**Backend fixes (owned by backend session):** see `PROGRESS.md` for the full SQL. Summary:
- Migration: add `creator_last_message_at` column, set to NOW on every fresh `activate()`
- `deactivate()` uses `UPDATE ... WHERE active=TRUE RETURNING id` — only write the "left" banner if a row was actually flipped
- Sweep additionally guards: don't write a "left" if one was already written in the last 10s

### Files changed in this fix round (1 file)
| File | Change |
|---|---|
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/viewmodel/ConversationViewModel.kt` | `startCountdownTicker()` calls `releaseHumanCreatorTakeover()` when `next == 0`. ~6 lines added. |

### Retest plan (after backend ships)

Same three scenarios as the original handoff, but with these new checks:

**Scenario 2 (creator-side takeover) — additions:**
- Take over and DON'T type anything. Send a message from a second device as the user. Expect: timer keeps counting down regardless of the user message (because Option A — only creator's activity resets it).
- Take over and immediately type a message. Expect: timer resets to 2:00 the moment your message is sent.
- Wait until 0:00. Expect: the toggle flips OFF **within 1 second** of 0:00 (was previously 0-36s delay). Input area disappears. The "has left the chat" banner appears in the message list immediately on the next refresh.

**Scenario 3 (user-side system messages) — additions:**
- Take over → release → take over → release. Open the conversation as the user. Expect: exactly **one** "joined" and **one** "left" per cycle. Total 4 banners across 2 cycles, not more.

**Scenario 6 (network failure on toggle) — unchanged.**

### State of this APK (2026-05-28)

- Mobile fix for Bug 2 is **in the installed APK on `ZN52225232`**.
- Mobile fix for Bug 1 = nothing to do, fully backend.
- Mobile fix for Bug 3 = nothing to do, fully backend.

**Do not retest the previously-broken scenarios on this APK until the backend session has shipped its fixes.** The mobile change alone won't fully resolve any of the three bugs — Bug 2 will still show server-side staleness on the user side, Bugs 1 and 3 are entirely backend.

---

## Latency fix round — 2026-05-28 (after round-1 retest)

Rishi reported that creator-side sends were slow: send button greyed correctly, but the message took up to ~1 second to appear on screen. RCA:

- `sendAsHumanCreator` did the POST (~200-500ms), then called `refreshHistory()` which **reset the paging source and fired a second `GET /messages` call** (~200-500ms more). Two back-to-back round trips before the bubble appeared.
- No optimistic UI — the message was completely invisible until both calls completed. The regular user `sendMessage` flow has had optimistic UI since day one via `_overlay.pending`; I just hadn't carried that pattern over.

**Fix shipped in this APK (one file, ~50 lines changed):**

`ConversationViewModel.sendAsHumanCreator()` now:
1. Builds a `LocalMessage(role=ASSISTANT, status=SENDING)` immediately and adds it to `_overlay.pending` **before** the network call. This makes the bubble appear within one frame (~16ms).
2. On POST success: removes the pending entry and adds a `SentMessage` to `_overlay.sent` with the real server-returned `ChatMessage`. No `refreshHistory()` — the next 3s polling tick will hydrate the message into the paged history, and the existing `loadedMessageIds` dedup will remove the overlay copy automatically.
3. On POST failure: marks the pending message as `LocalMessageStatus.FAILED` (existing red-error styling kicks in automatically).

Same pattern as the regular `sendMessage` flow (see `ConversationViewModel.kt:1042`). Symmetry preserved.

### Expected behavior now
- **Tap send → bubble visible: ~16ms** (one frame). Was 400-1000ms.
- **Button stays greyed for the POST round trip** (~200-500ms), then re-enables. Prevents double-sends.
- **If the POST fails**, the bubble turns red with the existing "Message Failed. Tap to resend" affordance. ⚠️ **Known limitation:** tapping retry currently no-ops for creator messages because the existing `retry()` uses `draftForRetry` which I left null for creator sends. To recover from a failure, the creator has to retype. Acceptable for v1; cleaner fix would be a dedicated `retryCreatorMessage(localId)` path or storing the content for retry.

### Brief duplicate-bubble race (rare, self-correcting)
If the 3s polling refresh fires *during* a POST's round trip, the paged result could briefly load the real message while the optimistic pending is still in `_overlay.pending`. The user would see their message twice for ~half a second. Self-corrects when the POST completes (pending → sent → dedup'd by loadedMessageIds). Not worth fixing now; would require either pausing polling during an active send, or content/timestamp-based dedup between pending and remote.

### Files changed in this round (1 file)
| File | Change |
|---|---|
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/viewmodel/ConversationViewModel.kt` | `sendAsHumanCreator()` rewritten to use optimistic overlay; removed the `refreshHistory()` call. ~50 lines net. |

---

## Flicker fix — 2026-05-28 (after latency-round retest)

Rishi reported visible flicker during creator sends — the "rare race" I flagged in the latency fix round was actually common enough to be noticeable.

**Root cause:** the polling loop in `startTakeoverPolling()` calls `refreshHistory()` every 3 seconds unconditionally. If that tick fired between the moment the creator pressed send (optimistic pending added) and the moment the POST returned (pending replaced with sent), the paging refresh could pull the real message from the backend in parallel — so for ~½ second the user saw their bubble twice.

**Fix (1 line + comment):** in `startTakeoverPolling()`, skip the `refreshHistory()` call when `isHumanCreatorMessageSending` is true. Once the POST completes (success or failure), the flag flips false and the next polling tick refreshes normally.

```kotlin
if (!_viewState.value.isHumanCreatorMessageSending) {
    refreshHistory()
}
```

**Trade-off:** if a NEW user message arrives during the same window (creator's POST in flight), the creator won't see it until the next 3s tick AFTER their send completes. So worst-case delay for the creator seeing an incoming user message goes from 3s → ~3.5s. Acceptable. The status poll (every ~6s) still runs unconditionally so timer reconciliation isn't affected.

### Files changed in this round (1 file)
| File | Change |
|---|---|
| `shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/viewmodel/ConversationViewModel.kt` | One-line guard in `startTakeoverPolling()` to skip `refreshHistory()` while a creator-send is in flight. |
