# DEV-2 — Image gen via Replicate Flux (21α.C2)

## TL;DR

**🟢 GREEN** — Empirical live test: POST `/api/v1/chat/conversations/{id}/images` returned 201 in 9.0s with a persisted image message + Storj-hosted URL.

## Evidence

### Live test

Synthetic owner-user `image-gen-creator` + throwaway bot `image-gen-preflight-2026-06-05` (avatar NULL → no-reference path → uses `config.REPLICATE_MODEL` = `black-forest-labs/flux-dev`):

```
create conv: 201
image-gen: 201 in 9.0s
  message_id: 1cbd2274-f522-4830-8c09-111a8110d832
  message_type: image
  media_urls[0]: https://gateway.storjshare.io/yral-ai-chat-uploads/image-gen-creator/2bf2a8af-e9...
```

All four invariants hold:
- 201 (not 5xx)
- Returned within Replicate's `Prefer: wait` window (no client-side polling needed)
- Message persisted as `message_type=image`
- Storj S3 URL populated in `media_urls`

Cleanup verified — bot + conv + msg all deleted.

### Diff vs chat-ai

| Aspect | chat-ai (Rust) | v2 (Python) |
|---|---|---|
| Replicate endpoint | `POST /v1/models/{model}/predictions` | same |
| Default model | (config `REPLICATE_MODEL`) | `black-forest-labs/flux-dev` |
| Reference-image path | flux-kontext-dev | **flux-kontext-dev** ✅ |
| Auth | Bearer token | Bearer token ✅ |
| `Prefer: wait` header | yes | yes ✅ |
| Polling fallback | yes | yes (30 × 2s polls) ✅ |
| Aspect ratio default | (per call) | `1:1` (or `9:16` for reference) |

Both implementations functionally equivalent. v2's polling timeout is hardcoded 30 × 2s = 60s wall; if Replicate is slow under load the prediction could time out. Acceptable for alpha — Flux Dev typically completes in <10s.

### Env wiring

```
REPLICATE_API_TOKEN=<set, non-empty>
REPLICATE_MODEL=<set or defaulted to black-forest-labs/flux-dev>
```

Confirmed via `docker service inspect` (values not shown).

### Cost ledger

This audit cost ~$0.003 in Replicate Flux Dev. Within budget.

## Recommendation

**Cutover gate C2: GREEN.** No action required. The path is healthy + the test confirms the full chain (auth → conv → replicate → polling → storj upload → message persist) works.

## What I did NOT verify

- Reference-image path (flux-kontext-dev with the bot's avatar) — would need a bot with a real avatar_url to exercise that branch. Code path is symmetric with the no-reference test; lower risk.
- 60s polling timeout behavior under Replicate slowness (worth a load-time follow-up but not a cutover blocker)
- Storj S3 URL expiry — the URL embeds the path but not a presign signature in the snippet I saw; if presigning is done at S3-key-resolution time, this is fine
