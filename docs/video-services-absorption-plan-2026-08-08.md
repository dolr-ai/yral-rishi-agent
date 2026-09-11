# Absorbing the departed-employee video services

**Date:** 2026-08-08
**Status:** investigation complete, plan proposed
**Method:** cloned all five repos, read the routes, probed production, and read
`yral-mobile`'s `AppConfigurations.kt` — which is the actual frontend contract.

---

## Headline

**Only one of the five repos is genuinely load-bearing.** Two are already dead,
one is an internal dependency the app never calls, and one can be deleted by
flipping a feature flag that mobile already ships.

**And there's a bigger problem than any of the five:** the app's *main video
feed* runs on Google Cloud Run and is not in any repo you listed.

---

## Ground truth — what mobile actually calls

From `yral-mobile/shared/core/.../AppConfigurations.kt`:

| Mobile constant | Host | Live? | Repo | Owner status |
|---|---|---|---|---|
| `FEED_BASE_URL` | `recommendation-service-82502260393.us-central1.run.app` | ? | **none provided** | ⚠️ unknown |
| `INFLUENCER_FEED_BASE_URL` | `recsys-influencer-feed.ansuman.yral.com` | up but **503** | ai-feed-recommendation-system | archived |
| `STORAGE_INTERFACE_BASE_URL` | `storage-interface.prakash.yral.com` | ✅ 200 | yral-video-storage-service | active |
| `VIDEOGEN_BASE_URL` | *same host* | ✅ 200 | same service | active |
| `UPLOAD_BASE_URL` | *same host* | ✅ 200 | same service | active |
| `OFF_CHAIN_BASE_URL` | `offchain.yral.com` | up | off-chain-agent | **archived** |
| `METADATA_BASE_URL` | `metadata.yral.com` | up | yral-metadata | **archived** |
| `OAUTH_BASE_URL` | `auth.yral.com` | up | yral-auth | **archived** |
| `BILLING_BASE_URL` | `billing.sarvesh.yral.com` | ✅ 200 | yral-billing | active |
| `DAILY_STREAK_BASE_URL` | `daily-streaks.naitik.yral.com` | ✅ 200 | naitik-yral-multiple-services | active |
| `CHAT` / `COACH` | `agent.rishi.yral.com` | ✅ | yral-rishi-agent | **ours** |
| `ANALYTICS` | `analytics.yral.com` | ✅ | yral-rishi-analytics | **ours** |

Videos themselves are served straight from `cdn-yral-sfw.yral.com` — they do
**not** pass through the storage service on read. That matters: read-path
availability is a CDN concern, not a service concern.

---

## The five repos, assessed

### 1. `yral-video-storage-service` — Rust, 26.5k lines, LIVE, **the only one that matters**

Serves **three** of mobile's base URLs from one host. Despite the name it's a
kitchen sink: Storj + Hetzner S3, HLS packaging, perceptual-hash dedupe, mirror
and scan jobs, thumbnail backfill, transcode, videogen orchestration, profile
images, and a daily `pg_dump` backup loop.

**But the frontend only touches a thin slice of it.** Everything mobile calls:

```
POST /api/v1/user/profile-image          DELETE /api/v1/user/profile-image
POST /api/v2/videogen/generate           GET  /api/v2/videogen/drafts/in-progress
GET  /api/v2/videogen/providers          GET  /api/v2/videogen/providers-all
POST /api/v2/videogen/complete           POST /api/v2/videogen/upload-url/refresh
     (upload endpoints on the same host)
```

Roughly eight endpoints. Every `/mirror/*`, `/media/*`, `/duplicate_raw/*` and
`/hls/*` route is internal ops — no frontend dependency at all.

### 2. `prakash-videogen` — Rust, 4.5k lines, LIVE at `comfyui.prakash.yral.com`

ComfyUI worker on a Vast.ai H100 behind a Cloudflare named tunnel. **Mobile
never calls it** — the storage service does. It's an internal dependency, so it
can be swapped for anything (hosted API, our own GPU) without touching the app.

### 3. `yral-video-upload-service` — **ARCHIVED, superseded**

1.3k lines, was a Cloudflare Worker at `yral-upload-video.go-bazzinga.workers.dev`.
Its V3 upload API now lives inside repo #1. **Ignore it.**

### 4. `ansuman-nsfw-detection-server` — **ARCHIVED, already replaced**

Python, and already superseded by **`prakash-nsfw-detection-server`** (Rust,
live at `nsfw.prakash.yral.com`, pushed 2026-07-28). Mobile doesn't call NSFW
detection at all — it's internal. **Absorbing this repo would be absorbing a
corpse.** If you want the NSFW service, take the Rust one.

### 5. `ai-feed-recommendation-system` — **half migrated, half still live and load-bearing**

This service has two distinct halves, and only one of them moved.

**Migrated — dead path.** `/api/v1/influencer-feed` returns 503
(`"Feed not yet available. Pipeline has not completed."`) because its config
still points at `chat_api_base_url = "https://chat-ai.rishi.yral.com"` and
chat-ai was decommissioned on 2026-07-15. This doesn't matter: the influencer
feed is now served by our own v2 at
`agent.rishi.yral.com/api/v2/discovery/influencer-feed`.

