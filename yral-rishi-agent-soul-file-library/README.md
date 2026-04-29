# yral-rishi-agent-soul-file-library

**Status:** empty placeholder. Code goes here when we reach the relevant phase per TIMELINE.md in the plan-and-discussions folder.

Spawned from the `yral-rishi-agent-new-service-template/` template (when it's built). Part of the monorepo at `github.com/dolr-ai/yral-rishi-agent`.

See `yral-rishi-agent-plan-and-discussions/` (sibling folder) for the full plan, constraints, timeline, and design decisions.

## Build-time contracts to honour (read before writing code)

- **Stable prompt prefix for provider-side caching** — the composed Soul File prefix (Layer 1 Global → Layer 2 Archetype → Layer 3 Per-Influencer → Layer 4 Per-User-Segment) MUST be byte-identical across turns for the same `(influencer_id, user_segment)` pair. No timestamps, request IDs, UUIDs, current-date strings, or random bullet ordering inside the cached prefix. This composer owns the contract; the orchestrator consumes the bytes opaquely. CI gate enforces byte-identity. Full rule: `yral-rishi-agent-plan-and-discussions/README.md` Section 2.8 Step 4.
- **Layer order is part of the public contract.** Reordering layers = breaking every downstream prompt cache. Treat as a versioned schema change.
- **Provider cache breakpoints** — emit `cache_control: {type: "ephemeral"}` (Anthropic) / equivalent (Gemini context-cache, OpenAI) at the END of Layer 4. Per-turn user message + memory facts go in the uncached suffix.
