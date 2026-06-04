# Post-mortem — Chat as Human Creator (PR #1172)

> See [`MOBILE-EXPERT-LESSONS.md`](./MOBILE-EXPERT-LESSONS.md) for SSE-debugging hard lessons — especially P1 (flavor confusion), P2 (build cache), P4 (claims-vs-code drift).

**Date:** 2026-05-29
**Author:** Claude (mobile build owner for this feature)
**For:** Rishi, and future-me when planning the SSE streaming feature

The Chat as Human feature shipped as a 5-commit PR after a multi-hour session that included multiple rounds of fix → regression → fix. This document is the honest accounting: every bug that surfaced, the root cause, what I'd do differently next time, and the specific test that would have caught it *before* Rishi noticed it on his Motorola.

---

## The cross-cutting themes (read these first)

These five patterns showed up across nearly every bug. They're the thing to fix next time, more than any specific technical mistake.

### 1. I designed for the creator's experience; the user's experience was an afterthought
Most of the surface area of the new UI was on the creator side (toggle, countdown, takeover bar). My mental model when speccing was "the creator gets a new screen, the user gets a banner." That framing was wrong. The user side had at least 60% of the bugs — flickering during polling, hidden messages after re-entry, banner duplication, backward auto-scroll. The user is *the person actually receiving* the takeover; their experience IS the feature.

**Next time:** for any feature with two roles, write the user story for *each role separately*, and budget equal planning depth to each. Don't let the role with new UI eat the planning oxygen.

### 2. I tested send/receive flows, not steady-state observation
Almost every test I ran in this session was "do an action, see the result." I rarely sat with the screen idle for 30 seconds and watched what happened. The every-3-seconds flicker during active takeover was visible to the human eye immediately — but I only noticed it after Rishi reported it, because I was always actively doing something on the screen.

**Next time:** add an explicit step to every test plan: "let the screen sit idle for 30 seconds and observe."

### 3. I patched symptoms instead of fixing invariants
The auto-scroll backward-jump bug had two manifestations. My first fix dedup'd by item identity ("don't re-scroll to the same target") — which only blocked one symptom. The real invariant — "auto-scroll moves forward in time, never backward" — caught the second manifestation Rishi found and several future ones I hadn't seen yet. Identity dedup was a workaround. Timestamp comparison was the invariant.

**Next time:** when fixing a bug, ask "what is the underlying rule I want?" not "what specific path do I want to block?" Block the rule violation, not the path.

### 4. I didn't audit existing components for assumptions the new feature would break
The auto-scroll helper assumed "every user send produces an AI reply, follow it." Takeover broke that assumption (the AI is suppressed). I didn't realize until the symptom showed up. Several other components had similar implicit assumptions — `resetState()` carries fields through, `loadedMessageIds` dedup expects sent items to eventually be paged in, `BotAccountConversationPrompt` is the only thing in the bot-account bottom slot.

**Next time:** for every new feature, list every existing component in the affected code path. For each, write down its implicit assumptions. Cross-check whether the new feature respects those assumptions.

### 5. I changed primitives and didn't trace all downstream effects
Adding stable keys to the LazyColumn fixed the swap flicker but broke the "items at index 0 implicitly land at the bottom" behavior. I didn't realize LazyListState's scroll-anchoring behavior depends on whether items have stable keys. Same pattern with `refreshHistory()` — I treated it as "fetch new messages" when it actually resets the entire pager.

**Next time:** before changing a primitive (key strategy, refresh trigger, state-class field), grep for every consumer of that primitive and trace how their behavior changes.

---

## Bug-by-bug accounting

### Bug 1 — Timer semantics were backward (backend bug, surfaced on mobile UI)

**Symptom:** Rishi expected the 2-minute countdown to mean "creator has 2 minutes to respond." Backend computed it as "user has 2 minutes to respond" — `remaining_seconds = 120 - elapsed_since_user_last_message_at`. As long as the user kept replying, the timer kept resetting; the creator could be silent for an hour and stay in control.

**Root cause:** I never asked, at spec time, what the timer's *user-facing meaning* was supposed to be. I just plumbed `remaining_seconds` from the backend response into the display. The wire field was named `remaining_seconds`; I assumed the unit and the semantics matched the intent.

