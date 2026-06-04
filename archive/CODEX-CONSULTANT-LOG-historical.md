## 2026-05-29

Question: SSE streaming chat bubbles flicker on Motorola for some bots, especially multi-batch ASCII/Hinglish replies. Need independent assessment of whether pre-compute Markdown/Text lock will fix it.

Assessment: The pre-compute-lock idea is not sufficient for the 2-batch flicker. It addresses the first-token / Local→Remote Markdown-vs-Text swap, but the current symptom pattern points to repeated Markdown re-rendering during streaming itself. In the mobile code, token events append to `streamingBuffer` on every batch and recompose the bubble, while ASCII content is routed through `Markdown(...)` via an all-ASCII heuristic. That means Ragini-style replies are reparsed as Markdown on every token batch. One-batch replies do not show the bug because there is only one render step. The cursor is also still appended into the Markdown input string during streaming, despite comments claiming otherwise, which may further destabilize rendering. Most likely root cause: Markdown renderer behavior during incremental updates, with lack of UI-side token coalescing as a secondary amplifier. Recommended next action: do not treat the lock heuristic as the fix for the 2-batch bug; first isolate the renderer by comparing streaming-as-Text vs streaming-as-Markdown and add precise logs around token batches, chosen render path, and recomposition counts.

Rishi decided: Pending.

## 2026-05-30

Question: Review another Claude session's `skills` design (`docs/SKILLS-FEATURE-DESIGN.md`) for the generalized specialist-influencer framework and react to its naming/scope choices.

Assessment: The revised design is stronger than the earlier `coaching_state`-only framing for YRAL's broader ambition. The rename from coaching-specific language to `skills`, `skill_slug`, and `user_skill_state` is the right abstraction because it covers coaches, briefings, advisors, and trackers without forcing every future feature into a goal/streak model. The orthogonal model `archetype × skill` is also correct: archetype remains HOW the influencer speaks, skill becomes WHAT the influencer does. The design is appropriately restrained for V1: one skill in code (`nutrition_coach`), one new state table, one nullable influencer column, and reuse of `messages`, `conversations`, `user_memories`, `proactive_messages`, Redis session memory, and the existing proactive loop. My main caution: V1 should avoid relying too heavily on hidden LLM-emitted XML/JSON for first-turn onboarding without a fallback parse/repair path, because this is the flakiest part of the design. Also, if creators will eventually edit skills in the Soul File Coach, the current coaching UI is not yet skill-aware; that future migration is real, not free. Overall recommendation: treat `docs/SKILLS-FEATURE-DESIGN.md` as the better north-star document, but implement the thinnest possible nutrition-only slice first and keep the hidden-JSON onboarding path on a tight leash.

Rishi decided: Pending.

## 2026-05-30

Question: Show a diagram for the full "expert factory" on top of the current YRAL Agent V2 system.

Assessment: The full expert factory should be visualized as one shared Coach OS with four layers: creator/domain input, domain-pack generation, runtime coaching engine, and learning/improvement loop. Current reusable assets slot in cleanly: wizard for first-draft bot/domain generation, Soul File composer for runtime prompt assembly, memory + session memory for personalization, proactive loop for check-ins, and scoring/recommendations/A-B testing for self-improvement. The missing architectural layer is a structured domain-pack schema and outcome/progress state. Once that exists, new verticals become configuration + review, not repeated handcrafting.

Rishi decided: Pending.

## 2026-05-30

Question: With the current YRAL Agent V2 architecture, how should YRAL build a self-improving proactive system that can become best-in-category for domains like nutrition, fitness, mental health, running, HYROX, etc., without Rishi hard-coding each category by hand?

Assessment: The current system is already most of the way to a reusable "Coach OS." It has the shared engine pieces: layered prompt composition (`services/soul_file.py`), long-term + session memory (`services/memory.py`, `services/session_memory.py`), a proactive loop (`main.py`, `services/proactive.py`), first-turn nudges (`services/nudge.py`), creator-side improvement tooling (`services/quality_scorer.py`, `services/recommendations.py`, `services/coach.py`, A/B testing), and a bot-creation wizard (`services/wizard.py`). The core strategic shift is not to build thousands of handcrafted influencers, but to add a thin "domain pack" layer on top of this engine. Each domain pack should define: target outcomes, check-in types, memory schema, progress schema, tool access, safety rules, and success metrics. Right now the system is still personality-first; to win in nutrition/fitness it must become outcome-first. The main missing pieces are: structured goal/progress state, domain-specific proactive triggers (not just 24h idle reopeners), explicit success metrics beyond engagement, and a generator workflow for new domain packs. Recommendation: focus first on 5-10 high-frequency, measurable categories (weight loss, muscle gain, running, HYROX, language learning, public speaking) and build one reusable vertical framework for them rather than broad category sprawl. Product-wise, keep chat free and monetize personalization, tracking, analysis, and proactive accountability. Technically, the next big move should be a generic "check-in agent + domain pack schema" rather than more one-off Soul File tuning.

Rishi decided: Pending.

## 2026-05-29

Question: Two post-Phase-5c SSE issues remain on Motorola: streaming jerkiness on long-history emoji bots (Soma) and re-entry double flicker. Need verification of mobile expert RCA and whether to ship animateItemPlacement, conditional auto-scroll, and a double-refresh investigation.

Assessment: The mobile expert is directionally right, but the hypothesis ranking should change. The strongest concrete cause in code is the Phase 8 `LaunchedEffect(streamingBufferLength) { scrollToItem(0) }` loop in `ChatConversationScreen.kt`, which fires on every coalesced buffer growth while near-bottom. That is an explicit repeated snap, not just passive LazyColumn layout shift. History length plausibly amplifies the visual and CPU cost, but it is a secondary amplifier, not the primary trigger. The re-entry double refresh is also likely already explained by code: paging rebuilds automatically when `conversationId` changes, and the screen separately calls `refreshHistory()` in `LaunchedEffect(Unit)`, so a single re-entry can produce two pager resets without any hidden Paging bug. `animateItemPlacement()` is not a clean first fix because the conversation list items currently do not have stable keys; adding placement animation without item identity is incomplete and may re-open earlier list-behavior problems. Recommended path: do not ship Fix 1 only. First remove or narrowly gate the per-buffer `scrollToItem(0)` behavior and fix the obvious duplicate refresh trigger. Then retest Soma. Only if a visible residual shift remains should the team add placement animation, and that should be done together with explicit stable keys rather than as a blind modifier tweak.

Rishi decided: Pending.

## 2026-05-29

Question: After instrumented proof that the Markdown library reparses on every token batch, choose between Option A (Markdown during streaming + 200-300ms coalescing) and Option D (Text during streaming and keep that specific streamed message as Text forever after done).

Assessment: The mobile expert's updated diagnosis is now correct. The code confirms ASCII content is Markdown-eligible by default, token events mutate the streaming buffer on every batch, and the current experiment forces `isStreaming -> false` for Markdown selection, which matches the hypothesis under test. Option D is conceptually clean for flicker, but "forever Text" is not cheap in this architecture: the override is currently only a ViewModel-scoped in-memory map keyed by server message id. On re-entry, paging, app restart, or any other screen that renders the same message, that decision disappears unless a persistent client-side metadata store is added. So D is not a small UI tweak; it becomes a new state-persistence feature. Option A is safer to ship because it stays aligned with the existing message model and server truth, though it should be tightened by rendering the cursor outside the Markdown input string and coalescing updates before feeding the renderer. Recommended path: ship A, not D.

Rishi decided: Pending.
