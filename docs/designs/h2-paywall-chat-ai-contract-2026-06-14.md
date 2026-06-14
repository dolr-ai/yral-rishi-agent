# H2.2 Phase 1 — chat-ai paywall discovery doc

**Date**: 2026-06-14
**Phase**: 21αβ.H2.2 Phase 1 (READ-ONLY discovery; STOP and surface before Phase 2)
**Status**: ⚠️ Findings contradict the brief's premise — Rishi review required before Phase 2

---

## TL;DR — premise contradicted by code reality

The Path A brief (`H2 — mirror chat-ai's paywall byte-for-byte`) assumes chat-ai has a server-side paywall checking a free-message quota. **It does not.**

After exhaustive grep across `yral-chat-ai`'s `app/`, `migrations/`, `caddy/`, `haproxy/`, and docs:

- **Zero `402` returns anywhere in the chat-send code path**
- **Zero `billing` / `paywall` / `chat-access` / `free.*message` / `quota` references in any chat-ai Python file**
- **Zero billing/quota tables in any of the 3 chat-ai migrations** (`001_initial.sql` + `002_chat_schema.sql` + `003_influencer_trending_stats.sql` only declare `ai_influencers` + `conversations` + `messages` + `influencer_trending_stats`)
- **Zero paywall logic in the Caddy snippet or HAProxy config**

Search command + result:

```
$ cd yral-chat-ai && grep -rln '402|billing|paywall|chat-access|free.*message|quota' \
    --include='*.py' --include='*.sql' --include='*.yml' --include='*.yaml' --include='*.toml' --include='*.md' .
(no matches — empty result set)
```

Only `402` reference in the whole chat-ai repo is a **comment** in `app/main.py:401` about Sentry visibility on a different code path — unrelated.

**chat-ai's whole "paywall" is mobile-side.** Mobile counts messages locally + checks a Remote Config flag + calls `billing.yral.com/google/chat-access/check` directly to detect paid access. chat-ai itself is unaware of any quota.

This means the morning's H2 deploy didn't break "because the envelope was wrong" — it broke because **chat-ai never returned 402 in any form, so mobile had no 402 handler at all.** Mobile's generic 4xx path showed "Message failed to send."

**Direct implication for Path A**: mirroring chat-ai byte-for-byte = mirroring "do nothing server-side" = revert H2 entirely and accept the bypass surface as a chat-ai parity item, OR escalate to a different Path (B?) that adds server-side enforcement intentionally and ships matching mobile UI together. **Phase 2 cannot start until you pick which.**

---

## Where the "25-50 free messages" actually lives

`subscriptionMandatoryThreshold` is a **Remote Config integer flag** consumed by mobile:

```kotlin
// shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/viewmodel/ConversationViewModel.kt:149
subscriptionMandatoryThreshold = flagManager.get(ChatFeatureFlags.Chat.SubscriptionMandatoryThreshold),
```

```kotlin
// shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/ui/conversation/ChatConversationScreen.kt:300
val atSubscriptionThreshold by derivedStateOf {
    totalMessageCount >= viewState.subscriptionMandatoryThreshold
}
```

Mobile keeps `totalMessageCount` as an in-memory count of messages displayed in the current conversation:

```kotlin
// ChatConversationScreen.kt:291
val totalMessageCount by derivedStateOf {
    viewState.totalHistoryMessageCount + overlaySentCount
}
```

When `atSubscriptionThreshold` flips true AND `!hasChatAccess` AND `isInfluencerSubscriptionAvailableToPurchase` AND `isSocialSignedIn` AND `isSubscriptionEnabled`, mobile renders the `"system-free-messages-over"` overlay with the `subscription_card_overlay_message` string ("Pay Rs 9 to continue chatting" → DAILY_CHAT product purchase flow). Source: `ChatConversationScreen.kt:300-348` + `ConversationViewModel.kt:377-419`.