**What I'd do differently:** at spec time, write the timer's meaning in one sentence ("creator must keep responding within 2 minutes"). Trace it back to the exact backend field that backs it. If the field tracks something different (e.g., `user_last_message_at` instead of `creator_last_message_at`), flag it as a backend question before building anything on top.

**Test that would have caught it:** a single isolated manual test: take over, type one message, then DO NOTHING from the creator side while the user (on a second account) keeps sending messages. Watch the timer. If it resets every time the user sends → user-silence semantics → wrong. I never did this test in isolation; my testing always had both sides active simultaneously, which masked the semantics.

---

### Bug 2 — "X has left the chat" duplicated 3× (backend bug, mobile rendered faithfully)

**Symptom:** Each takeover cycle left an extra "X has left the chat" banner on the user side. Three cycles = three banners.

**Root cause (backend):** Two paths wrote the banner: manual release (`creator_takeover.py:107-114`) and the auto-release sweep (`main.py:135-142`). Neither was idempotent. Plus `takeover_repo.activate()` used `COALESCE(user_last_message_at, NOW())` — so a fresh takeover inherited the stale timestamp from a previous cycle and could time out within seconds, triggering the sweep's "left" write.

**Root cause (my omission):** When I planned the mobile rendering, I treated `role="system"` messages from the backend as ground truth — "whatever the backend writes, I render." I didn't ask "could the backend write the same banner twice?" That's a question I should have asked.

**What I'd do differently:** when consuming a list of records from a backend, ask explicitly: "is this list deduplicated? on what key? what happens if the same logical event arrives twice?" The contract is part of the integration spec, not an unstated assumption.

**Test that would have caught it:** as a creator, rapidly toggle takeover ON/OFF five times. As the user, count "joined"/"left" banners — should be exactly five of each. Easy manual test. I didn't run it because my testing only did one cycle at a time.

---

### Bug 3 — LazyColumn flickered every 3 seconds during active takeover (mobile bug)

**Symptom:** When takeover was on, the entire message list visibly rebuilt every 3 seconds. The screen "blinked."

**Root cause:** My polling loop called `refreshHistory()` every 3 seconds to pull in new messages. I'd read `refreshHistory()` as "fetch new messages" — but its actual implementation increments `historyRefreshTrigger`, which triggers `flatMapLatest` in the `pagedHistory` flow to cancel the active Pager, wipe `loadedMessageIds`, and create a fresh `ConversationMessagesPagingSource`. That's a full LazyColumn rebuild every tick.

**What I'd do differently:** before adopting an existing function for a new purpose, read its implementation, not just its name. `refreshHistory` is named like a lightweight fetch; it actually does a heavyweight reset. The right primitive for "pull new messages and merge" was a separate call (`getCreatorConversationMessagesPage` direct + overlay merge — what I eventually built in `pollLatestMessagesIntoOverlay`).

**Test that would have caught it:** open a conversation as creator, take over, then SIT AND WATCH for 30 seconds. The flicker is unmistakable. I didn't notice because I was always actively typing or scrolling during my testing.

---

### Bug 4 — Optimistic Local→Remote swap caused a one-frame subtree rebuild (mobile bug, reverted)

**Symptom:** When a creator's optimistic pending message was replaced by the real server-returned message (~200-500ms after send), there was a one-frame visible flicker at the bubble's position.

**Root cause:** Without stable keys on `items()`, Compose tracks LazyColumn slots positionally. At the position where the message lived, the item type changed from `ConversationMessageItem.Local` to `ConversationMessageItem.Remote`. `MessageRow`'s `when` branch for the two types compiles to different slot-table calls — Compose tore down the Local subtree and rebuilt the Remote one. The bubble looked identical visually; the rebuild was the flicker.

**What I'd do differently:** when designing optimistic UI where a Local item becomes a Remote item at the same screen position, ask "does Compose treat this as the same slot?" The answer depends on whether `items()` has a stable key and whether both branches of the `when` produce the same composable structure. Both questions need explicit answers at design time.

**Test that would have caught it:** add an optimistic send → watch the bubble at the moment the POST resolves. Even with bare eyes, a one-frame flicker at 200-500ms is visible. I tested optimistic UI alongside several other changes in the same session; the flicker noise was masked by everything else moving.

