# Human-to-Human (H2H) chat — implementation plan

**Branch:** `rishi/h2h-chat`, branched off `origin/main` at 05229d97.
**Status:** Plan only. No code. Awaiting Rishi approval before implementation.
**Companion docs:**
- Discipline: [`MOBILE-EXPERT-LESSONS.md`](./MOBILE-EXPERT-LESSONS.md) on `rishi/sse-streaming` (will arrive on main when PR [#1173](https://github.com/dolr-ai/yral-mobile/pull/1173) merges). Read P1, P2, P4 before touching code on this branch.
- SSE work: PR [#1173](https://github.com/dolr-ai/yral-mobile/pull/1173) is dormant-by-default and shares the chat module. Section 12 below covers rebase/merge coordination.

## 1. What this PR does (product)

Adds Human-to-Human direct messaging to the mobile app, behind `ChatFeatureFlags.Chat.H2hChatEnabled` (default OFF). When the flag is on:

- Every **other-user profile** (not bot profile) shows a "Send Message" button.
- Tapping it creates (or opens) a 1:1 H2H conversation and routes to the chat screen.
- The chat screen renders the same way as AI chat — same bubbles, same input, same scroll — minus AI-specific affordances (no streaming cursor, no "Try Again" retry on errors).
- H2H conversations appear in the same inbox list as AI conversations (Instagram-DM style; one unified list).

## 2. What's already there (don't re-invent)

The Day 8 backend (PR #158) was paired with anticipatory mobile scaffolding. Specifically:

- `Conversation.conversationUser: ConversationUser?` already exists ([`shared/features/chat/.../domain/models/Conversation.kt`](shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/domain/models/Conversation.kt)). When non-null, it carries `principalId`, `username`, `profilePictureUrl`.
- `ConversationDto.user: ConversationUserDto?` and the mapper that materializes the domain `ConversationUser` already exist ([`data/models/ConversationDto.kt`](shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/models/ConversationDto.kt) and [`Mappers.kt`](shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/models/Mappers.kt)).
- The inbox `ConversationListItem` already prefers `conversationUser.profilePictureUrl` and `conversationUser.username` over the influencer fields when both are present ([`ui/inbox/ConversationListItem.kt`](shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/ui/inbox/ConversationListItem.kt)).
- Navigation routes via `OpenConversationParams` + a Decompose `StackNavigation<Config>` in `DefaultChatComponent` ([`nav/DefaultChatComponent.kt`](shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/nav/DefaultChatComponent.kt)).
- The inbox query passes `influencerId = null` to its paging source, so it lists **all** conversations for the user, not just AI ones ([`InboxViewModel.kt`](shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/viewmodel/InboxViewModel.kt)).

What does NOT exist yet:
- No `conversation_type` field anywhere in DTOs or domain — the backend now exposes `"human_chat"` and we have to deserialize it.
- No "Send Message" button on user profile screens.
- No create-H2H-conversation use case (`POST /api/v1/chat/human/conversations`).
- No H2H send-message path (`POST /api/v1/chat/human/conversations/{id}/messages`).
- No `H2hChatEnabled` feature flag.
- No H2H awareness in `ConversationViewModel` (subscription / welcome / influencer-specific machinery runs unconditionally today).

## 3. Architecture decision — reuse vs. parallel ViewModel

**Decision: reuse `ConversationViewModel` with branches off a new `ConversationKind` field.** A parallel `H2HConversationViewModel` would duplicate the overlay/paging/send queue/optimistic-Local infrastructure — none of which is AI-specific — and would split future bug-fix surface area in half.

The H2H branches are mostly skip-conditions:

- Don't fetch the influencer.
- Don't load subscription products / IAP state / welcome message.
- Don't run takeover polling.
- Use `sendMessageUseCase`'s H2H sibling instead of the AI one.
- Read participant info from `conversationUser` instead of `influencer`.

`ConversationKind` is added as a sealed type so the compiler enforces exhaustive handling at branch points:

```kotlin
sealed interface ConversationKind {
    data object Ai : ConversationKind          // influencer != null, conversationUser == null
    data class Human(                          // conversationUser != null, influencer == null
        val participantPrincipalId: String,
    ) : ConversationKind
}
```

`ConversationViewState` carries `conversationKind: ConversationKind`, derived from the loaded `Conversation` (presence of `conversationUser` vs `influencer`, cross-checked against the new `conversationType` field — see §4).

## 4. Backend wire integration

### 4.1 Endpoints (deployed at `chat-ai.rishi.yral.com` per Day 8, accessed via the existing `CHAT_BASE_URL`)

| Method | Path | Body | Notes |
|---|---|---|---|
| `POST` | `/api/v1/chat/human/conversations` | `{ "participant_id": "<other principal_id>" }` | Returns `Conversation` JSON. Idempotent — if a conversation between these two users already exists, returns it instead of creating a duplicate. |
| `GET` | `/api/v1/chat/human/conversations` | — | Lists H2H conversations for the current user. **We may not need this directly** — the existing combined inbox `GET /conversations` already surfaces H2H conversations once we add `conversation_type` parsing. Confirm with backend whether the combined endpoint is canonical. |
| `POST` | `/api/v1/chat/human/conversations/{id}/messages` | Same shape as `SendMessageRequestDto` minus `is_streaming` and `is_safety_fallback` | Returns `SendMessageResponseDto` shape. |

### 4.2 DTO changes

- `ConversationDto`: add `@SerialName("conversation_type") val conversationType: String? = null`. Default-null-handle so older inbox responses (pre-Day-8) don't crash. Domain `Conversation` gains `conversationType: String?` carried through.
- `Mappers.kt`: `ConversationDto.toDomain()` already maps `user → conversationUser`. Extend it to carry `conversationType`. **Critical**: when `conversationType == "human_chat"`, `influencer` may be `null` in the DTO. Today the mapper *throws* if `influencer` is missing — that path needs to be relaxed conditionally on the type.

### 4.3 Use cases (new)

- `CreateHumanConversationUseCase` — wraps `POST /api/v1/chat/human/conversations`. Returns the domain `Conversation`. Both "created" (201) and "already exists" (200) are success cases.
- `SendHumanMessageUseCase` — wraps `POST /api/v1/chat/human/conversations/{id}/messages`. Same domain return type as the existing `SendMessageUseCase` (`SendMessageResult`).

The existing `SendMessageUseCase` stays AI-only. `ConversationViewModel`'s send routing picks one based on `viewState.conversationKind`.

### 4.4 Repository

Extend `ChatRepository` (interface) with the two new methods. The implementation (`ChatRepositoryImpl`) gets new methods on `ChatDataSource` and `ChatRemoteDataSource` for the H2H paths. The H2H paths are URL-only differences from the AI paths — no body-shape changes other than dropping the streaming-related fields.

## 5. ViewModel changes (`ConversationViewModel`)

### 5.1 State carry

Add to `ConversationViewState`:

```kotlin
val conversationKind: ConversationKind = ConversationKind.Ai,
val participantUser: ConversationUser? = null,   // populated for Human conversations
val isH2hChatEnabled: Boolean = false,            // feature flag, gating UI affordances
```

`conversationKind` is set in `initializeFromInbox(...)` and `initializeForChatWall(...)` based on the loaded conversation. `participantUser` mirrors `Conversation.conversationUser` for H2H, otherwise null. `isH2hChatEnabled` is read from `FeatureFlagManager` in the VM constructor, same pattern as `isSseStreamingEnabled` / `isChatAsHumanCreatorEnabled`.

### 5.2 Send routing

```kotlin
private fun routeSendMessage(draft: SendMessageDraft) = when (val k = viewState.value.conversationKind) {
    ConversationKind.Ai -> sendMessageUseCase(...)             // existing path
    is ConversationKind.Human -> sendHumanMessageUseCase(...)  // new path
}
```

The optimistic Local + overlay + paging + retry-on-failure infrastructure is shared (and stays in place).

### 5.3 Skip branches for H2H

Conditional `if (kind is ConversationKind.Human) return` early-outs in:

- `loadInfluencer(...)` / influencer-fetch paths
- `loadSubscriptionProducts(...)` / IAP machinery
- Welcome-message setup
- Takeover polling startup

Telemetry events get a `conversationKind` parameter or are skipped for H2H if they're influencer-specific.

### 5.4 Retry affordance

**Spec note (open question for Rishi):** the spec says "no AI-specific affordances (no 'Try Again' on errors, no streaming cursor)". Today, the legacy non-streaming send path DOES render a retry button on failed user messages — that's a non-AI feature shared with AI chat. Strict reading of the spec is that H2H gets no retry at all; failed messages stay failed and the user retypes. **Open question §11.1.**

## 6. UI changes

### 6.1 Profile screen — "Send Message" button

Location: `ProfileMainScreen.kt`. Add a CTA in the profile header area, alongside Follow/Unfollow. Gated by `isH2hChatEnabled` (flag off → button hidden entirely, no surprise UX exposure).

On tap:

1. Set local "creating" loading state on the button.
2. Call `createHumanConversationUseCase(participantPrincipalId = profileOwner.principalId)`.
3. On success → `component.openConversation(OpenConversationParams(conversationId = created.id, participantPrincipalId = profileOwner.principalId, ...participant display fields))`.
4. On failure → toast with the mapped error string; keep the user on the profile screen.

The followers/following list rows (`FollowersBottomSheet.FollowerRow`) intentionally do NOT get the button in v1 — keep entry single-source-of-truth.

### 6.2 `OpenConversationParams` extension

Today's params carry `influencerId, conversationId, userId, username, displayName, avatarUrl, ...`. Add:

```kotlin
val participantPrincipalId: String? = null,
val conversationKindHint: ConversationKind? = null,   // optional; the VM will re-derive after load anyway
```

`conversationKindHint` is just an optimization so the screen can render the right empty-state ("Start a chat with Alice") before the network load returns the canonical `Conversation`.

### 6.3 Chat screen — header

`ChatConversationScreen` currently renders the influencer's avatar + name in the header. For H2H, render `participantUser.profilePictureUrl` / `participantUser.username` (with a username fallback if `username == null`). Decision happens off `viewState.conversationKind` — one `when` at the header composable's top.

### 6.4 Chat screen — bubble rendering

The existing `MessageRow.isUser` derives from `role == ConversationMessageRole.USER`. The backend's H2H message DTO uses the same `role` field but maps `sender_id == current_user_principal_id → USER` and the other participant → ASSISTANT. **Confirm with backend** that this convention holds — open question §11.2.

Assuming it does: no changes to `ConversationMessageBubble.kt`. Both H2H sides render exactly as today's user-vs-assistant split (pink-right vs gray-left). The streaming cursor only renders when `isStreaming = true`, which is never true for H2H (the H2H send path doesn't go through SSE).

### 6.5 Chat screen — input

No change. `ChatInputArea` is type-agnostic. The `hasWaitingAssistant` flag still gates send-button-disabled-during-reply behavior (Phase 7-final from the SSE PR's send-button gating); for H2H this means "disabled during in-flight non-streaming send", which already matches production semantics.

### 6.6 Chat screen — disabled affordances for H2H

When `conversationKind is Human`:
- Don't render the subscription/IAP overlay or paywall card.
- Don't render the takeover bar (already only renders when `isBotAccount` is true).
- Don't render the welcome / first-message overlay.
- Don't render the streaming cursor (automatic — no streaming path).

These are all early-return composables today; we add a `conversationKind is Human` short-circuit at each.

### 6.7 Inbox

No change. `ConversationListItem` already renders dual-avatar correctly. The list query already surfaces all conversations.

**One gotcha:** if `H2hChatEnabled` is OFF but a user has H2H conversations from a prior session (impossible today since the feature is new, but defensive), we'd surface them in the list with no way to open them safely. **Decision:** when `isH2hChatEnabled = false`, filter H2H items out of the inbox list at the screen layer. Cheap and keeps dormancy total.

## 7. Feature flag

Add to `ChatFeatureFlags.Chat`:

```kotlin
val H2hChatEnabled: FeatureFlag<Boolean> =
    boolean(
        keySuffix = "h2hChatEnabled",
        name = "Human-to-Human chat",
        description = "Enables direct messaging between users via " +
            "POST /api/v1/chat/human/conversations. Off until backend + " +
            "feature go-live.",
        defaultValue = false,
    )
```

Wire `flagManager.get(ChatFeatureFlags.Chat.H2hChatEnabled)` into `ConversationViewState.isH2hChatEnabled`, and into the profile screen's ViewModel state for button visibility.

## 8. Edge cases

| Case | Behavior |
|---|---|
| Backend returns existing conversation (200) | Treat as success. Same navigation. |
| Invalid `participant_id` (backend 4xx) | Toast with mapped error; user stays on profile screen. |
| Current user has blocked the participant (if/when block feature ships) | Out of scope. No block feature exists today. Document as a Phase-2 follow-up. |
| Participant's profile becomes deleted while chat is open | Same behavior as a deleted-influencer AI chat today (need to verify what that is — open question §11.3). |
| User taps "Send Message" while offline | The POST will fail; current `ChatErrorMapper` produces a generic network error → toast. Acceptable for v1. |
| H2H conversation in inbox while `H2hChatEnabled = false` | Filter out at inbox-screen layer (see §6.7). |
| Stale current-user principal_id (e.g. user logged out mid-session) | `SessionManager.userPrincipal` is observed; on logout, the inbox + chat screens get torn down by Decompose, so no zombie state. Same as today. |
| Race: two devices create conversations with same pair simultaneously | Backend should be the de-dup point (return existing). If it doesn't, mobile gets two conversations for the same pair, both valid. Document as backend invariant — open question §11.4. |
| H2H message fails to send | LocalMessage moves to `FAILED` state. Per spec interpretation, no retry button. User has to copy-paste and resend. (Open question §11.1.) |

## 9. Files to add (estimated 6)

```
shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/
  domain/models/ConversationKind.kt
  domain/usecases/CreateHumanConversationUseCase.kt
  domain/usecases/SendHumanMessageUseCase.kt
  data/models/CreateHumanConversationRequestDto.kt
  data/models/SendHumanMessageRequestDto.kt              # if shape differs from existing SendMessageRequestDto
```

`SendHumanMessageRequestDto` is only needed if the H2H wire shape diverges — if the existing DTO works with default-null `is_streaming` / `is_safety_fallback`, we reuse it and drop this file.

## 10. Files to modify (estimated 9)

| File | One-line rationale |
|---|---|
| `data/models/ConversationDto.kt` | Add `conversation_type` field (nullable). |
| `data/models/Mappers.kt` | Carry `conversationType` through to domain; allow null `influencer` when `conversationType == "human_chat"`. |
| `domain/models/Conversation.kt` | Add `conversationType: String?` carried from DTO. |
| `domain/ChatRepository.kt` + `data/ChatRepositoryImpl.kt` | New `createHumanConversation(participantId)` + `sendHumanMessage(conversationId, draft)` methods. |
| `data/ChatRemoteDataSource.kt` (interface and impl) | New H2H endpoint paths. |
| `di/ChatModule.kt` | Register the two new use cases. |
| `viewmodel/ConversationViewState.kt` | Add `conversationKind`, `participantUser`, `isH2hChatEnabled`. |
| `viewmodel/ConversationViewModel.kt` | Send routing + skip branches for H2H + initialize `conversationKind` from loaded conversation. |
| `nav/conversation/OpenConversationParams.kt` | Add `participantPrincipalId`, `conversationKindHint`. |
| `ui/conversation/ChatConversationScreen.kt` | Header branch on `conversationKind`; skip subscription/welcome/takeover overlays for Human. |
| `ui/inbox/InboxScreen.kt` (or its VM) | Inbox filter when `H2hChatEnabled = false`. |
| `shared/features/profile/.../ProfileMainScreen.kt` | New "Send Message" button + tap handler. |
| `shared/libs/feature-flag/.../ChatFeatureFlags.kt` | New `H2hChatEnabled` flag. |

Two of these files (`ConversationViewModel.kt`, `ChatConversationScreen.kt`) are also heavily modified on the SSE branch — see §12 for rebase coordination.

## 11. Open questions for Rishi (answer before coding)

1. **Retry on H2H send failure** — strict spec reading is "no Try Again button at all." Confirm: failed H2H messages stay as red FAILED bubbles with no retry affordance, and the user must re-type from scratch? Or keep the retry button (it's not technically AI-specific)?
2. **Backend message-side role convention** — does the H2H message DTO use `role: "user" | "assistant"` mapped to current-user-vs-other, or does it use `sender_principal_id` and require mobile to derive the role by comparing to `SessionManager.userPrincipal`? Need the exact wire format before §6.4 lands.
3. **Deleted-participant behavior** — what does the AI chat show today when an influencer is deleted? H2H should match.
4. **De-dup invariant** — is the backend guaranteed to return the existing conversation on POST when a pair already has one? Or do we need client-side de-dup as a defense?
5. **Inbox sort order** — H2H and AI conversations are interleaved by last-message timestamp, same as the existing inbox sort? Confirming the unified sort is desired.
6. **Send button location on profile** — header area next to Follow/Unfollow, or sticky bottom CTA, or both? Sketch / mock needed.
7. **First-time-empty-state** — when a fresh H2H conversation has no messages, what does the chat screen show? Empty + input bar, or a "Say hi to Alice!" prompt? AI chat has the welcome-bubble for empty state; H2H needs its own design.
8. **Should "Send Message" appear on bot/influencer profiles too?** Spec says "NOT bot profile" — confirming. Bot profiles already route to the existing AI chat flow on tap; the Send Message button would be ambiguous there.

## 12. Coordination with SSE PR #1173

The SSE PR modifies `ConversationViewModel.kt`, `ChatConversationScreen.kt`, `ChatRepository.kt`, `ChatModule.kt`, and adds `MOBILE-EXPERT-LESSONS.md`. This H2H plan touches the same first four. Two possible merge orders:

**Order A: SSE merges first, then H2H rebases.** Simpler. The lessons file and the SSE additions land on main; H2H rebases and resolves the four-file conflicts. Most of the H2H changes are in different sections of those files (header rendering vs streaming buffer state), so conflicts should be mechanical.

**Order B: H2H merges first, then SSE rebases.** SSE PR has to absorb H2H's changes. Bigger conflict surface for SSE (the `conversationKind` branches affect routing throughout the VM). Not recommended.

**Recommendation:** wait for SSE PR to merge before opening H2H PR. This branch can land its first commit immediately (no merge-state dependency for the planning + DTO/flag work), but the chat-screen and VM changes should land after the rebase to minimize re-review for Sarvesh.

## 13. Test plan (manual, on Motorola)

When implementation lands. Mirror the §11 style from the SSE plan:

- **H1 Functional happy path** — Profile of another user → tap Send Message → loading → chat screen opens → send "hi" → message lands. Other user (via test rig or second device) sees the message in their inbox.
- **H2 Idempotency** — Tap Send Message twice on same profile in quick succession → only one conversation gets created (backend returns existing on second tap) → both taps land on the same chat screen.
- **H3 Inbox visibility** — H2H conversation shows in the unified inbox with the other user's avatar + username. Tapping the row opens the chat.
- **H4 Flag-off dormancy** — With `H2hChatEnabled = false`, no Send Message button visible on any profile; existing H2H conversations don't appear in inbox.
- **H5 Carve-outs** — Bot profiles do NOT show the Send Message button. Active takeover on the user's own bot doesn't interact with H2H state.
- **H6 Error path** — Send message while offline → message goes to FAILED state. Verify the "Try Again" behavior matches whichever way §11.1 is answered.
- **H7 AI regression** — Open an existing AI conversation → verify subscription card, welcome message, retry buttons, and (when SSE is merged) streaming behavior all still work. The `conversationKind` branches must not leak into the AI path.
- **H8 Cross-screen** — Inbox + Wall + own profile + media upload flows unaffected by H2H changes.

## 14. Estimated scope

- **Code lines added**: ~300-500 in shared/features/chat, ~50 in profile/, ~10 in feature-flag.
- **Sessions**: 2-3 (one for backend/DTO/repository/use-cases, one for VM + chat screen branches, one for profile button + inbox filter + flag + Motorola test pass).
- **PR size**: estimated ~600 lines diff. Well below the "request a senior review on size" threshold.

---

**Next step:** Rishi reviews this plan, answers the §11 open questions, signs off on the architectural decision in §3 (reuse vs. parallel VM). Then implementation begins.
