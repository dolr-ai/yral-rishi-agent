# Video-gen + storage-interface: recon findings & migration plan

**Date:** 2026-08-21 · **Status:** awaiting Rishi sign-off · **Nothing has been changed.**

All findings below were verified empirically (curl/dig/gh/OpenAPI) or by reading
source. Where I could not verify, it is marked **OPEN**.

---

## 1. Headline: three of the brief's premises are wrong, in our favour

| Brief said | Actually |
|---|---|
| Video-gen paths are "currently broken", service decaying | GPU worker is **healthy and processing jobs** — last completed job **2026-08-21 05:18 UTC** (~1h before recon). RabbitMQ connected. |
| Prakash's *personal* infra | `storage-interface` runs on **prakash-1/2/3 — company Hetzner bare-metal**, in the *same* `dolr-ai/hetzner-bare-metal-fleet` Ansible inventory as rishi-1..6. |
| Migrate onto our swarm | Mostly unnecessary. It is already our hardware, our GitHub org, our DNS zone. |
| Saikat *intends to* remove legacy bot-pfp endpoints | **Already removed.** Live `offchain.yral.com` no longer serves `/api/v1/user/profile-image` (404; absent from its published OpenAPI). |

Also: the GPU is **not** an H100. It is an **NVIDIA RTX PRO 6000 Blackwell Max-Q**, 96 GB VRAM, 1 TB system RAM.

**Recommendation: adopt in place, do not migrate.** Rebuilding this onto rishi-1..6
would be weeks of work to arrive at the same hardware in the same fleet. The real
job is (a) fix the one bug that breaks prod, (b) take ownership of access and
deploys, (c) resolve the Vast.ai dependency.

---

## 2. What is actually broken — one root cause, four broken flows

Mobile completed a **SpacetimeDB migration (`c01ff20b`, PR #1222)** that removed IC
delegated identities from the app entirely and moved every call to a **yral-auth
Bearer JWT**. The storage-interface **never got that migration** — it still requires
a chain-verified `DelegatedIdentityWire` **in the request body**, and contains
**zero** yral-auth / JWKS / ES256 code.

Every mobile → storage-interface call therefore fails deserialization with **HTTP 422**:

| Flow | Mobile sends | Server requires | Verified |
|---|---|---|---|
| `POST /api/v2/videogen/drafts/in-progress` | `{user_id}` + Bearer | `{delegated_identity, user_id}` | 422 `missing field 'delegated_identity'` |
| `POST /api/v2/videogen/generate` | `{request, upload_handling}` + Bearer | `{request, delegated_identity}` | 422 `missing field 'request'`→`delegated_identity` |
| `POST /api/v1/user/profile-image` (human) | `{image_data}` + Bearer | `{delegated_identity_wire, image_data}` | 422 `missing field 'delegated_identity_wire'` |
| `POST /api/v1/user/profile-image` (**bot avatar**, AI-influencer creation) | `{image_data}` + Bearer | same | same |

The 422 in the brief is not a dead backend, not a payload typo, and not four
separate bugs. **It is one auth split-brain**, and the brief's item 2 (switch to
yral-auth v2) *is* the fix. Bot PFP is not a separate workstream — same endpoint,
same fix.

`GET /api/v2/videogen/providers` is unauthenticated and returns 200 — consistent
with "only the authenticated paths are down."

---

## 3. The auth switch is not uniform — three tiers of difficulty

The delegated identity is used for two different things, and only one of them is authentication.

