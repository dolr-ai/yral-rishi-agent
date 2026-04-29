# yral-rishi-agent-conversation-turn-orchestrator

**Status:** empty placeholder. Code goes here when we reach the relevant phase per TIMELINE.md in the plan-and-discussions folder.

Spawned from the `yral-rishi-agent-new-service-template/` template (when it's built). Part of the monorepo at `github.com/dolr-ai/yral-rishi-agent`.

See `yral-rishi-agent-plan-and-discussions/` (sibling folder) for the full plan, constraints, timeline, and design decisions.

## Build-time contracts to honour (read before writing code)

- **Treat the composed Soul File prefix as opaque bytes.** Don't slice, normalize, re-order, or string-format the prefix returned by `yral-rishi-agent-soul-file-library`. Doing so will break provider prompt caching and silently regress TTFT.
- **Variable, per-turn content goes AFTER the cache breakpoint** — user message, retrieved memory facts, recent message tail, current timestamp (if needed). Never inject these inside the prefix.
- **Forward `cache_control` markers to the provider unmodified** — the composer emits them; the orchestrator passes them through. See `yral-rishi-agent-plan-and-discussions/README.md` Section 2.8 Step 4 "Stable prompt prefix for provider-side caching".
- **Latency budget for the hot path** is in Section 2.8 Step 2; cache hit on the prefix is what makes the 50%-faster-than-Python-chat-ai target reachable on prefix-heavy turns.