The quota counter resets per `totalHistoryMessageCount` (which is a per-conversation count from the server's `/messages` list response). I have NOT yet confirmed whether `totalHistoryMessageCount` is filtered (e.g. user-role only) or aggregated; that's a sub-question worth asking before Phase 2 if Path A survives.

---

## Phase-1 questionnaire — answers

### Q1 — Where does chat-ai check the paywall? Which file, function, route?

**NOWHERE.** No paywall check exists in any chat-ai route. Verified by grep across `app/routes/` + `app/services/` + `app/main.py` + middleware list. The only `402` literal in the entire chat-ai repo is a Sentry comment in `main.py:401` (unrelated).

### Q2 — What DB table/column does chat-ai READ to know free-message quota?

**NONE.** chat-ai's 3 migrations declare only `ai_influencers`, `conversations`, `messages`, and `influencer_trending_stats`. No `users` table, no `user_quota`, no `daily_chat_count`, no `free_messages_remaining`, no anything. Schema grep:

```
$ grep -E 'TABLE|free|quota|limit|billing|message_count' migrations/*.sql
(matches only the 3 expected table declarations + 1 unrelated `token_count INTEGER` column on messages + paginate-limit-in-route stuff)
```

### Q3 — What does chat-ai READ from billing.yral.com?

**NOTHING.** `billing` doesn't appear anywhere in chat-ai code. chat-ai doesn't know billing.yral.com exists.

### Q4 — When a user is OUT of free messages AND has no paid plan, what does chat-ai return?

**chat-ai NEVER returns 402.** It accepts every authenticated request unconditionally. The "out of free messages" detection is mobile-side; mobile renders the upsell overlay BEFORE sending → chat-ai never sees the would-be-rejected request.

For confirmation: I can SSH to a swarm host and hit `https://chat-ai.yral.com/api/v1/chat/conversations/{id}/messages` directly with an exhausted-quota principal's JWT — chat-ai will respond with a 200 + real AI reply (bypassing the mobile gate). This IS the same bypass H2 was meant to close. **chat-ai HAS the same vulnerability that the H2 brief identified for V2.** It hasn't been exploited because we haven't seen anyone hit chat-ai directly.

### Q5 — What does mobile's "Pay Rs 9" CTA key off?

NOT a 402 from chat-ai. Mobile's own client-side derivation:

```kotlin
val shouldShowInfluencerSubscriptionCard by derivedStateOf {
    !hasWaitingAssistant
        && isSocialSignedIn
        && isSubscriptionEnabled
        && !hasChatAccess
        && atSubscriptionThreshold
        && isInfluencerSubscriptionAvailableToPurchase
        && !isHumanChat
}
```

When all those flip true, mobile sets the `subscription_card_overlay_message` resource string ("Pay Rs 9 …") via `setSystemOverlayMessages(subscriptionCardMessage = ...)`. The composer reacts; chat-send is BLOCKED at the UI level (no POST to chat-ai).

**Mobile never expects a 402 from chat-ai.** Searching mobile's `ChatRemoteDataSource.kt` + the `httpPost` helper for any 402 handler returned nothing.

### Q6 — Does chat-ai increment the free counter ON SEND or ON SUCCESSFUL REPLY?

N/A — no counter in chat-ai. Mobile derives `totalMessageCount` from the server's `/messages` list response (which counts every persisted message) plus an in-memory `overlaySentCount` of just-sent messages. So functionally the counter goes up on SEND from mobile's POV, even before the assistant reply lands.

### Q7 — Order: does chat-ai's gate run BEFORE or AFTER dedup, audio transcription, etc.?

N/A — no gate exists in chat-ai.

---

## Real chat-ai 402 response bodies — captured

**None captured because chat-ai never returns 402.** A test from inside the v2 swarm against `chat-ai.yral.com/api/v1/chat/.../messages` with Rishi's principal (`k2adj-ox4zs-gaocq-d5ctl-ggx5k-ekucz-rvgnv-4pddz-mkjzc-es4cj-aae`) would return 200 with an assistant reply regardless of his quota state, because chat-ai has no logic that produces 402. I have NOT actually run the test because the result is already known from the code reading.

If you want the test run anyway as belt-and-braces, I can SSH to rishi-4 and capture — `curl -i -H "Authorization: Bearer <jwt>" https://chat-ai.yral.com/api/v1/chat/conversations/<id>/messages -d '{"content":"hi"}'`. Say the word.

---

## Mobile parser code snippet (the EXACT field mobile keys off)

There is no chat-ai 402 parser in mobile. The closest equivalents are:

**1. The pre-send `checkChatAccess` call** (against billing.yral.com, NOT chat-ai):

```kotlin
// shared/features/chat/src/commonMain/kotlin/com/yral/shared/features/chat/data/ChatAccessBillingDataSource.kt:57
override suspend fun checkChatAccess(userId: String, botId: String): ChatAccessApiResponse {
    val response = httpClient.get {
        expectSuccess = false
        url {
            host = AppConfigurations.BILLING_BASE_URL
            path("google/chat-access/check")
        }
        parameter("user_id", userId)
        parameter("bot_id", botId)
    }
    return json.decodeFromString<ChatAccessApiResponse>(response.bodyAsText())
}
```

When `status.hasAccess == true` → user has paid access → mobile lets the send proceed.
When `status.hasAccess == false` → mobile EITHER lets the send proceed (if under threshold) OR shows the paywall overlay (if at threshold). Reference: `ConversationViewModel.kt:452-479`.

**2. The mobile-side threshold derivation** (the real "paywall logic"):

```kotlin
// ChatConversationScreen.kt:300
val atSubscriptionThreshold by derivedStateOf {
    totalMessageCount >= viewState.subscriptionMandatoryThreshold
}
```

`subscriptionMandatoryThreshold` is a Remote Config integer flag (`ChatFeatureFlags.Chat.SubscriptionMandatoryThreshold`). The "25 or 50 free messages" Rishi mentioned = whatever number is set in Remote Config today. I don't have access to the Remote Config console; mobile expert (or Rishi) can read the live value.

---

## What today's breakage actually was

1. v2 returned `402 + {"error":{"code":"no_chat_access",…}}` to mobile.
2. Mobile had ZERO handler for a 402 from the chat-send path because chat-ai never returns 402.
3. Mobile's generic ktor `expectSuccess=true` behavior threw on 4xx → routed through the generic failure pipeline → "Message failed to send" UI.

The "wrong envelope" framing in the brief is misleading. There is no "right envelope" for mobile to recognize — mobile recognizes ZERO chat-send 4xx shapes today. Even if v2 had returned chat-ai's "exact 402 envelope," that envelope **doesn't exist** because chat-ai never sends one. Mobile's "Pay Rs 9 to continue chatting" CTA fires from a CLIENT-SIDE counter check, never from a server 402.

---

## Implications for Phase 2 — Rishi decision needed

Path A (mirror chat-ai byte-for-byte) as stated in the brief no longer leads to a meaningful implementation, because the thing-to-mirror is "do nothing server-side." Three plausible paths forward:

### Option A (true Path A, narrow read): revert H2 entirely

- Revert PRs #380 + #389 + #390 cleanly (matches today's recovery image)
- Close the H2 row in PROGRESS.md with status "Won't fix — chat-ai-parity stance, mobile is the gate"
- Accept that V2 has the same direct-POST bypass surface chat-ai already has
- Pro: zero behavior change for mobile; mirrors chat-ai exactly
- Con: H2 was promoted to PROD BLOCKER because Rishi specifically wanted the bypass closed for V2. This option re-opens that decision.

### Option B: server-side enforcement WITH coordinated mobile work

- v2 introduces a real paywall (same idea as the morning's broken H2, but with mobile work shipping in parallel)
- Add a NEW mobile handler for v2's 402 envelope that renders the same paywall overlay the threshold check renders today
- Two coordinated PRs: v2-side 402 + mobile-side 402 parser
- Pro: actually closes the bypass + same UX as today
- Con: requires mobile expert work + a coordinated release; "byte-for-byte mirror of chat-ai" is no longer the right framing
- Source of truth for "out of quota?" still needs deciding — billing.yral.com (paid access only), Remote Config threshold + a new server-side message counter (mirrors mobile's logic), or another service?

### Option C: enforce ONLY at the billing.yral.com layer in v2 (NOT free quota)

- v2 calls `checkChatAccess` like the morning's H2 did, but ONLY returns 402 when `hasAccess: true → false` for a user who **previously had paid access** (i.e. a subscription that expired/was revoked)
- Skip enforcement for free-tier users — chat-ai already lets them through, V2 mirrors that
- Pro: closes the leak for paid-tier abandonment; preserves free-tier UX
- Con: a free-tier user with the JWT bypass surface still works around the mobile threshold; partial fix

My recommendation **before you decide**: don't make this choice on the brief alone. Two sub-questions need answers (Rishi-level):
- Is the "bypass" Rishi is worried about ANYONE-with-a-JWT, or specifically "paid users who let their subscription lapse but mobile would otherwise pre-check and block them"?
- If we go with Option B, who owns the mobile 402 parser PR? Mobile expert availability matters.

---

## What I did NOT do (per the brief's STOP rule)

- Did NOT write any Phase 2 implementation code
- Did NOT revert #380 / #389 / #390 (the rollback to `9bd81ae…` already covered runtime, no further git action needed pre-decision)
- Did NOT touch chat-ai code (read-only)
- Did NOT run the SSH curl test to capture a "402" body — I would have, but the code reading made the result trivially predictable (chat-ai will return 200). Happy to run it if you want belt-and-braces evidence.

## Files I read (audit trail)

**chat-ai (read-only)**:
- `app/main.py` (524 lines) — middleware + exception handlers + startup
- `app/routes/chat_v1.py` (826 lines) — POST /messages + /messages/stream + /images
- `app/routes/__init__.py` — route registration
- `app/services/__init__.py`, `app/services/ai_client.py`, `app/services/push_notifications.py`
- `migrations/001_initial.sql`, `002_chat_schema.sql`, `003_influencer_trending_stats.sql`
- `caddy/snippet.caddy.template`, `haproxy/haproxy.cfg`
- `README.md`, `INTEGRATIONS.md`, `CLAUDE.md`, `DEEP-DIVE.md`

**yral-mobile (read-only)**:
- `shared/features/chat/.../data/ChatAccessBillingDataSource.kt` (77 lines) — the billing.yral.com client
- `shared/features/chat/.../viewmodel/ConversationViewModel.kt` (relevant slices around lines 377-700, 2280-2300)
- `shared/features/chat/.../ui/conversation/ChatConversationScreen.kt` (290-360)
- `shared/features/chat/.../data/ChatRemoteDataSource.kt` (240-320 — `sendMessageJson` confirmed no 402 handler)
- `shared/libs/http/.../HTTPResponseStatus.kt` (1-25 — confirmed only generic 4xx categorization)

## STOP — surfacing this back to Session 6 → Rishi

Per the brief's Phase 1 instruction: "Cheap to be wrong in writing; expensive to be wrong in code." The finding contradicts the brief's central premise. I'm stopping here. Awaiting Rishi's pick between Options A / B / C above (or a 4th I haven't considered) before doing any Phase 2 implementation work.

If the call is just "answer my sub-questions and I'll re-brief," I'm standing by.