**Tier A — auth only. Drop-in. (`drafts/in-progress`, `generate`'s gate)**
The identity is discarded (`let (_identity, sender) = ...`); only the principal is
used, then `identity_principal == user_id` is enforced. yral-auth v2 `sub` **is**
the principal → straight swap. Reuse `yral-rishi-agent` PR #489's pattern
(JWKS fetch + ES256 + issuer check; remember the real-User-Agent workaround for
Cloudflare). JWKS is live and serving `kid=default`, ES256, P-256.

**Tier B — deferred signing credential. Needs a design decision. (`generate` → `complete`)**
`generate.rs:666` encrypts the whole `DelegatedIdentityWire` into
`upload_destination.encrypted_identity`, ships it through RabbitMQ, and
`draft_client.rs` decrypts it **minutes later** to register the post *as the user*.
A Bearer JWT is short-lived and is not a signing key — it cannot be stored and
replayed this way.

**Tier C — signs canister calls as the user. Hardest. (`profile-image`)**
`profile_image.rs` builds a per-request `ic_agent::Agent` **signing as the user** to
write `profile_picture_url` to the `user_info_service` canister. A JWT cannot do this.

**Escape hatch for B and C:** mobile has already moved to SpacetimeDB, so the
canister write may now be vestigial. And the service *already* loads a
`BACKEND_ADMIN_IDENTITY` PEM (`main.rs:265`) that currently goes unused on these
paths. Either the writes are dropped, or they run under the service identity with
the principal taken from the verified JWT. **This is the one question that decides
the size of the job — it needs Saikat.**

---

## 4. Verified topology & dependency graph

**`storage-interface.prakash.yral.com`** → Cloudflare → **Caddy** (`via: 1.1 Caddy`)
→ prakash-1/2/3, app on `:3005`. Repo: **`dolr-ai/yral-video-storage-service`**
("Storj Interface API"). Rishi has **admin**. Far bigger than the brief implies —
37 endpoints: videogen, profile images, raw/HLS upload + dedup, `move-to-nsfw`, and
a whole Storj↔Hetzner **media mirror/pHash** subsystem.

| Component | Where | Verdict |
|---|---|---|
| storage-interface app | prakash-1/2/3 (`94.130.13.115`, `88.99.151.102`, `138.201.129.173`) | **Keep** — our fleet |
| Patroni Postgres HA + etcd + pgbouncer | same 3 nodes, in-repo `docker-compose.ha.yml` | **Keep** — daily `pg_dump` → `prakash-yral/yral-video-storage-service/`, 30-day retention |
| RabbitMQ (AMQPS) | same 3 nodes — port **5671 open on all three** (repo `prakash-rabbitmq`, archived) | **Keep**, take ownership |
| Caddy edge | prakash-1 (`server_1`) holds the public domain | **Keep**; note single-node edge = SPOF |
| **videogen-worker + ComfyUI** | **Vast.ai rented GPU** (RTX PRO 6000, 96 GB) | ⚠️ **Only genuinely off-fleet piece** |
| Cloudflare Named Tunnel | `comfyui/videogen.prakash.yral.com`; token in GH secrets | **Migrate** — zone is ours, tunnel account **OPEN** |
| Hetzner S3 `prakash-yral` | `yral-profile/users/`, `gobgob/`, DB backups | **Keep**, verify account ownership |
| Legacy Hetzner S3 `yral-profile` | off-chain-agent's old bucket | **Migrate data** — see §5 |
| Storj `yral-sfw` / `yral-nsfw-videos` | Storj account | **OPEN — owner unknown** |
| `off-chain-agent` | `offchain.yral.com`, live, 10 paths left | Shrinking; no longer serves pfp or videogen |

**Freshness:** last deploy 2026-07-27 (success), last push 2026-07-30. Frozen but
stable — it is not rotting, it is just unmaintained. 5 open PRs, 2 of them Prakash's.

---

## 5. Bot PFP — the sequencing hazard has already half-fired

- Legacy endpoints: **already gone** from live off-chain-agent. Nothing left to gate.
- Buckets: legacy `yral-profile` → new `prakash-yral/yral-profile/`. Mobile
  (`ProfileUrlRewrites.kt`) already rewrites legacy URLs to the new bucket at read
  time, and also rewrites GobGob defaults from Cloudflare Images to
  `prakash-yral/gobgob/`. GobGob defaults are confirmed present and public-read
  (`gob.1.png` → 200, 41 KB).
- **OPEN:** whether the legacy `yral-profile` bucket's user images were actually
  copied across. Both buckets 403 on listing and I have no S3 credentials. If the
  copy never happened, every pre-migration avatar 404s **silently** — reads are
  rewritten to a bucket that may not hold the objects.

**This is now the single highest-value unknown.** It needs 10 minutes with the
Hetzner S3 keys (in the repo's GitHub secrets — Rishi is admin) to settle. Ask
Saikat whether the copy was done before answering anything else about bot PFP.

---

## 6. The GPU decision (strategy, for Rishi — not ops)

Our swarm is CPU-only; LTX-2 + ComfyUI cannot run on it. Three options:

1. **Status quo — keep renting.** Zero work. Risk: the Vast.ai account and its
   payment method are almost certainly **Prakash's**. If the card fails or the
   instance is reclaimed, video-gen dies with no warning and the model weights +
   ComfyUI custom-node setup go with it. **This is the largest unmanaged risk in
   the whole estate.**
2. **Re-rent under a company account** (Vast.ai or RunPod) and redeploy the worker
   there. Modest work: the deploy is a GitHub Action that scp's one Rust binary +
   starts a Cloudflare tunnel. Removes the personal-account dependency.
3. **Fold into ATMZ/Amorae GPU plans.** Right long-term home, wrong timescale for
   restoring prod.

**Recommend (2) now, (3) later.** Do (2) *before* it breaks, not after — and
snapshot the ComfyUI environment + model weights regardless of which we choose.

---

## 7. Proposed phasing

**Phase 0 — access & ownership (blocking, mostly Rishi/Saikat, no code)**
- Root `authorized_keys` on the fleet is only `github-actions@yral.com` +
  Saikat. **We have no SSH to prakash-1/2/3** — our CI key is a different key.
  Ask Saikat to add ours via the `ssh_security` role, or use the
  `DEPLOY_SSH_PRIVATE_KEY_PRAKASH` secret.
- Inventory the Vast.ai, Cloudflare-tunnel, Storj and Hetzner-S3 account owners
  and payment methods. Move any on Prakash's personal billing.
- Answer the §5 bucket-copy question.

**Phase 1 — restore prod (the actual fix)**
- One PR to `yral-video-storage-service`: yral-auth v2 ES256 verification, reusing
  PR #489's shape. Accept the Bearer as identity source; keep `delegated_identity`
  **optional** so nothing that still sends it breaks. Tier A endpoints only —
  `drafts/in-progress` and `generate`'s gate. Small, single-concern.
- Then Tier C (`profile-image`) once Saikat answers the canister-write question.
  This is what un-breaks **bot avatars and AI-influencer creation**.
- Then Tier B (`generate` → `complete` deferred identity), which is a design change,
  not a port.
- No mobile change should be needed — mobile is already correct. That is the point:
  **fix the server to match the app, not the app to match the server.**

**Phase 2 — take over the pipeline**
- Rename `prakash-*` → something neutral in the fleet inventory; adopt the deploy
  workflow; point Sentry at `sentry.rishi.yral.com`; add the service to our
  monitoring. Optionally front it with our 3-node Caddy so the edge stops being
  single-node.

**Phase 3 — GPU independence** (§6 option 2).

**Rollback:** Phase 1 is a pure additive auth path behind the existing deploy
workflow — revert the PR and redeploy. Phases 2–3 are DNS/edge changes, reversible
by restoring the previous Caddy snippet and tunnel target. Nothing in this plan
requires a schema change; if one appears, `pg_dump` first per Rule 9.

---

## 8. Questions I need answered before building

**Rishi:**
1. Adopt in place (my recommendation) or genuinely migrate onto rishi-1..6? This is the fork in the road.
2. GPU: re-rent under a company account now, or accept the risk for the moment?
3. Are you OK with me holding admin/deploy on `yral-video-storage-service`?

**Saikat:**
4. **Was the legacy `yral-profile` bucket copied into `prakash-yral/yral-profile/`?** (highest value)
5. `profile-image` writes `profile_picture_url` to the `user_info_service` canister *as the user*. Post-SpacetimeDB, is that write still needed? If yes, can it run under `BACKEND_ADMIN_IDENTITY`? — this sizes Tier C.
6. Same question for the deferred `encrypted_identity` → draft registration (Tier B).
7. Can our CI key be added to the fleet's canonical `authorized_keys`?

---

## Appendix — reproductions

```
curl -X POST -d '{}' https://storage-interface.prakash.yral.com/api/v2/videogen/drafts/in-progress
→ 422  missing field `delegated_identity`

curl -X POST -H 'authorization: Bearer <jwt>' -d '{"image_data":"..."}' \
     https://storage-interface.prakash.yral.com/api/v1/user/profile-image
→ 422  missing field `delegated_identity_wire`      # bot + human avatars

curl https://videogen.prakash.yral.com/health
→ 200  status healthy, rabbitmq connected, last job 2026-08-21T05:18:43Z

curl https://offchain.yral.com/api/v1/user/profile-image  → 404   # legacy already removed
curl https://auth.yral.com/.well-known/jwks.json          → 200   # ES256, kid=default
```
