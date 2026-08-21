# Video generation on the agent service — full plan & handoff

**Date:** 2026-08-21 · **Status:** plan agreed, ready to build · **Owner of execution:** Session 6
**Nothing has been changed in any repo.** All facts below verified by curl / source reading.

---

## 1. TL;DR

Prakash left. His `storage-interface` service owns AI video generation and is
**broken for the mobile app**. The cause is a single auth mismatch, not four bugs.

We are **not migrating his service**. We are rebuilding **only the AI-video-generation
path** — 5 endpoints — inside `yral-rishi-agent` as `app/videogen/`, roughly 300–400
lines, reusing the agent's existing auth, Postgres, LLM providers, Sentry and deploy.
Everything else in his service (28 of 37 endpoints) has **no mobile consumer** and gets
retired, not ported.

---

## 2. Why it's broken (one root cause)

Mobile's SpacetimeDB migration (`c01ff20b`, yral-mobile PR #1222) removed IC delegated
identities from the app and moved every call to a **yral-auth Bearer JWT**. The
storage-interface never got that migration — it still requires a chain-verified
`DelegatedIdentityWire` **in the request body** and contains zero yral-auth/JWKS/ES256
code. Every mobile call fails deserialization with **HTTP 422**.

Reproduction:
```
curl -X POST -d '{}' https://storage-interface.prakash.yral.com/api/v2/videogen/drafts/in-progress
→ 422  missing field `delegated_identity`
```

**Fix the server to match the app, not the app to match the server.**

---

## 3. Verified facts that shaped the plan

**The two "hard" problems don't exist.**
- `profile_image.rs` writes the picture URL to a canister *as the user* — but mobile
  already writes it itself via SpacetimeDB `update_profile_details_v2`
  (`AiInfluencerViewModel:566`, `EditProfileViewModel:403`). Redundant.
- `update_video_metadata.rs:122` registers posts with `state.ic_agent` — the **service's
  own** identity. The user's delegated identity is only used to *verify who you are*,
  which a JWT does. So no IC agent is needed anywhere.
- Posts now live in SpacetimeDB. `add_post` / `update_post_status` are **admin reducers**
  (`apps/yral-database-spacetime/src/posts.rs:270`), commented *"called by external
  Prakash/Naitik service"* — i.e. exactly the service we're building. Plain HTTP + JWT.

**Storage: it's Hetzner, not Storj.** ← *this resolves "I can't find yral-sfw on Storj"*
`cdn-yral-sfw.yral.com` returns `x-debug-bucket: yral-sfw` and
`x-amz-request-id: …hel1-prod1-ceph3` — **Hetzner object storage**, same service as the
profile-picture bucket. The Storj `yral-sfw` is the *other half of a mirror* the mobile
app never reads from.
**Consequence: write to Hetzner, drop Storj entirely, and no mobile URL change is needed.**

**The client builds video URLs itself** (`IndividualUserDataSourceImpl.kt:56,61`):
```
https://cdn-yral-sfw.yral.com/{publisher_user_id}/{video_uid}.mp4
https://cdn-yral-sfw.yral.com/{publisher_user_id}/{video_uid}-thumbnail.png
```
So the object key is fixed: `{user_principal}/{video_id}.mp4`. Match it and playback
just works. **We must also generate the thumbnail** or every card renders broken.

**Manual video upload is dead code.** `Config.FileUpload` is never navigated to; every
route in `DefaultUploadVideoRootComponent` lands on `AiVideoGen`, and a restored
`FlowSelection` route is redirected (line 73). Rishi was right; the earlier claim that it
was live was wrong.

**Load is tiny.** The worker's last 5 completed jobs span 2026-08-14 → 08-21. Roughly one
a day (suppressed by the outage, so treat as a floor). A 3-node RabbitMQ cluster and a
second Patroni cluster are vastly over-built for this.

**ComfyUI has its own queue** — `POST /prompt` → `prompt_id`, `GET /history/{id}` →
outputs. Saikat was right. RabbitMQ is unnecessary.

**One workflow covers both modes.** `build_ltx2_workflow` has a `PrimitiveBoolean`
(`267:201` = `is_t2v`) wired to the `bypass` input of both `LTXVImgToVideoInplace` nodes.
Text-to-video and image-to-video are the same graph.

