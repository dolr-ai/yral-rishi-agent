# PLAN — Chat as Human (Creator Takeover)

**Date:** 2026-05-28
**Status:** DRAFT for Rishi's approval — DO NOT IMPLEMENT YET
**Branch:** local only, never push

---

## What's already there (good news)

The codebase already has most of the scaffolding for this feature. We are *extending*, not building from scratch.

- **`isBotAccount` session flag exists.** When a human creator switches to one of their AI influencer profiles, `SessionManager.isBotAccount` flips to `true`. The whole app already branches on this.
- **The "creator views user-bot conversation" screen is already the same screen as the user's chat — just with the input hidden.** Specifically, `ChatConversationScreen.kt:337` returns `true` (blocked) when `isBotAccount`, so `sendMessage` never fires. `ConversationMessagesList.kt:80` already flips message rendering (USER messages from the user appear on the LEFT, ASSISTANT/bot messages appear on the RIGHT — which is the visual perspective the creator should see).
- **Chat home already routes bot-account sessions straight to Inbox** (`DefaultChatHomeComponent.kt:37`).
- **The text input component (`ConversationInputArea.kt`) is fully reusable.** Pure stateless composable: takes `input`, `onInputChange`, `onSendClick` callbacks. We can drop it into a new place.
- **The data layer pattern is symmetric** (`ChatRemoteDataSource.kt`) — adding 4 new POST/GET endpoints follows the existing pattern exactly.
- **DI is centralized** in `ChatModule.kt` — adding new use cases is a one-liner each.

## What's NOT there (the critical finding)

> **The mobile app has ZERO WebSocket code anywhere in the entire repo.**
> I grepped the full tree, the http lib, libs.versions.toml — no Ktor WebSocket plugin, no `wss://`, no event subscription system.

Today, messages arrive in the chat screen via **REST pagination only**. There is no real-time push. Even the existing `ChatUnreadRefreshSignal` (which sounds WebSocket-ish) is just an internal in-app SharedFlow that triggers a refresh — nothing leaves the app.

This matters because the feature spec relies on 4 WebSocket events:
- Creator side: `new_user_message_during_takeover`, `human_creator_takeover_ended`
- User side: `human_creator_takeover_started`, `human_creator_takeover_ended`

**Rishi must pick one of three options before I write code.** See the next section.

---

## Decision needed: the WebSocket gap

### Option A — Build the WebSocket client first (correct, slower)

Add Ktor WebSocket plugin to `shared/libs/http`, build a connection manager that opens a single `wss://agent.rishi.yral.com/ws/...` connection scoped to the app's foreground lifecycle, route incoming events through a `ChatRealtimeEventBus` (similar to `ChatUnreadRefreshSignal` but for backend pushes). Then implement the takeover UI on top of that.

- **Scope:** ~800–1200 new lines including reconnect/backoff/auth/lifecycle.
- **Risk:** This is a much bigger change than the takeover UI itself. Sarvesh will inherit a whole new networking layer. Symmetry concern: where does the WebSocket live — `shared/libs/http`, `shared/libs/realtime`, or `shared/features/chat/data`? Needs a real architecture call, not a 1-day patch.
- **Verdict:** Correct long-term, but couples a tiny feature to a foundational change. **I do not recommend doing this as part of the takeover work.**

### Option B — Polling fallback (pragmatic, build the UI now)

Implement the takeover UI exactly as specified, but instead of subscribing to WebSocket events, poll `GET /api/v1/creator/conversations/{id}/human-creator-takeover-status` every 3 seconds while the relevant screen is in the foreground. Stop polling when screen leaves foreground.

For the user-side system messages, when a user opens a chat we already make the standard `GET /messages` paging call — the backend can return any active-takeover banner as a special system-typed message in the message stream, so the user sees the banner without WebSocket.

- **Scope:** Takeover UI only, ~250–400 lines.
- **Trade-off:** 3-second latency on the user seeing "creator joined" / on the creator seeing new user messages. Acceptable for v1 — the use case is occasional creator stepping in, not high-frequency.
- **Trade-off:** Battery + bandwidth from polling. Mitigated by only polling when the conversation screen is foregrounded.
- **Verdict:** **My recommendation.** Ships the feature now, defers the WebSocket architecture decision to when it's truly needed (probably when SSE streaming lands in Phase 2.7).