---

### Bug 5 — Stable keys fixed the flicker but broke implicit scroll-to-bottom (mobile bug, reverted)

**Symptom:** After adding `key = { item.stableKey }` to fix bug 4, new messages started rendering past the visible LazyColumn area — visually hidden behind the input box.

**Root cause:** `LazyListState`'s anchoring behavior in `reverseLayout=true` depends on whether items have stable keys. Without keys, every overlay update is treated as a structural change and the list naturally settles with the newest item at the bottom-rendered edge. With keys, `LazyListState` anchors to the previously-tracked key and items are positioned relative to that anchor. When a new item appears at a smaller index (newer in reverseLayout), the anchor stays put and the new item lands past the viewport's leading edge.

**What I'd do differently:** the lesson is general — adding stable keys is a behavioral change to LazyListState, not just a Compose-internal optimization. Whenever I change identity primitives, I have to enumerate every place the old behavior was relied on. In this case, the implicit "bottom anchoring" was a behavior, not a documented contract; I had to discover it through Rishi's testing.

**Test that would have caught it:** the same "send a message during takeover, observe scroll position" test. The instant the stable-key build went in, the message went off-screen.

---

### Bug 6 — `resetState()` dropped the new flag field (mobile bug, fixed)

**Symptom:** After adding the feature flag, every conversation entry showed the legacy "switch to your human profile" prompt instead of the takeover bar, even with the flag set to true in code.

**Root cause:** `ConversationViewState` has two constructor call sites — line 131 (initial creation) and line 816 (inside `resetState()`). I updated the first one to read `flagManager.get(...)` but missed the second one. `resetState()` runs on every conversation entry; it rebuilt the state from scratch with the data-class default for the new field (`false`).

**What I'd do differently:** when adding a field to a state class, the discipline is `grep -n "ConversationViewState("` to find every constructor call, not just `.copy()` calls. Every constructor must either set the field explicitly or accept that it goes to default. Both are valid choices; the bug is forgetting to make the choice.

**Test that would have caught it:** open a conversation → leave → re-enter. The feature-gated UI must be present both times. This is a basic "re-entry" test I should run on every flag-gated feature.

---

### Bug 7 — Auto-scroll snapped backward when the takeover suppressed the AI reply (mobile bug, fixed)

**Symptom:** As the user, sending a message during active takeover caused the message to briefly appear, then the viewport scrolled "backward" to an older ASSISTANT message, pushing the user's just-sent message off-screen behind the input box.

**Root cause:** `findLatestAssistantIndex` returns the most recent ASSISTANT message in the rendered list. During normal user-AI chat the user's send produces an optimistic ASSISTANT placeholder, which is the new "latest assistant." When the takeover suppressed the AI reply, `handleSendSuccess` removed the placeholder without replacement. `findLatestAssistantIndex` re-evaluated and returned a *stale older* ASSISTANT. `derivedStateOf` re-emitted, `LaunchedEffect(scrollTarget, ...)` re-fired, and `animateScrollToItem` snapped backward to the older target.

**What I'd do differently:** before writing the takeover send path, I should have enumerated every existing component that reacts to "the latest ASSISTANT message changed." `AutoScrollToAssistantMessage` was one of them, and its implicit assumption was "any change means a newer one arrived." I had to discover that the hard way.

**Test that would have caught it:** as the user with takeover active on the other side, send 2-3 consecutive messages and observe where the viewport lands. Easy to run if I'd put it in my plan.

---

### Bug 8 — A second form of bug 7 surfaced on re-entry + typing (mobile bug, fixed in PR's last commit)

**Symptom:** Even after fixing bug 7 with identity-based dedup, a different repro path remained: leave the conversation, come back, tap into the input box → the same backward-scroll happened.

**Root cause:** My identity-dedup fix said "don't scroll if `targetId == lastScrolledTargetId`." On re-entry, `lastScrolledTargetId` reset to null (`remember`-scoped state in a freshly recreated composable). When `readyForAutoScroll` flipped true on first keystroke, the `LaunchedEffect` re-fired, target = stale ASSISTANT, `lastScrolledTargetId = null`, so my guard let the scroll through.

