# Phase 1.17 — post-fix re-run (chat-send 503 resolved)

**Date:** 2026-06-04 06:34 UTC
**Context:** Re-run after the Caddy `unhealthy_latency 3s` directive was removed. Mirrors the methodology in `PHASE-1-17-LATENCY-COMPARISON-2026-06-04.md`.

## Concurrency=3, N=25 (the previously-broken scenario)

| Endpoint | v2 success | chat-ai success | v2 p50 | chat-ai p50 |
|---|---:|---:|---:|---:|
| chat-send | **25/25** (was 9/25) | 25/25 | 2811ms | 2705ms |
| inbox-list | 25/25 | 25/25 | 289ms | 303ms |
| inf-list | 25/25 | 25/25 | 325ms | 324ms |

**v2 chat-send went from 64% failure → 0% failure.** Same script, same methodology, same load profile — only the Caddy config changed.

## What changed in Caddy

Removed a single line from the `reverse_proxy yral-rishi-agent:8000` block:

```diff
 fail_duration 10s
 max_fails 2
 unhealthy_status 5xx
-unhealthy_latency 3s
```

That directive was Caddy's passive "if a single response takes longer than 3 seconds, count it as a fail." Combined with `max_fails 2 / fail_duration 10s`, two slow-but-legitimate LLM responses would mark the entire upstream unhealthy → 503-empty-body for the next 10 seconds. Under concurrency=3 the math worked out to ~64% of chat-send traffic hitting a "marked-down" window.

## Cutover signal

- **Reliability:** chat-send 503 issue **eliminated.** Concurrent load now serves cleanly. ✅
- **Latency target (CLAUDE.md "50% faster"):** still NOT met. Parity with chat-ai persists because both run on Gemini for the user-facing path. This remains a separate decision (provider re-routing) untouched by this fix.
- **Active health probe still works:** `/health` every 2s is the primary up/down signal — that hasn't changed.
- **Passive 5xx still catches real failures:** `unhealthy_status 5xx` remains. Any real upstream 5xx (not slow-but-200) still trips the unhealthy threshold.

## What also landed in this fix

The live Caddyfile had no source-of-truth in the repo (Phase 0 created it directly as a docker config object). Recovered + dropped at `bootstrap/scripts/caddyfile.agent.txt` so future edits go through PRs. A big comment above the now-absent line documents *why* `unhealthy_latency` must not be re-added.