**NOT migrated — live production traffic.** `/api/v1/recommend-with-metadata/{user_id}`
returns **200 with real videos** in ~1.5s:

```json
{"user_id":"...","videos":[{"video_id":"0028f0fa...","canister_id":"ivkka-...",
"post_id":"fec35362-...","publisher_user_id":"jovus-...","num_views_all":42,
"from_ai_influencer":true,"is_following":false,"is_pro_user":false}, ...]}
```

`FeedRemoteDataSource.fetchAIFeeds()` calls it on `INFLUENCER_FEED_BASE_URL`
with `count` and `rec_type`. **No feature flag at that call site.**

**So this repo is not deletable — it's the second most important one**, and
arguably higher risk than the storage service, because the repo is archived
*and* it runs on a departed employee's machine. Its config expects ClickHouse
and kvrocks reached via `ansuman-1` HAProxy. That's a box we don't control
holding the state (pools, bloom filters, view counts, percentile pointers) for
a live feed.

---

## What's actually left

Three things, not five:

1. **Take over `storage-interface.prakash.yral.com`** — eight frontend
   endpoints plus whatever ops jobs are genuinely required.
2. **Take over the AI video recsys** — `/api/v1/recommend-with-metadata`, plus
   the ClickHouse and kvrocks state behind it on `ansuman-1`.
3. **Decide what backs video generation** — keep Vast.ai/ComfyUI, or replace it.

---

## Proposed plan

### Phase 0 — this week, no code

- **Map `ansuman-1`.** The AI video feed depends on ClickHouse and kvrocks on a
  departed employee's box. Get access, confirm what's running, confirm who pays
  for it, and back up the kvrocks state. **This is the most fragile live
  dependency we have** — an archived repo on an unowned machine serving the app.
- **Find the Cloud Run feed.** `recommendation-service-82502260393.us-central1.run.app`
  is the app's primary video feed and you have no source for it. Get access to
  that GCP project and find the repo. **This is the biggest single risk in the
  estate** and it isn't one of the five you asked about.
- **Inventory credentials** for `storage-interface.prakash.yral.com`: Storj
  access grants, Hetzner S3 keys, `SERVICE_SECRET_TOKEN`, the Cloudflare tunnel,
  the Vast.ai account, and the Postgres behind it. Departed employees mean
  rotation, and rotation before you understand the wiring breaks production.
- **Take a backup** of the storage service's database before touching anything.

### Phase 1 — adopt, don't rewrite (2-3 weeks)

Move `yral-video-storage-service` under our deploy pipeline **unchanged**:
our CI, our Swarm, our Sentry, our secrets, our hostname
(`video.rishi.yral.com`), with `storage-interface.prakash.yral.com` kept as a
CNAME so mobile needs no release.

Resist the urge to rewrite first. It's 26.5k lines of Rust doing HLS,
transcoding and perceptual hashing — none of which is quick to reproduce, and
all of which is currently working.

### Phase 2 — strangle it down to the thin slice (4-6 weeks)

Build `yral-rishi-video-service` in Python/FastAPI, matching our
`app/{config,routes,services,repositories}` shape, implementing **only the eight
endpoints mobile calls**. Route traffic across per-endpoint, old service still
running, one endpoint at a time.

**Deliberately drop:** mirror jobs, Storj/Hetzner scan jobs, phash dedupe and
backfill, thumbnail backfill, chain snapshots, the built-in `pg_dump` loop (we
have WAL-G), duplicate detection, and the preview environment. If any of those
turn out to be needed, they run as scheduled scripts rather than service routes.

**Keep as-is:** the Storj and Hetzner buckets, the CDN, and every URL format —
those are baked into published content and cannot change.

### Phase 3 — decide on video generation

`prakash-videogen` is an H100 on Vast.ai running ComfyUI behind a tunnel. It's
the most expensive and most fragile piece, and mobile never touches it directly.

Given the compute-cost thesis, this is where the interesting decision sits:
keep the rented H100, move it onto our own hardware, or use a hosted API and
delete the whole thing. **Decide this on cost-per-video-second**, and note that
it can be changed at any time without a mobile release.

---

## Risks

1. **The Cloud Run feed has no owner and no repo.** Highest-traffic dependency
   in the app, and if that GCP account lapses or its billing fails, the main
   feed dies with no recovery path. Fix this first.
2. **`off-chain-agent`, `yral-metadata` and `yral-auth` are all archived** and
   the app still calls all three. They're outside this brief but they're in the
   same failure mode, and auth failing is worse than video failing.
3. **Credential rotation.** Departed employees hold Storj grants, Hetzner keys,
   Cloudflare tunnels and the Vast.ai account. Rotate — but map the wiring
   first, because several of these are shared across services.
4. **Rewriting too early.** The temptation is to start clean in Python. The
   transcode/HLS/phash code is the part that took longest to get right and has
   no test coverage we control. Adopt first, strangle second.

---

## Open questions

1. Who owns the GCP project behind `recommendation-service-…run.app`?
2. Is the influencer-feed flag safe to flip immediately, or does mobile need a
   release to expose it?
3. Do we still need Storj at all, or can everything consolidate onto Hetzner S3?
   The service already supports both and that would remove a whole vendor.
4. Is anyone still using the `/mirror/*` and `/media/*` ops routes, or have they
   been idle since the team left?