**What I'd do differently:** identity dedup was patching a specific scenario, not enforcing the underlying rule. The real invariant — "auto-scroll moves forward in time, never backward, and never to a target older than the newest visible item" — would have caught both forms in one fix. When fixing a bug, write the rule down first; if your fix only blocks one path to violating the rule, look for the other paths.

**Test that would have caught it:** a "leave and re-enter, then type" repro is exactly the test I should have run after the first fix to verify the invariant held. I didn't; Rishi found it.

---

### Bug 9 — "Joined the chat" banner appeared ~3 seconds late (mobile bug, fixed)

**Symptom:** When the creator toggled takeover ON, the user-side "X has joined the chat" banner didn't appear until the next 3-second polling tick. The creator could start typing before the banner showed.

**Root cause:** I treated the polling cycle as the universal mechanism for "pull new content," forgetting that for a user-action-triggered change, the latency budget is one frame, not three seconds. The fix was a one-shot `pollLatestMessagesIntoOverlay()` call inside the `startHumanCreatorTakeover` success branch.

**What I'd do differently:** map every UI change to its trigger. Background-driven changes can use polling. User-action-driven changes must update within a frame.

**Test that would have caught it:** toggle ON, observe time-to-banner with bare eyes. A 3-second delay after a tap is obvious.

---

### Bug 10 — Phase 3.8 spec drift in real-time (process bug, not code)

**Symptom:** Rishi originally specced Phase 3.8 (graceful Gemini error UX) with an inline `is_safety_fallback: true` flag on `assistant_message`. The backend session went with a cleaner top-level `error` object instead. I had to refuse to plan against the old spec.

**Root cause:** I'd already started thinking about how to add the flag to existing DTOs before Rishi told me to pause. Not a real bug yet, but a near-miss.

**What I'd do differently:** for any spec that's "we'll do this later," don't pre-plan in detail. The spec almost always changes by the time you start.

---

## What this means for SSE streaming

The SSE feature has the same structural risks, amplified:

- **The user is the primary surface.** Like the takeover user side, the streaming user is the entire point. I cannot treat this as "the backend streams, the bubble grows, easy."
- **Every existing component in the send-receive path will have implicit assumptions.** `_overlay.pending` assumes one optimistic message at a time. The auto-scroll machinery has the same backward-jump risks. The polling cycle will race with the SSE delivery. `loadedMessageIds` dedup needs to know about the streamed message's eventual server id.
- **Steady-state observation matters more than action-driven testing.** A stream that delivers one token then stalls is invisible to a "send and watch the result" test. Need to test "send and watch for 30 seconds."
- **Symptoms vs invariants:** the equivalent of "auto-scroll moves forward only" is "the streaming bubble grows forward only; tokens are never re-rendered." Write the rule down at spec time.
- **Spec drift:** the backend doesn't exist yet. I cannot pre-plan the wire format. I can pre-plan the architecture *shape* (lifecycle, fallback decision tree, edge case handling) without committing to specific message types. That's what `SSE-PLANNING-NOTES.md` is for.

---

## My pre-flight checklist for the next mobile feature

When the SSE spec lands and I start the real planning, I will go through this list explicitly before writing any code:

1. **Two-role separation:** if there are two user roles, write each user story separately. Equal depth.
2. **Existing-component audit:** list every component in the affected code path. For each, write down its implicit assumptions. Cross-check.
3. **Invariants over paths:** for each bug class I expect (e.g., "scroll position should be stable"), write the rule. Code defensively against the rule, not specific paths.
4. **Primitive change tracing:** if I change a key strategy, a state field, a refresh trigger — grep every consumer.
5. **Constructor-call discipline:** for any state class field added or changed, grep `ClassName(` to find every constructor call site.
6. **Latency budgets per trigger:** user-action triggers must update within a frame. Background polling can be 3s+. Map every UI change to its trigger.
7. **Steady-state test:** in my test plan, an explicit step is "let the screen sit idle for 30 seconds and observe." Not optional.
8. **Multi-cycle test:** repeat the user-flow 5+ times in succession. Subtle duplication/leakage shows up on cycle 3, not cycle 1.
9. **Re-entry test:** every flag-gated or state-derived UI must be tested by leaving and re-entering the screen.
10. **Cross-screen test:** the user-side experience must be observed on a real second device (or by switching accounts), not inferred from the creator-side logs.
