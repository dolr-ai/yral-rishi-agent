# Pre-spawn contracts from coordinator — yral-rishi-agent-soul-file-library

> **Provenance:** This file preserves engineering-contract content that lived in
> the placeholder `README.md` the coordinator pre-staged for this service folder
> (commit history visible via `git log --follow yral-rishi-agent-soul-file-library/README.md`
> across the spawn PR). The placeholder `README.md` was removed during the
> Day-1 spawn (per `new-service.sh`'s no-overwrite guard) and replaced with the
> template's standard `README.md`. The contract content below was preserved
> verbatim by Session 4 so future readers do not lose it. A follow-up PR may
> fold this content into `DEEP-DIVE.md` / `WALKTHROUGH.md` once the
> soul-file-library's real surface is built.

## Build-time contracts to honour (read before writing code)

- **Stable prompt prefix for provider-side caching** — the composed Soul File prefix (Layer 1 Global → Layer 2 Archetype → Layer 3 Per-Influencer → Layer 4 Per-User-Segment) MUST be byte-identical across turns for the same `(influencer_id, user_segment)` pair. No timestamps, request IDs, UUIDs, current-date strings, or random bullet ordering inside the cached prefix. This composer owns the contract; the orchestrator consumes the bytes opaquely. CI gate enforces byte-identity. Full rule: `yral-rishi-agent-plan-and-discussions/README.md` Section 2.8 Step 4.
- **Layer order is part of the public contract.** Reordering layers = breaking every downstream prompt cache. Treat as a versioned schema change.
- **Provider cache breakpoints** — emit `cache_control: {type: "ephemeral"}` (Anthropic) / equivalent (Gemini context-cache, OpenAI) at the END of Layer 4. Per-turn user message + memory facts go in the uncached suffix.

## RELATED FILES
- `README.md` — the template-spawned service README (replaces the pre-spawn placeholder).
- `DEEP-DIVE.md` — visual mental model; absorbs these contracts when the real surface lands.
- `WALKTHROUGH.md` — narrative trace once the 4-layer composer is wired.
- `yral-rishi-agent-plan-and-discussions/README.md` Section 2.8 Step 4 — the v2 plan's stable-prefix caching rule.