---

## 4. Decisions taken (and why)

| # | Decision | Rationale | Who |
|---|---|---|---|
| 1 | Rebuild, don't migrate | Only 9 of 37 endpoints have a mobile consumer | Rishi |
| 2 | Live inside `yral-rishi-agent`, not a new service | Reuse auth, DB, LLM, Sentry, deploy, Caddy | Rishi |
| 3 | Package layout `app/videogen/`, not spread across `routes/`+`services/`+`repositories/` | 90+ flat files already; precedent exists (`app/eval/`, `services/llm_clients/`) | Rishi |
| 4 | Kill RabbitMQ — submit straight to ComfyUI | ComfyUI queues natively; worker's HTTP path already exists | Rishi + Saikat |
| 5 | Delete Prakash's Rust worker entirely | Workflow JSON lives in the service we're replacing anyway; direct-to-ComfyUI is ~60 lines | Claude |
| 6 | No fingerprint / dedup | Every prompt yields a different video | Rishi |
| 7 | One NSFW check on the prompt, not a moderation subsystem | Simplicity | Rishi |
| 8 | **The image is checked in the same LLM call** | Image-to-video means user-supplied images; multimodal LLM covers both inputs in one call | Claude → Rishi agreed |
| 9 | NSFW rejection = HTTP 400 + `{"InvalidInput":{"message":...}}` | Mobile already renders this; zero mobile work | Claude |
| 10 | **Hetzner bucket, not Storj** | The CDN mobile reads fronts Hetzner (§3) | Claude (reversed twice — see §3) |
| 11 | No backfill of old data | New users only | Rishi |
| 12 | Service polls; nothing calls us back | Removes HMAC signing, the outbox, and presigned-URL refresh | Claude |
| 13 | Collapse 5 identifiers to 2 | One uuid = `video_id` = post id = `operation_id` = object name; plus ComfyUI's `prompt_id` | Claude |
| 14 | **Act as the user on SpacetimeDB, not as admin** | Least privilege; no shared secret to obtain. Needs PR #190 | Rishi |

**What "HMAC" was** (asked): the worker called *back* into the service to say "done."
Anyone could forge that POST, so the worker attached a signature derived from the body +
a shared secret. In the new design nothing calls us back — we poll — so it disappears
entirely, along with ~340 lines.

---

## 5. The build

### Architecture

```
mobile ──JWT──> agent.rishi.yral.com /api/v2/videogen/*
                      │
                      ├─ LLM (Saikat's infra): prompt + image NSFW check
                      ├─ Postgres (Patroni): videogen_requests
                      ├─ ComfyUI (Vast.ai GPU): POST /prompt, GET /history
                      ├─ Hetzner S3 yral-sfw: {principal}/{video_id}.mp4 + -thumbnail.png
                      └─ SpacetimeDB: add_post / update_post_status (admin)
```

### File structure

```
app/videogen/
  __init__.py          router export
  routes.py            5 routes
  models.py            DTOs + the providers constant
  repository.py        one table
  prompt_check.py      prompt + image, one multimodal call
  comfyui.py           upload image, submit, poll, fetch, inject workflow
  workflows/ltx2.json  the graph, re-exportable straight from ComfyUI
  storage.py           put object + thumbnail, build playback URL
  spacetime.py         add_post / update_post_status
  worker.py            the pending → complete loop
  README.md
```

### Flow

1. `POST /generate` → verify JWT → **one multimodal LLM call on prompt + image**
2. If unsafe → **HTTP 400** `{"InvalidInput":"<message>"}` → mobile shows it
3. Insert `pending` row, mint one uuid → return `{operation_id, provider}`
4. If image: `POST /upload/image` to ComfyUI; inject prompt/duration/seed into `ltx2.json`
5. `POST /prompt` → store `prompt_id`
6. Background loop scans `pending`: `GET /history/{prompt_id}`
7. On done: fetch mp4 → `ffmpeg -i in -vframes 1` for the thumbnail → put both to Hetzner
   at `{principal}/{video_id}.mp4` and `-thumbnail.png`
