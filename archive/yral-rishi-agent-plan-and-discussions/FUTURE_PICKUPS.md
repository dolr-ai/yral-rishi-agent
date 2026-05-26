# FUTURE_PICKUPS.md — deferred ideas to revisit later

This file is a **parking lot**, not a TODO list. Things noted here are **not** in the current build plan and should NOT bleed into Phase 1–8. Revisit only when the trigger conditions listed against each item are actually hit.

The R&D source for the items below was an OpenClaw codebase deep-dive on 2026-04-27. OpenClaw (steipete's personal AI assistant gateway, open-sourced Nov 2025) is a recombination of agentic patterns, not a foundational invention — but two of its patterns are worth bookmarking for v2's later phases. The third (prompt-prefix stability) was promoted into the active plan on 2026-04-27 — see Section 2.8 Step 4 of `README.md`.

---

## 1. Double-lane request queueing (session lane + global lane)

### What it is

Every agent invocation is dispatched through **two queue lanes simultaneously**:

1. **Session lane** — keyed by `(user_id, influencer_id)` (or whatever uniquely identifies one conversation). Serializes requests for the same conversation. If a user double-taps "send" or two requests arrive milliseconds apart, the second waits for the first to complete instead of running concurrently and producing two parallel — and possibly contradictory — replies.
2. **Global lane** — a single shared queue across all sessions. Lets us cap **total concurrent in-flight LLM requests** (e.g., max 200 at a time) so we never blow past Gemini / Anthropic / OpenRouter rate-limits. When the cap is full, new requests wait briefly instead of hard-failing with HTTP 429s in users' faces.

A request goes through **both lanes**: enqueued on the session lane → when its turn comes, enqueued on the global lane → when global capacity is free, executes.

### Why we don't need it now

The existing Python `yral-chat-ai` has no such pattern and handles 25K msgs/day. v2 Phase 1 is a feature-parity drop-in, so we inherit "no queue" by default. Adding queueing increases scope without solving a problem that has been observed.

### Trigger conditions to revisit

Build it when **any one** of these is true:

- Real-traffic measurement on v2 shows >0.1% of turns being interleaved (two turns from the same user racing each other) — this manifests as duplicate LLM calls, half-rendered SSE streams, or replies referencing context from the wrong turn.
- We hit a provider rate-limit ceiling and Sentry shows >0.01% of turns failing with HTTP 429 from Gemini / Anthropic / OpenRouter.
- Daily message volume crosses ~100K/day (per Section 2.7.5 scale projection, this is roughly Month 3-4 at expected growth).
- We deploy a feature where one user can fan out N parallel turns (e.g., a "compare 3 influencers' replies side-by-side" UX) — this needs the global lane to keep total concurrency bounded.

### Cost to add later

Low. ~50-100 lines in `yral-rishi-agent-conversation-turn-orchestrator` + a Redis-backed queue (Redis is already in the template). Backwards-compatible: existing turns just run through the new lanes unchanged.

### Reference

OpenClaw `src/agents/pi-embedded-runner/run.ts:241` — `enqueueCommandInLane(sessionLane, ...)` wrapped in `enqueueGlobal(...)`.

---

## 2. A2UI — declarative agent-to-UI protocol

### What "A2UI declarative UI" actually means

**A2UI** stands for **Agent-to-User-Interface**. It's a JSON specification (open-sourced by OpenClaw at v0.8 public preview, in their `vendor/a2ui/` directory) that lets an LLM emit **structured UI components** as part of its response, instead of (or alongside) plain text. The mobile/web client renders those components using a pre-approved component catalog.

**Concrete picture.** A bot decides the user needs a quick mood selector. Today (text-only chat), the bot would write *"How are you feeling today? 1) Great 2) OK 3) Tired"* and the user types a number back. With A2UI, the bot emits something like this in its response stream:

```json
{
  "components": [
    { "id": "heading_1", "type": "heading", "text": "How are you feeling?" },
    {
      "id": "chips_1",
      "type": "chip-group",
      "options": [
        { "label": "Great",  "value": "great"  },
        { "label": "OK",     "value": "ok"     },
        { "label": "Tired",  "value": "tired"  }
      ],
      "onAction": { "type": "send_value" }
    }
  ]
}
```

The Android app sees this JSON, looks up `chip-group` in its native component catalog (a Compose function the YRAL mobile team has implemented and approved), renders three native chips inline in the chat, and when the user taps "Tired" the value `"tired"` flows back to the bot as a normal message-like event. Same idea on iOS or web — different rendering layer, identical wire format.

The "**declarative**" part: the bot describes **what should appear** (component types, properties, IDs, actions) — it does NOT emit HTML, JavaScript, CSS, or layout pixels. The client owns "how it looks." The agent owns "what it means."

### Why "declarative" matters here vs. just having the bot write HTML

Three reasons:

1. **Safety.** An LLM cannot inject XSS, scripts, fetch arbitrary URLs, or break out of the chat sandbox if all it can emit is a fixed list of pre-approved component types. The threat surface is the size of the component catalog, not the size of the web platform.
2. **LLM-friendliness.** A JSON list of `{type, props}` tuples is dramatically easier for an LLM to produce correctly than well-formed HTML — fewer hallucinated tags, simpler streamable structure, easier to validate before rendering.
3. **Framework-agnostic.** Same JSON renders in Compose on Android, SwiftUI on iOS, React on web, terminal-text on a CLI. We don't ship a webview into the chat window or fork the renderer per platform.

### Why we don't need it now

Phase 1 is a drop-in replacement for `yral-chat-ai` with **one** mobile change (the `CHAT_BASE_URL` swap), per CONSTRAINTS A16. The current product is plain text + the existing "Default Prompts" starter chips that the mobile app renders today. A2UI would require:

- A native component catalog in `yral-mobile` (Compose + SwiftUI implementations of every supported component type).
- A mobile-side renderer that parses the A2UI JSON stream and dispatches to native components.
- A round-trip protocol for user interaction events back to the bot.
- Schema versioning and forward-compat (old apps must not crash on new components).

That is a multi-week mobile-team initiative. It violates the "mobile changes deferred to Phase 3+, one at a time" rule (CONSTRAINTS A12 / A16).

### Trigger conditions to revisit

Consider A2UI when **all three** are true:

- v2 is past Phase 3 and we've already delivered SSE streaming as the first mobile change.
- A product-side hypothesis emerges that needs **structured interactive elements inline in chat** — NOT just buttons in the chat composer. Examples that would justify it: bot-driven mood/intake forms, structured decision trees for nutritionist/coach archetypes, "rate this response" inline thumbs, mini-poll UIs for fan engagement, or an in-chat guided meditation timeline.
- The mobile team has bandwidth to take on a renderer + component catalog as a deliberate multi-week project.

If these aren't all true, plain text + carousel-style starter chips (which already exist) are sufficient and we don't pay the renderer-development tax.

### Cost to add later

High initial cost (mobile-team weeks for the renderer + catalog), then near-zero marginal cost per new component type. The cost shape is: invest once in the renderer, then the orchestrator and the soul-file authors get a new expressive primitive cheaply for years afterwards.

### Reference

OpenClaw `vendor/a2ui/README.md` and `vendor/a2ui/specification/0.8/json/` for the schema. The component set in v0.8 covers: text, heading, button, text-field, chip-group, dropdown, checkbox, table, chart (bar/line/pie), card, stack layout, custom registered components.

---

## How to use this file

When a future Claude session or contributor asks "should we add X to v2?" and X is on this list, the answer is **no, not yet** — the trigger conditions above are how we know "yet" has arrived. Promote an item out of this file into the main plan only when the trigger fires, and date the promotion (the Step 4 prompt-prefix rule's "(locked 2026-04-27)" stamp is the model).

Add new items to this file as we encounter them. Required fields per item:

1. What it is (1-2 paragraphs).
2. Why we don't need it now (tied to current phase / constraints).
3. Trigger conditions to revisit (concrete, measurable).
4. Cost to add later (rough effort + reversibility).
5. Reference (where the idea came from).
