# Video feed service — spec

**Date:** 2026-08-08 (rev 2 — simplified after the Saikat-pipeline decision)
**Status:** spec, ready to build
**Replaces:** `ai-feed-recommendation-system` `/api/v1/recommend-with-metadata`
**Lives in:** `yral-rishi-video-service`
**Companion:** `docs/video-services-absorption-plan-2026-08-08.md`

---

## 1. The contract — frozen, do not change

```
GET /api/v1/recommend-with-metadata/{user_id}?count=20&rec_type=mixed
```

```json
{
  "user_id": "test-user-1",
  "videos": [
    {
      "video_id": "0028f0fad4b1ff6427a8a3a7882b844b",
      "canister_id": "ivkka-7qaaa-aaaas-qbg3q-cai",
      "post_id": "fec35362-907d-4624-8446-048c1b901a61",
      "publisher_user_id": "jovus-…-aqe",
      "num_views_loggedin": 0,
      "num_views_all": 42,
      "from_ai_influencer": true,
      "is_following": false,
      "is_pro_user": false
    }
  ]
}
```

`count` default 20, cap 500. `rec_type` — accept anything, treat as `mixed`.

**Four of the nine fields are constants** (verified across 25 live videos):
`canister_id` is one config value, `is_following` and `is_pro_user` are always
`false`, `num_views_loggedin` is always `0`. See §6.

---

## 2. Architecture — one Redis key, one refresher

```
  ┌─── refresher, every 15 min ──────────────────────────────┐
  │                                                          │
  │  Saikat's ClickHouse    video_stats_daily  ──┐           │
  │  (rishi-6)              video_id, day, views │           │
  │                                              │           │
  │  media index Postgres   catalogue ───────────┼──▶ merge  │
  │  video_fingerprint_index                     │    + rank │
  │                                              │           │
  │  our Postgres           ai_influencers ──────┘           │
  │                         (from_ai_influencer)             │
  │                              │                           │
  └──────────────────────────────┼───────────────────────────┘
                                 ▼
                    Redis  vfeed:ranked   (one key, TTL 1h)
                    fully-hydrated JSON, ~2-5k videos
                                 │
  ┌──────────────────────────────┼───────────────────────────┐
  │  serving (per request)       ▼                           │
  │  read key → shuffle by user → minus seen-set → take N    │
  └──────────────────────────────────────────────────────────┘
                                 ▼
                              mobile
```

**Two design decisions that do most of the simplifying:**

1. **ClickHouse is never in the request path.** The refresher runs every 15
   minutes; serving touches Redis only. If ClickHouse is down, the feed keeps
   serving until the key expires.
2. **The Redis payload is fully hydrated.** There are only five real fields per
   video, so we store finished objects rather than ids. **No database read at
   request time at all** — no `videos` table, no `video_counters` table, no
   hydration join.

Size: 5,000 videos × ~200 bytes ≈ 1 MB in one Redis value. Cap the list at
5,000.

---

## 3. The refresher

One function, every 15 minutes. Three reads, one merge, one write.

```
views     ← ClickHouse:  SELECT video_id, sum(views) AS views_7d
                         FROM video_stats_daily
                         WHERE day >= today() - 7
                         GROUP BY video_id

catalogue ← Postgres:    SELECT video_id, post_id, publisher_user_id,
                                first_seen_at
                         FROM all_servable_videos_on_yral
                         WHERE servable_status = 'servable'

ai_ids    ← Postgres:    SELECT id FROM ai_influencers
```

Then in Python:

```python
score = log1p(views_7d) * exp(-age_days / HALFLIFE_DAYS)   # default 7
from_ai_influencer = publisher_user_id in ai_ids
```

Sort by score, take the top 5,000, write to `vfeed:ranked`.

**That's the entire ranking system.** Ansuman's had ~25 tunable weights across
four score families; this has one formula and one knob.

---

## 4. Serving

```
1.  GET vfeed:ranked                       (one Redis read)
2.  shuffle deterministically by user_id   (md5(user_id + video_id), no state)
3.  drop anything in vfeed:seen:{user_id}
4.  take `count`
5.  SADD the served ids, EXPIRE 24h
6.  return
```

Fallback ladder:

1. Key missing → serve newest-first straight from Postgres (`LIMIT 500`)
2. Seen-set exhausts the list → clear the seen set and continue rather than
   returning a short page

No cold-start branch is needed: with no per-user personalisation, a new user
gets the same list, shuffled by their id.

---

## 5. Redis keys

| Key | Type | TTL | Contents |
|---|---|---|---|
| `vfeed:ranked` | string (JSON) | 1h | fully-hydrated video objects, score desc |
| `vfeed:seen:{user_id}` | set | 24h | video_ids already served |

TTL on the ranked key is the staleness guard — if the refresher dies, the key
expires and serving falls back to the DB rather than serving a frozen feed
forever.

---

## 6. Field sources — resolved

Verified against 25 live videos plus
`yral-video-storage-service/src/media_index/schema.rs`.