8. **`add_post(status=Draft)` FIRST, then close the row** — reversed, the spinner vanishes
   before the draft appears (`complete.rs:244` warns about exactly this). The SpacetimeDB
   call forwards **the user's own `id_token`**, stored on the row at `/generate` and
   **deleted when the row reaches a terminal state**. Never logged.
9. Mobile polls `/drafts/in-progress`; the Drafts tab reads SpacetimeDB separately

A single loop is restart-safe by construction — the row is written before submission, so
the loop *is* the recovery path. No resume-on-boot special case.

### Schema (Rule 9: `pg_dump` first)

```sql
CREATE TABLE videogen_requests (
    id             BIGSERIAL PRIMARY KEY,
    user_id        TEXT NOT NULL,          -- JWT sub
    prompt         TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',  -- pending|complete|failed
    comfy_id       TEXT,                   -- ComfyUI prompt_id
    video_id       TEXT,                   -- uuid: post id + object name + operation_id
    video_url      TEXT,
    user_token     TEXT,                   -- user's id_token, forwarded to SpacetimeDB
                                           -- at completion; NULLed on terminal state
    failure_reason TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ON videogen_requests (user_id, status, created_at DESC);
```

### Mobile contracts — verified against the Kotlin DTOs

| Route | Request | Response |
|---|---|---|
| `POST /api/v2/videogen/generate` | `{request:{prompt, model_id, user_id, image?, aspect_ratio?, duration_seconds?, generate_audio?, negative_prompt?, resolution?, seed?, token_type?}, upload_handling:"ServerDraft"}` | `{operation_id, provider}` |
| `GET /api/v2/videogen/providers` | — | `{providers:[…]}` — only `id`+`name` required, rest nullable |
| `GET /api/v2/videogen/providers-all` | — | same list, unfiltered (internal builds) |
| `POST /api/v2/videogen/drafts/in-progress` | `{user_id}` | `{items:[{operation_id,status,created_at,provider,model_id,prompt,thumbnail_url}]}` |
| `POST /mark-post-as-published` | `{post_id}` | **plain string, not JSON** |

Image field shape: `{"type":"Base64","value":{"data":"<b64>","mime_type":"image/png"}}`
Error type is driven by **HTTP status**: 400→invalid input, 401→auth, 402→balance,
429→rate limit, 502→provider, 503→unavailable. Body parsed as the error DTO, else the raw
text is shown to the user.

`token_type` is always `Free` and provider cost is 0 — accept the field, build no billing.

---

## 6. Explicitly NOT being built

Raw/HLS upload, `get-upload-url`, `update-video-metadata`, dedup, pHash, `move-to-nsfw`,
the Storj↔Hetzner mirror, the media index — **28 endpoints with no mobile consumer.**
Existing videos keep playing from the CDN because they're already in the bucket.
Retire `storage-interface`, prakash-1/2/3, the RabbitMQ cluster and its Patroni cluster
once videogen is proven.

---

## 7. Open questions, by owner

### Rishi
1. Hetzner (not Storj) confirmed for the bucket? — recommended, and it needs **no** mobile URL change.
2. Hetzner S3 credentials for `yral-sfw` — they're in the `yral-video-storage-service`
   GitHub secrets (`HETZNER_S3_ACCESS_KEY` / `HETZNER_S3_SECRET_KEY`); you're repo admin.
3. Feature-flag name for the app-config gate (default false, per standing rule).

