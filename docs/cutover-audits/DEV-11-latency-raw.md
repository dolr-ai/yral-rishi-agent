# Phase 1.17 — v2 vs chat-ai latency comparison

**Run:** 2026-06-04T13:22:49.625680+00:00 → 2026-06-04T13:46:43.089110+00:00
**v2 URL:** `https://agent.rishi.yral.com`
**chat-ai URL:** `https://chat-ai.rishi.yral.com`
**Samples per endpoint per backend:** 100  **Concurrency:** 1

**CLAUDE.md target:** v2 must be **≥50% faster** than chat-ai on user-facing endpoints.

## Summary

| Endpoint | v2 p50 | chat-ai p50 | Δp50 | v2 p95 | chat-ai p95 | Δp95 | Target met? |
|---|---:|---:|---:|---:|---:|---:|:---:|
| `chat-send` | 3404ms | 3759ms | ↓9% | 5676ms | 7386ms | ↓23% | ❌ |
| `inbox-list` | 904ms | 427ms | ↑112% | 4653ms | 1240ms | ↑275% | ❌ |
| `inf-list` | 377ms | 339ms | ↑11% | 2754ms | 2867ms | ↓4% | ❌ |

## Per-endpoint detail

### `chat-send`

| Stat | v2 | chat-ai |
|---|---:|---:|
| n (success) | 100 | 100 |
| n (error)   | 0   | 0   |
| p50 ms      | 3404 | 3759 |
| p95 ms      | 5676 | 7386 |
| p99 ms      | 9462 | 7937 |
| mean ms     | 3693 | 4066 |
| min ms      | 1944 | 1692 |
| max ms      | 10648 | 9905 |
| status mix  | `{200: 100}` | `{200: 100}` |

### `inbox-list`

| Stat | v2 | chat-ai |
|---|---:|---:|
| n (success) | 100 | 100 |
| n (error)   | 0   | 0   |
| p50 ms      | 904 | 427 |
| p95 ms      | 4653 | 1240 |
| p99 ms      | 5179 | 1370 |
| mean ms     | 1532 | 626 |
| min ms      | 381 | 397 |
| max ms      | 6684 | 1919 |
| status mix  | `{200: 100}` | `{200: 100}` |

### `inf-list`

| Stat | v2 | chat-ai |
|---|---:|---:|
| n (success) | 100 | 100 |
| n (error)   | 0   | 0   |
| p50 ms      | 377 | 339 |
| p95 ms      | 2754 | 2867 |
| p99 ms      | 3729 | 3205 |
| mean ms     | 743 | 700 |
| min ms      | 313 | 302 |
| max ms      | 4147 | 4396 |
| status mix  | `{200: 100}` | `{200: 100}` |

## Notes

- Both backends served the SAME JWT (verify_signature=false) so auth path is held constant.
- Concurrency capped at 1 per backend to avoid skewing production latency.
- Chat-send latency excludes conversation-create — the timed payload is the POST /messages (LLM round-trip + persistence).
- chat-ai p95+ on chat-send may include cold-start/queueing variance from its older runtime; v2 is on the new Patroni cluster with the 25.4 hot-routing in place.
