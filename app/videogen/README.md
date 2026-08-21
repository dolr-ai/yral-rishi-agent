# Video generation

Everything for AI video generation lives in this package. `ls app/videogen/` is
the whole feature.

Replaces the videogen half of `storage-interface.prakash.yral.com` (Prakash left;
the service is orphaned). That service is ~6,250 lines of Rust across three
servers; this is ~700 lines with no server of its own.

## Why it broke, and what the fix actually was

Mobile's SpacetimeDB migration moved every call to a yral-auth bearer token. The
old service still required a chain-verified `delegated_identity` **in the request
body**, so every videogen call returned **422**. One auth mismatch, four dead
endpoints. The repair is to make the server match the app — not the reverse.

## Flow

```
POST /generate
  verify JWT
  → one multimodal LLM call: prompt + image judged together   prompt_check.py
     unsafe → 400 {"InvalidInput": "<message the user sees>"}
  → INSERT pending row  (before submitting — this IS the recovery path)  repository.py
  → upload image to ComfyUI, inject the graph, POST /prompt   comfyui.py
  → 200 {operation_id, provider}

poll loop, every 15s                                          worker.py
  → GET /history/{prompt_id}
  → download video, extract thumbnail, put both to Storj       storage.py
  → add_post(status=Draft) AS THE USER                        spacetime.py
  → close the row
```

`add_post` runs **before** the row is closed. Reversed, the app's spinner clears
a beat before the draft appears and the user watches their video vanish.

## Things that are load-bearing

**The storage key layout is the mobile contract.** The app never reads a video
URL from us; it builds one from the post's `video_uid` and creator:

```
https://cdn-yral-sfw.yral.com/{principal}/{video_id}.mp4
https://cdn-yral-sfw.yral.com/{principal}/{video_id}-thumbnail.png
```

Changing bucket or provider is config plus a CDN origin change. Changing the key
layout is a mobile release.

**The thumbnail is not optional.** Every draft and feed card fetches the
`-thumbnail.png` sibling. Skip it and every card renders broken.

**The error body shape is not free-form.** The app parses `{"<Variant>": "<string>"}`
and falls back to dumping raw text at the user for anything else — a nested
object shows them JSON. Variants it understands: `InvalidInput`, `ProviderError`,
`NetworkError`, `UnsupportedModel`, plus bare `"AuthError"`.

**The safety gate fails closed.** An unreachable or unparseable model refuses the
generation. A retry costs the user seconds; a bad video is public and permanent.
The image is checked alongside the prompt — image-to-video means the user
supplies a picture, and a clean prompt over an unacceptable image is still an
unacceptable video.

**We act as the user on SpacetimeDB**, forwarding their own token, rather than
holding the shared `SPACETIMEDB_ADMIN_TOKEN` — which can rewrite any user's
username, email or subscription. `user_token` lives on the row only while the
generation is in flight and is cleared on any terminal state.

## What this deliberately does not have

No message broker (ComfyUI queues natively), no HMAC-signed completion callbacks
(nothing calls us back — we poll), no retry outbox, no pre-signed upload URLs or
the refresh endpoint their expiry required, no request fingerprinting or dedup
(every prompt yields a different video), no staging bucket for images (they go
straight to ComfyUI), and no separate moderation service.

One identifier — `video_id` — is the operation id, the storage object name, the
post id and our lookup key. The old service carried five.

## The workflow graph

`workflows/ltx2.json` is exported straight from ComfyUI, so when the models on
the GPU box change someone re-exports and replaces the file. Only four values
are injected by node id: prompt, duration, the image filename, and the two
sampler seeds — the exported graph pins both seeds to 0, which would make every
generation of a given prompt identical.

One graph serves both modes: node `267:201` feeds the `bypass` input of the
image-conditioning nodes, so text-to-video runs the same graph with the image
path switched off.

## Operating it

Ships dormant. `ENABLE_VIDEOGEN_LOOP=true` starts the poll loop; it is also the
stop button, checked every tick so it takes effect without a redeploy.

| Setting | Purpose |
|---|---|
| `COMFYUI_BASE_URL` / `COMFYUI_AUTH_TOKEN` | ComfyUI on the GPU box |
| `VIDEOGEN_S3_BUCKET` | Storj bucket for finished videos |
| `VIDEOGEN_PUBLIC_URL_BASE` | must match what the app's CDN hostname serves |
| `VIDEOGEN_POLL_INTERVAL_SECONDS` | default 15 |
| `VIDEOGEN_STALE_AFTER_SECONDS` | default 1800 — when a stuck generation is retired so the spinner clears |

Requires `ffmpeg` in the image for thumbnail extraction.

## Open dependencies

1. **ComfyUI is not reachable from the swarm.** It listens on `127.0.0.1:18188`
   on the GPU box with **no authentication of its own** — it needs a tunnel and
   a shared token, and must never be exposed openly.
2. **`cdn-yral-sfw.yral.com` must serve `VIDEOGEN_S3_BUCKET`**, or generation
   succeeds and playback 404s. Cloudflare origin change.
3. **[cluster#190](https://github.com/dolr-ai/yral-bare-metal-kubernetes-cluster/pull/190)**
   must merge and the module be republished. Until then `add_post` and
   `update_post_status` are admin-only and return `Unauthorized`; the video is
   still stored, the request is recorded failed.