### Saikat — ONE item: review a PR
4. **Review + merge [dolr-ai/yral-bare-metal-kubernetes-cluster#190](https://github.com/dolr-ai/yral-bare-metal-kubernetes-cluster/pull/190)**
   — opened 2026-08-21. Lets a post's **creator** call `add_post` / `update_post_status`
   (previously admin-only), matching the existing `delete_post` gate. Then republish the
   SpacetimeDB module.

   **We ask for no credential at all.** Instead of holding the shared
   `SPACETIMEDB_ADMIN_TOKEN` — which is shared across yral-auth, yral-web, yral-metadata
   and off-chain-agent, and can rewrite any user's username/email/plan — the service acts
   **as the calling user** by forwarding their own yral-auth `id_token`. That token already
   carries `ext_spacetimedb_token: true`, SpacetimeDB derives the Identity from its
   `iss`+`sub`, and it lives 7 days (`ACCESS_TOKEN_MAX_AGE`) — far longer than a generation.
   Least privilege: a compromise reaches only users mid-generation, not the whole database.

**Resolved 2026-08-21, no longer blockers:**
- ~~Expose ComfyUI~~ — we have **root on the box** (`ssh -p 47225 root@93.91.156.105`).
  Verified live: ComfyUI answers on `127.0.0.1:18188`, `/queue` `/history` `/object_info`
  all 200, `ltx-2.3-22b-dev-fp8.safetensors` present, and all six custom node types the
  workflow needs exist. **We can tunnel for dev and set up the authenticated exposure
  ourselves.** GPU: RTX PRO 6000, 97,887 MiB.
- ~~Which LLM for the multimodal check~~ — **`runpod_vllm`**
  (`https://saikat-llm-mixture-of-experts.yral.com/v1`, `supports_vision: True`) is already
  in the agent's `llm_registry.py`. Precedent: `influencer_classification` does exactly this
  — multimodal classification, runpod primary, never gemini. Add a `videogen_prompt_check`
  process the same way. **Zero new integration.**

### Session 6 — blocking; build starts once these are answered
1. What identity does **AI-influencer creation** use to call SpacetimeDB admin reducers —
   the end user's yral-auth JWT, a **service** JWT with a fixed `sub`, or the spacetime CLI
   publisher identity?
2. If a service JWT: how is it minted, where does the secret live, and does it expire /
   need refresh?
3. **What is that service's derived SpacetimeDB Identity (hex), and is it already in the
   `ADMINS` const?** If yes, we reuse it and need **nothing from Saikat at all.**
4. Exact HTTP shape for calling a reducer from a *backend* (not mobile). Mobile uses
   `callProcedure(name, args, idToken)` — what's the raw URL / headers / body?
5. Is there an existing client/helper we should reuse rather than writing a second one?
6. Confirm prod host + database name — `maincloud.spacetimedb.com` /
   `yral-database-spacetime-4lbo7`?
7. **`AppConfigurations.kt` collision.** Videogen needs `VIDEOGEN_BASE_URL` and
   `UPLOAD_BASE_URL` → `agent.rishi.yral.com`; Session 6 is changing
   `STORAGE_INTERFACE_BASE_URL` in the same file. **One PR, not two.**

### Answered — SpacetimeDB service auth
`apps/yral-auth/src/oauth/jwt/mod.rs:53`: *"SpacetimeDB derives a deterministic `Identity`
from the `iss` + `sub` claims."* So a yral-auth ES256 JWT minted for a fixed service `sub`
**is** the SpacetimeDB token, and the derived Identity is stable — that's the value Saikat
adds to `ADMINS`. Precedent exists: `SPACETIMEDB_ADMIN_TOKEN` in
`apps/yral-auth/src/kv/spacetime_kv.rs:41`.

---

## 8. Sequencing

**Buildable now, blocked on nothing:** routes, models, repository, ComfyUI client,
workflow port, prompt check, the polling loop, storage writer — against stubbed
SpacetimeDB and S3.

**Blocked on Saikat:** end-to-end test (needs ComfyUI exposed) and the final `add_post`
step (needs `ADMINS`).

**Blocked on Rishi's Motorola pass:** the mobile PR. No mobile PR opens before it.

**Deploy discipline unchanged:** feature branch → PR → CI green + review → explicit Rishi
approval → merge → build+deploy. Flag default false until cutover.

**Standing risk:** the GPU box is still on Prakash's Vast.ai account. Everything here
points at it. Moving it to a company account should not wait for this build.

---

## Correction (2026-08-21, found while building)

The error body shape above was wrong in the first draft. The app's
`VideoGenErrorDtoSerializer` reads `element[key] as? JsonPrimitive` — the value
must be a **plain string**, not a nested object:

```json
{"InvalidInput": "We can't create a video from this. Try describing something else."}
```

A nested `{"message": …}` throws in the serializer and the app falls through to
its raw-text branch, showing the user a blob of JSON. Covered by a test
(`tests/test_videogen.py::test_error_body_is_a_flat_string_value`).