> **CORRECTION 2026-08-09.** An earlier revision said `post_id` and
> `publisher_user_id` come from the media index. **They do not.** Verified
> against the live replica: `all_servable_videos_on_yral` has **586,046 rows
> with `publisher_user_id` and `post_id` NULL on every single one.** The 25/25
> figure came from the live *feed response*, not the index. The live recsys
> sources both fields from ClickHouse `video_unique_v2`, which is now archived
> on rishi-6 (255,621 rows). **The catalogue source is ClickHouse, not
> prakash's Postgres.** `yral_posts` (on-chain snapshot) carries
> `post_id` + `video_uid` + `creator_principal` and is the fallback join.

| Field | Source |
|---|---|
| `video_id` | ClickHouse `video_unique_v2` (media index for storage facts only) |
| `post_id` | ClickHouse `video_unique_v2`; fallback `yral_posts.post_id` |
| `publisher_user_id` | ClickHouse `video_unique_v2`; fallback `yral_posts.creator_principal` |
| `num_views_all` | ClickHouse `video_stats_daily` |
| `from_ai_influencer` | `publisher_user_id IN (SELECT id FROM ai_influencers)` |
| `canister_id` | **config constant** `ivkka-7qaaa-aaaas-qbg3q-cai` |
| `is_following` | **constant `false`** |
| `is_pro_user` | **constant `false`** |
| `num_views_loggedin` | **constant `0`** |

`canister_id` is not per-video data — every row returns the same value, which is
literally `profile_canister_id` from ansuman's `influencer_feed.toml`. **So
there is no IC canister client, no chain snapshot, no `yral_posts` join.**

`is_following` and `is_pro_user` already return `false` in production, so
shipping them as constants matches current behaviour exactly — not a
regression, and no billing or follow-graph dependency.

---

## 7. Contract with Saikat's pipeline

**The interface is a ClickHouse table, not a Kafka topic.**

Saikat owns: Snowplow collector → Kafka → `raw_events` → materialised view →
`video_stats_daily (video_id, day, views)`.

We own: reading `video_stats_daily`.

Why a table and not the topic: he stays free to change the bus, the consumer or
the raw schema without breaking us, and the feed wants aggregates rather than a
stream — subscribing directly would mean maintaining aggregation state in the
feed service, which is exactly the complexity we're deleting.

**`video_stats_daily` is one row per video per day**, so it stays small
regardless of event volume. Raw events can carry a TTL; the aggregate doesn't
need one.

---

## 8. What we're deleting

| Ansuman had | We do |
|---|---|
| ClickHouse + kvrocks on `ansuman-1` | Saikat's ClickHouse + our Redis |
| Bloom filters (1M cap, 4× expansion) | a Redis set with a TTL |
| 11 popularity percentile buckets | one score |
| 5 freshness windows, 4 pools | one ranked list |
| 4 score families, ~25 weights | one formula, one knob |
| Following pools, refill locks, percentile pointers | precompute the whole list |
| HMAC-signed internal view-count push | read the aggregate table |
| IC canister + offchain-agent clients | config constant |
| Discovery-boost job, feed mixer, publisher enrichment | dropped |
| Per-request DB hydration | pre-hydrated Redis payload |

**~10,700 lines → an estimated 250.**

---

## 9. Cutover — without breaking the live service

Nothing here touches ansuman's service until the final DNS switch, and the
rollback is a DNS change.

1. **Deploy** at `video.rishi.yral.com`. Ansuman's service runs untouched.
2. **Shadow compare** for a week — call both, log overlap, latency and empty-page
   rate. The bar is "not obviously worse", not identical output.
3. **Repoint** `recsys-influencer-feed.ansuman.yral.com` at the new service.
   **No mobile release** — we own the `yral.com` zone.
4. **Keep ansuman's service reachable** under a different hostname for a week.
5. **Then** decommission `ansuman-1` — but only after the history export (§10)
   is verified.

---

## 10. Prerequisite — the history export

Independent of everything above, and more urgent than any of it.

`ansuman-1` holds ClickHouse database `yral`, which is the **only copy** of:

| Table | What |
|---|---|
| `user_video_relation` | watch history — irreplaceable |
| `global_popular_videos_l7d` | precomputed 7-day popularity |
| `video_unique_v2` | video catalogue |
| `follower_graph` | follows |
| `ai_ugc`, `bot_uploaded_content` | AI/bot content flags |
| `ugc_content_approval`, `excluded_videos` | moderation state |

Export all of it into our own ClickHouse before the box is touched. **Kafka
cannot reach backwards** — Saikat's pipeline covers today forward only.

Backstop if `ansuman-1` is already unrecoverable: mobile ships **Mixpanel and
Firebase** alongside Snowplow, so an independent copy of the event history
exists in both and can be exported.

---

## 11. Build order

| PR | Contents | Size |
|---|---|---|
| 1 | ClickHouse + Postgres readers, merge, score, write `vfeed:ranked` | ~120 |
| 2 | serving endpoint, seen-set, fallback ladder | ~90 |
| 3 | shadow-compare harness + metrics | ~60 |

Nothing is user-visible until the DNS change.

---

## Open questions

1. Does `video_stats_daily` exist in Saikat's ClickHouse yet, or does he need to
   add the materialised view? (Blocks PR1.)
2. Do we read the catalogue from prakash's `video_fingerprint_index` directly,
   or mirror it into our own Postgres first?
3. Is `rec_type` ever anything but `mixed` in a shipped build?
