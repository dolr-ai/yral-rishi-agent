# Pre-spawn contracts from coordinator — yral-rishi-agent-conversation-turn-orchestrator

> **Provenance:** This file preserves engineering-contract content that lived in
> the placeholder `README.md` the coordinator pre-staged for this service folder
> (commit history visible via `git log --follow yral-rishi-agent-conversation-turn-orchestrator/README.md`
> across the spawn PR). The placeholder `README.md` was removed during the
> Day-1 spawn (per `new-service.sh`'s no-overwrite guard) and replaced with the
> template's standard `README.md`. The contract content below was preserved
> verbatim by Session 4 so future readers do not lose it. A follow-up PR may
> fold this content into `DEEP-DIVE.md` / `WALKTHROUGH.md` once the
> orchestrator's real surface is built.

## Build-time contracts to honour (read before writing code)

- **Treat the composed Soul File prefix as opaque bytes.** Don't slice, normalize, re-order, or string-format the prefix returned by `yral-rishi-agent-soul-file-library`. Doing so will break provider prompt caching and silently regress TTFT.
- **Variable, per-turn content goes AFTER the cache breakpoint** — user message, retrieved memory facts, recent message tail, current timestamp (if needed). Never inject these inside the prefix.
- **Forward `cache_control` markers to the provider unmodified** — the composer emits them; the orchestrator passes them through. See `yral-rishi-agent-plan-and-discussions/README.md` Section 2.8 Step 4 "Stable prompt prefix for provider-side caching".
- **Latency budget for the hot path** is in Section 2.8 Step 2; cache hit on the prefix is what makes the 50%-faster-than-Python-chat-ai target reachable on prefix-heavy turns.

## RELATED FILES
- `README.md` — the template-spawned service README (replaces the pre-spawn placeholder).
- `DEEP-DIVE.md` — visual mental model; absorbs these contracts when the real surface lands.
- `WALKTHROUGH.md` — narrative trace once `run_turn` is wired.
- `yral-rishi-agent-plan-and-discussions/README.md` Section 2.8 — the v2 plan's hot-path latency narrative.
