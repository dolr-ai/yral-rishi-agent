# Spawn brief — Video Service Session

**Date:** 2026-08-08
**Purpose:** paste the block in §1 as the opening prompt for a new Claude Code
terminal. §2 is the access checklist for Rishi.

---

## 1. The brief (paste this)

> You are the **Video Service Session**. You own one deliverable:
> **`yral-rishi-video-service`** — a new service that replaces the video feed
> currently served by a departed employee's box, without breaking the live
> mobile app.
>
> **Working directory:** `~/Claude Projects/yral-rishi-video-service`
> (create it; new git repo, sibling of `yral-rishi-agent`).
>
> ### Read first, in this order
> 1. `~/Claude Projects/yral-rishi-agent/CLAUDE.md` — the rules. They apply to
>    you unchanged: symmetry, simplicity, comments explain WHY, one PR per
>    concern, feature branches only, never push to main, and the deploy process
>    is never bypassed.
> 2. `~/Claude Projects/yral-rishi-agent/docs/video-feed-service-spec-2026-08-08.md`
>    — **your build spec.** Follow it.
> 3. `~/Claude Projects/yral-rishi-agent/docs/video-services-absorption-plan-2026-08-08.md`
>    — why this exists and what else is in the estate.
>
> ### What you are replacing
> `https://recsys-influencer-feed.ansuman.yral.com/api/v1/recommend-with-metadata/{user_id}`
> — live, serving the mobile app's AI video feed, running on an unowned machine
> from an archived repo. The response contract is frozen; everything inside is
> yours to simplify.
>
> ### Shape of the service
> Mirror `yral-rishi-agent`'s layout exactly — `app/{config,models,routes,
> services,repositories}`, `_env()` config constants, asyncpg raw SQL, FastAPI.
> A developer moving between the two repos should not have to think.
>
> Target is roughly **250 lines** across three PRs. If you find yourself past
> 400, stop and ask — you have probably reintroduced something the spec
> deliberately deleted.
>
> ### Build order
> - **PR1** — refresher: read ClickHouse + media index + `ai_influencers`,
>   merge, score, write `vfeed:ranked` to Redis. ~120 lines.
> - **PR2** — serving: `GET /api/v1/recommend-with-metadata/{user_id}`,
>   per-user shuffle, seen-set, fallback ladder. ~90 lines.
> - **PR3** — shadow-compare harness + metrics. ~60 lines.
>
> ### Start here — this needs no credentials
> The old service is publicly reachable, so before you have any access:
> 1. Capture 200+ live responses across varied `user_id` and `count` values.
>    Freeze them as contract fixtures.
> 2. Write the response-shape tests from those fixtures.
> 3. Build the shadow-compare harness (PR3 first, out of order — it's the only
>    part with no dependencies).
> 4. Scaffold the repo, CI, Dockerfile and Swarm compose, mirroring
>    `yral-rishi-agent`'s.
>
> ### Verify before building PR1
> Confirm `video_stats_daily` (or an equivalent per-video daily aggregate)
> exists in the ClickHouse on rishi-6. **If it doesn't, stop and tell Rishi** —
> Saikat needs to add the materialised view, and that blocks PR1.
>
> ### Autonomy — do these without asking
> Repo scaffolding, CI workflows, Dockerfile, Swarm compose, all code, tests,
> fixtures, the shadow-compare harness, opening PRs, responding to Codex review,
> and iterating until CI is green.
>
> ### Stop and ask — never do these alone
> - **Any DNS change.** The cutover repoints
>   `recsys-influencer-feed.ansuman.yral.com`. That is Rishi's call, and it is
>   the moment real users move.
> - **Touching ansuman's live service or `ansuman-1`** in any way, including
>   load-testing it.
> - **Rotating any credential.** Several are shared across services.
> - **Merging to main or deploying.** PR → CI green → Codex review → explicit
>   "merge it" from Rishi → merge → then deploy.
> - **Schema changes** without a `pg_dump` snapshot first.
> - Anything that would need a mobile app release. If you think you need one,
>   you have misread the plan — the whole design avoids it.
>
> ### Definition of done
> Deployed at `video.rishi.yral.com`, shadow-comparing against the live service
> for seven days, with a written report of overlap, latency and empty-page rate.
> **The DNS switch is Rishi's decision, not yours.**
>
> ### Working style
> Rishi has ADHD and reads every line. Keep updates short and concrete. Plain
> English. Push back when something looks wrong. Ask a question rather than
> guess — he prefers a question to undoing a mistake.
>
> Start by reading the three documents above, then report back with your plan
> and anything that blocks you.

---

## 2. Access checklist — what Rishi needs to provide

### Already available, no action needed

| Access | Notes |
|---|---|
| The five source repos | public in `dolr-ai`, readable via `gh` |
| `yral-mobile` | readable — `AppConfigurations.kt` is the frontend contract |
| `yral-rishi-agent` | the pattern to mirror |
| The live old service | public; enough to build fixtures and the harness |

### Needed to build PR1 — **the blockers**

| Access | Why | Who provides |
|---|---|---|
| **ClickHouse on rishi-6, read-only** — host, port, db, user, password | the view aggregates | you / Saikat. Use a dedicated reader, same pattern as `marketing_reader` |
| **Confirmation that `video_stats_daily` exists** | PR1 reads it | Saikat |
| **Postgres `video_fingerprint_index`, read-only** — on the storage service's own Patroni, prakash's infra | the video catalogue | **you — this is the real blocker** |
| **Our Postgres, read-only** — `ai_influencers` | `from_ai_influencer` | you (existing v2 credentials) |
| **Redis on rishi-4/5** | `vfeed:ranked`, seen-sets | you (existing Sentinel) |

### Needed to deploy

| Access | Notes |
|---|---|
| GitHub repo `dolr-ai/yral-rishi-video-service` | create it empty; your `gh` token already has `repo` scope |
| Two repo secrets | `DEPLOY_SSH_KEY`, `OPENAI_CODEX_API_KEY` — same as amorae |
| SSH to rishi-4/5 as `rishi-deploy` | already allowed per CLAUDE.md |
| Caddy route for `video.rishi.yral.com` | Session 6 owns the edge config |
| A free Swarm port | amorae took 8003; pick the next |

### Rishi-only — never delegated

| Action | Why |
|---|---|
| **Cloudflare DNS repoint** of `recsys-influencer-feed.ansuman.yral.com` | this is the live cutover |
| **SSH to `ansuman-1`** | must be obtained, and the history export runs separately from this session |
| **Credential rotation** | shared across services; map the wiring first |

---

## 3. What this session does NOT own

Keep the scope tight — these are separate jobs:

- **The `ansuman-1` history export.** Urgent, but independent, and it should not
  block or be blocked by the build. Give it to Session 6 or do it yourself.
- **The storage service absorption** (`storage-interface.prakash.yral.com`) —
  Phase 2 of the absorption plan, a different service and a different session.
- **The videogen decision** — Vast.ai and ComfyUI, Phase 4.
- **The orphaned Cloud Run video feed** — `recommendation-service-…run.app`,
  still unowned and still the biggest unresolved risk in the estate.

---

## 4. The one-line version

> Build a 250-line replacement for a 10,700-line feed service, prove it matches
> in shadow for a week, and hand Rishi a DNS change.