### Option C — Ship UI without realtime, wait for WebSocket

Build only the toggle button + manual send. No countdown timer auto-flip, no incoming-user-message refresh, no user-side system messages. The creator manually toggles off, reloads the screen to see new user messages.

- **Scope:** ~150 lines.
- **Trade-off:** Most of the feature value is lost. The countdown and "creator joined" banner are the magic. Not worth shipping like this.
- **Verdict:** Not recommended.

---

## The plan (assuming Option B — polling)

### Files I will modify (10 files) and create (10 files)

#### Data layer (3 modified, 5 new)

| File | Change |
|---|---|
| **MODIFY** `shared/features/chat/.../data/ChatRemoteDataSource.kt` | Add 4 functions: `startTakeover`, `endTakeover`, `sendCreatorMessage`, `getTakeoverStatus`. Follow existing `httpPost`/`httpGet` pattern (lines 99-116 are the template). |
| **MODIFY** `shared/features/chat/.../domain/ChatRepository.kt` | Add the same 4 function signatures to the interface. |
| **MODIFY** `shared/features/chat/.../data/ChatRepositoryImpl.kt` | Delegate the 4 new functions to the data source. |
| **NEW** `shared/features/chat/.../data/models/HumanCreatorTakeoverStatusDto.kt` | DTO: `isActive: Boolean`, `userLastMessageAtIso: String?`, `expiresInSeconds: Int`. |
| **NEW** `shared/features/chat/.../data/models/StartTakeoverResponseDto.kt` | DTO mirroring the backend response. |
| **NEW** `shared/features/chat/.../data/models/SendCreatorMessageRequestDto.kt` | DTO: `content: String`, `clientMessageId: String`. |
| **NEW** `shared/features/chat/.../domain/models/HumanCreatorTakeoverStatus.kt` | Domain model: `isActive`, `userLastMessageAt: Instant?`, computed `remainingSeconds: Int`. |
| **NEW** `shared/features/chat/.../data/models/Mappers.kt` *(addition)* | Mappers for the new DTOs → domain models. (I'll add to existing Mappers.kt — not a new file. Strike this from "new" count: 4 new DTOs total.) |

#### Use cases (4 new)

| File | Purpose |
|---|---|
| **NEW** `shared/features/chat/.../domain/usecases/StartHumanCreatorTakeoverUseCase.kt` | Calls `POST .../human-creator-takeover` |
| **NEW** `shared/features/chat/.../domain/usecases/EndHumanCreatorTakeoverUseCase.kt` | Calls `POST .../human-creator-release` |
| **NEW** `shared/features/chat/.../domain/usecases/SendHumanCreatorMessageUseCase.kt` | Calls `POST .../human-creator-messages` |
| **NEW** `shared/features/chat/.../domain/usecases/GetHumanCreatorTakeoverStatusUseCase.kt` | Calls `GET .../human-creator-takeover-status` |

#### DI (1 modified)

| File | Change |
|---|---|
| **MODIFY** `shared/features/chat/.../di/ChatModule.kt` | Register the 4 new use cases via `factoryOf(::…)` next to existing ones (lines 50-56 region). |

#### ViewModel (1 modified)

| File | Change |
|---|---|
| **MODIFY** `shared/features/chat/.../viewmodel/ConversationViewModel.kt` | Add takeover state to `ConversationViewState` (`isTakeoverActive`, `takeoverRemainingSeconds`). Add methods: `toggleTakeover()`, `sendAsCreator(text)`, `refreshTakeoverStatus()`. Add a polling loop that fires every 3s when `isBotAccount && observingTakeover`. When polling returns inactive, flip `isTakeoverActive` to false and stop the loop. Also add `setSystemBannerMessage(text: String?)` that writes into the existing `systemOverlayMessagesFlow` (line 193 — the mechanism already exists). |

#### UI (1 modified, 2 new)

| File | Change |
|---|---|
| **MODIFY** `shared/features/chat/.../ui/conversation/ChatConversationScreen.kt` | When `viewState.isBotAccount`, render the new `CreatorTakeoverBar` below the messages list. Inside: the toggle button and (when ON) the timer + a `ConversationInputArea` whose `onSendClick` calls `viewModel.sendAsCreator(...)`. |
| **NEW** `shared/features/chat/.../ui/conversation/CreatorTakeoverBar.kt` | Composable. Renders the toggle button. When ON, shows countdown timer + reuses `ConversationInputArea` for typing. Visual states: hidden (`> 60s`), normal (`31–60s`), prominent red+large (`≤ 30s`). |
| **NEW** `shared/features/chat/.../ui/conversation/SystemBannerMessage.kt` | Composable for the centered, italic, light-background "📣 X has joined the chat" banner. Used by `ConversationMessagesList` when message has the system-banner marker. |

#### Message list (1 modified)

| File | Change |
|---|---|
| **MODIFY** `shared/features/chat/.../ui/conversation/ConversationMessagesList.kt` | In `MessageRow`, detect system-banner messages (we mark them via a special `id` prefix like `system-takeover-*` written by the viewmodel into the existing system overlay). Render via `SystemBannerMessage` instead of `MessageContent`. |

#### String resources (1 modified)

| File | Change |
|---|---|
| **MODIFY** `shared/features/chat/.../composeResources/values/strings.xml` | Add 6 strings: `takeover_toggle_inactive` ("Take over as %1$s"), `takeover_toggle_active` ("Release control"), `takeover_timer_label` ("%1$s remaining"), `takeover_banner_joined` ("📣 %1$s, the human creator behind %2$s, has joined the chat. You're now talking to them directly."), `takeover_banner_left` ("%1$s has left the chat."), `takeover_input_placeholder` ("Reply as %1$s..."). |

### Total surface area
- **10 files modified**
- **10 new files** (4 DTOs, 4 use cases, 2 composables)
- **~400 lines of strict code**

---

## UI mockup (text-art)

### Creator-side: takeover bar inside conversation view

```
┌──────────────────────────────────────────────────────────┐
│  ← [bot avatar]  ChattyBot                  ⋮            │  ← existing ChatHeader
├──────────────────────────────────────────────────────────┤
│                                                          │
│   user (LEFT side):     "hey bot, are you real?"         │  ← isBotAccount flips
│                                                          │     LEFT/RIGHT today
│   bot (RIGHT side):     "yeah I'm super real :)"         │
│                                                          │
│   user (LEFT side):     "you sound like a chatbot lol"   │
│                                                          │
│   ─── system banner (centered, italic, lighter bg) ───   │
│        📣 You took over the chat at 4:32 PM              │  ← (optional courtesy
│   ────────────────────────────────────────────────────   │     banner shown only
│                                                          │     to the creator)
└──────────────────────────────────────────────────────────┘
│  [ NEW — CreatorTakeoverBar ]                            │
│  ┌────────────────────────────────────────────────────┐  │
│  │  ⚪  Take over as Sarvesh                          │  │  ← toggle OFF state
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘

                              ↓ creator taps toggle ↓

┌──────────────────────────────────────────────────────────┐
│  [ CreatorTakeoverBar — toggle ON ]                      │
│  ┌────────────────────────────────────────────────────┐  │
│  │  🔵  Take over as Sarvesh        ⏱  1:47          │  │  ← timer, normal style
│  └────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Reply as Sarvesh...                          [↑] │  │  ← reused ConversationInputArea
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘

                              ↓ when timer ≤ 30s ↓

│  ┌────────────────────────────────────────────────────┐  │
│  │  🔵  Take over as Sarvesh        ⏱  0:24  [RED]   │  │  ← timer prominent red+large
│  └────────────────────────────────────────────────────┘  │
```

### User-side: in-chat system messages

```
┌──────────────────────────────────────────────────────────┐
│  ← [bot avatar]  ChattyBot                  ⋮            │
├──────────────────────────────────────────────────────────┤
│                                                          │
│   user (RIGHT):    "hey bot, are you real?"              │
│   bot (LEFT):      "yeah I'm super real :)"              │
│                                                          │
│   ──── (centered, italic, lighter background) ────       │
│     📣 Sarvesh, the human creator behind ChattyBot,      │  ← human_creator_takeover_started
│     has joined the chat. You're now talking to them      │
│     directly.                                            │
│   ───────────────────────────────────────────────────    │
│                                                          │
│   bot (LEFT):      "actually no, I'm Sarvesh 👋"         │  ← arrives via normal
│                                                          │     message channel,
│                                                          │     same avatar/name
│   user (RIGHT):    "wait what?? you're the real owner?"  │
│                                                          │
│   bot (LEFT):      "yep, just wanted to say hi"          │
│                                                          │
│   ──── (centered, italic, lighter background) ────       │
│     Sarvesh has left the chat.                           │  ← human_creator_takeover_ended
│   ───────────────────────────────────────────────────    │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## Event flow (Option B — polling)

### Creator side (when on conversation view with `isBotAccount=true`)

```
┌─ ConversationScreen mounted ─┐
│                              │
│ isBotAccount? ───── no ──→ existing behavior (chat normally)
│      │ yes                   │
│      ▼                       │
│ render CreatorTakeoverBar    │
│ (toggle OFF, no timer)       │
└──────────────────────────────┘
        │ user taps toggle ON
        ▼
┌─ POST .../human-creator-takeover ─┐
│  backend returns { user_last_message_at } │
└───────────────────────────────────┘
        │
        ▼
┌─ Start polling loop (every 3s) ─┐
│  GET .../human-creator-takeover-status  │
│     ├─ isActive=true  → recompute remainingSeconds, update timer
│     └─ isActive=false → flip toggle OFF, hide input, stop polling
└─────────────────────────────────┘
        │ creator types + sends
        ▼
POST .../human-creator-messages  → message appears in chat via normal /messages reload
                                    (also the GET-status response refreshes the message list)
        │ creator taps toggle OFF manually
        ▼
POST .../human-creator-release  → flip toggle OFF, hide input, stop polling
```

### User side (always, on regular chat view)

```
┌─ ConversationScreen mounted (isBotAccount=false) ─┐
│                                                   │
│  Existing /messages paging call                   │
│                                                   │
│  Backend returns messages in chronological order, │
│  including system-typed messages with content:    │
│    - "human_creator_takeover_started"             │
│    - "human_creator_takeover_ended"               │
│  These are tagged with a special message_type     │
│  and the role field set to "system".              │
│                                                   │
│  ConversationMessagesList detects role=="system"  │
│  and renders SystemBannerMessage instead of       │
│  the standard MessageContent.                     │
└───────────────────────────────────────────────────┘

For real-time arrival of new banner messages while user is on screen:
  → relies on a 5–10s background refresh of the latest page
    (or, future WebSocket, when Option A is built).
```

**Backend coordination required for Option B:** The backend needs to write the takeover_started / takeover_ended events into the `messages` table as `role="system"` rows so they come through the normal `/messages` endpoint. If the backend currently only emits these via WebSocket, we'll need a backend change to persist them too. **Question for Rishi: does the backend already do this, or do I need to ask the backend session to add it?**

---

## Edge cases I've identified

1. **App backgrounded mid-takeover.** Polling stops (it's foreground-scoped). When app foregrounds, we call `GET .../human-creator-takeover-status` once to reconcile, then resume polling if still active.

2. **Backend timer expired but mobile clock is wrong.** We compute `remainingSeconds` from `(now - user_last_message_at)`. If device clock is off, our display will lie. Mitigation: prefer the backend's own `expires_in_seconds` field in the status response and use device clock only for the per-second tick between polls.

3. **Toggle ON request fails (network).** Show inline error, leave toggle OFF. No partial state. Same for toggle OFF request — but if OFF fails, we still hide the UI locally (creator's intent was to stop), and rely on backend auto-release.

4. **Send message during takeover fails.** Show retry on the failed message bubble — reuse the existing `LocalMessageStatus.FAILED` flow that the user-side chat already has (`ConversationMessagesList.kt:106`).

5. **Two devices logged in as the same creator.** If creator takes over on device A, then opens the same conversation on device B, device B's GET-status should show active. Device B's toggle should render in the ON position with the remaining timer. This works naturally with Option B (polling).

6. **User sends a message while creator has takeover.** Backend resets `user_last_message_at`, so the next poll returns a refreshed timer. The new user message arrives in the creator's view on the next message list refresh.

7. **Polling battery drain.** Mitigation: only poll while the specific conversation view is foregrounded AND `isBotAccount` AND toggle ON. Cancel via `viewModelScope` automatically. Use `Lifecycle.repeatOnLifecycle(STARTED)` equivalent.

8. **The "📣 joined" banner showing twice.** If backend persists the banner as a message AND also pushes via future WebSocket, we'd render it twice. For Option B we only render it from the messages stream — no duplicate. (When Option A lands, dedupe by message id.)

9. **isBotAccount changes while screen is open** (creator switches back to their human profile). The `viewState.isBotAccount` is observed reactively — the CreatorTakeoverBar will disappear on the next recomposition. We should also auto-release the takeover if active. Add a `LaunchedEffect(viewState.isBotAccount)` that calls release if it flips from `true` to `false` while takeover is active.

10. **Symmetry concern (Rishi's #1 rule).** Every existing chat use case is a single-purpose UseCase class registered the same way in DI. The 4 new use cases follow this exact pattern — no special-casing.

---

## Test plan on Motorola (after build)

### Setup
- Account A (Rishi): human creator who owns at least one AI influencer (ChattyBot).
- Account B: a regular user who has at least one ongoing conversation with ChattyBot.
- Both accounts installed on the Motorola — switch via the existing logout/login flow, OR use two devices if available.

### Scenario 1: Existing chat must still work (regression test FIRST)
1. Sign in as Account B (regular user).
2. Open conversation with ChattyBot.
3. Send a message. Expect: AI replies as before. No UI change visible to the user. ✅ proves additive-only.

### Scenario 2: Creator takeover flow
1. Sign in as Account A (creator).
2. Switch to ChattyBot profile via the existing profile switcher.
3. Open Inbox → tap Account B's conversation.
4. Verify: existing read-only view still works, messages still flip LEFT/RIGHT correctly.
5. Verify: new CreatorTakeoverBar visible at bottom with toggle OFF, no input.
6. Tap toggle ON. Verify: input appears, timer starts at 2:00 and counts down.
7. Type "hello from the real human" and tap send. Verify: message appears in chat, attributed to ChattyBot (same avatar).
8. Wait 30 seconds. Verify: timer style switches to red/larger at ≤30s.
9. Tap toggle OFF manually. Verify: input disappears, timer disappears.

### Scenario 3: User sees system messages
1. While Account A has takeover ON, switch the Motorola to Account B and open the same conversation.
2. Verify: a system banner "📣 Rishi, the human creator behind ChattyBot, has joined the chat" is visible in the message list.
3. Verify: subsequent messages from the creator appear styled as ChattyBot (same avatar/name).
4. When Account A's takeover ends, refresh the user's view. Verify: a second banner "Rishi has left the chat" appears.

### Scenario 4: Auto-release on timer expiry
1. Account A takes over. Don't send any messages. Wait 2 minutes.
2. Verify: at 2:00 elapsed, the creator's toggle auto-flips OFF, input disappears.

---

## Open questions for Rishi

1. **Which option (A, B, or C) above?** I strongly recommend **B (polling)** so this feature can ship in 1 day instead of pulling in a full WebSocket buildout.
2. **Does the backend persist takeover_started/ended as `role="system"` messages in the `messages` table?** If not, the user-side banner won't show without WebSocket. Confirm with the backend session or check `app/routes/chat_v3.py` / `app/repositories/message_repo.py`.
3. **Profile switcher:** I assume one exists today (since `isBotAccount` flips work). I have NOT looked at the profile switcher UI yet in detail — confirm it's working and I don't need to touch it. The Explore agent flagged `ProfileMainScreen.kt` as the likely location but I haven't verified.
4. **String resource location:** I assumed strings live in the chat feature module's `composeResources/values/strings.xml`. I should confirm the actual path before adding strings.
5. **Toggle visual:** Material `Switch`, custom rounded button, or matched to an existing design system component? Want me to match the existing chat-screen button styling.

---

## What I will NOT do

- ❌ Not pushing anything to git remote. Local-only changes per the rules.
- ❌ Not building a WebSocket client unless explicitly chosen (Option A).
- ❌ Not modifying the user chat send/receive code paths (additive only).
- ❌ Not changing the existing profile switcher.
- ❌ Not committing this plan doc to git — it's a working document.
