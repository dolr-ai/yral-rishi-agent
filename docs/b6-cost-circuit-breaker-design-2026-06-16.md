# B6 — Cost circuit breaker design

**Date**: 2026-06-16
**Phase**: 21α.B6 (re-scoped from closed-unmerged PR #289)
**Status**: ⚠️ Design doc only — NO implementation today. Rishi reviews + decides 3 open questions in §13 before code lands.
**Stacked on**: PR #395 (drill plan doc) → PR #394 (Langfuse trace fix) → PR #393 (PROGRESS/DAILY-LOG surface)
**Audience**: Rishi (reviewer)

---

## TL;DR — 5-bullet executive summary

1. **Hybrid threshold scoping**: per-user-daily ($1 default, from #289) AND global-hourly ($20 default, 2× H11's alert threshold so we see H11 fire before we block). Either trip = block. Per-user catches a single bypass user; global catches distributed attack / system-wide leak.
2. **No new aggregation infrastructure — reuse H11's `llm_costs` SQL pattern.** Same Postgres source of truth that H11 already aggregates from; cached in Redis with 10s TTL so we don't hammer Postgres on every LLM call. Latency budget <5ms — Redis-hit is sub-ms, Postgres-fallback ~5-10ms but rare.
3. **Check lives in `app/services/llm_registry.py` (the LLM chokepoint), NOT chat.py.** Same gate H11 uses for measurement. Protects EVERY LLM call regardless of route — chat AND background (proactive, scoring, summarization). Middleware-in-chat.py would miss the background paths.
4. **Hot-editable via `circuit_breaker_config` table + Redis cache (60s TTL)** mirroring the proven Phase 19.1 rate-limiter pattern. Three knobs: `b6_enabled` (master kill, ships `false`), `b6_enforce` (shadow vs enforce, ships `false`), `b6_per_user_daily_usd` + `b6_global_hourly_usd` thresholds. A single SQL UPDATE disables B6 in 1 second, no redeploy.
5. **Shadow mode for ≥7 days**: logs "would-have-tripped" events to `circuit_breaker_events` table + Sentry breadcrumb + dashboard tile. NEVER blocks while shadow. Only flip `b6_enforce=true` after empirical proof of zero false positives. Mobile response (in enforce mode): **503 + Retry-After: 3600** — mobile already handles 503 cleanly via its generic "Try again later" UI. NO new error envelope, NO 402 (H2's mistake), NO 429 (wrong semantic).

---

## Why this doc exists

The 2026-06-14 H2 paywall WON'T FIX decision rested on a 3-layer safety net:

- ✅ H11 cost alerting (#306, live) — DETECT only
- ✅ Phase 19.1 per-user rate limiter (live) — ENFORCE (request count)
- ❌ B6 cost circuit breaker — **never shipped**

This morning's safety-net drill plan (PR #395) surfaced the gap: B6's draft PR #289 was closed 2026-06-08 in favor of a "fresh rebuild" after PR #293 changed the substrate (`llm_registry.call()` shape moved into a new `_do_complete()` helper handling primary + fallback). The breaker would need to gate BOTH attempts; #289 only gated the primary.

At 100% prod, the trio is **2-of-3**. A motivated bypass user could push cost past H11's $10/hr alert and continue burning Gemini budget while Rishi reads the Sentry email. This doc designs the missing ENFORCE layer + spec's it so it CAN'T repeat H2's "broke alpha team on first deploy" failure mode.

---

## Reading the H2 incident as a constraint on B6

H2 paywall (#380 + #389 + #390, reverted via #392) broke alpha on 2026-06-14:
- billing.yral.com returned `expires_at: null` for non-subscribers (= "never had a plan," NOT "expired")
- H2 read that as "no access" → returned 402 to every authenticated user
- Mobile had no 402 parser → generic "Message failed to send" → 100% of users blocked
- Rolled back within hours; H2 closed WON'T FIX

**The 5 hard properties this design must respect to not repeat that incident:**

1. **DEFAULT OPEN** — circuit ships in closed-circuit (allow-traffic) state. Only opens on a real cost-spike signal. Even if config missing or wrong on first deploy, traffic flows normally.
2. **FAIL OPEN ON ERRORS** — B6's own DB query, threshold check, Redis lookup all → ALLOW the LLM call on any error. Never block on tooling failure.
3. **HOT-EDIT KILL SWITCH** — Postgres config row `b6_enabled BOOLEAN`. A single UPDATE disables B6 globally in 1 second. No restart, no deploy. Pair with hot-edit threshold (raise on the fly if B6 trips legitimately).
4. **SHADOW MODE FIRST** — first deploy ships with `b6_enforce=false`. B6 logs "would have tripped" events to a dedicated table without blocking. Only flip to enforce after ≥7 days of shadow data proving no false positives.
5. **MOBILE-SAFE RESPONSE SHAPE** — when B6 trips (enforce mode), response is one mobile already handles. NEVER a new error code mobile hasn't seen.

These constraints are load-bearing on every design decision below.

---

## Research findings

### PR #289 (closed unmerged) — what was salvageable

**Proposed architecture (per PR body):**
- New `app/services/llm_cost_breaker.py` (~170 LOC, service layer)
- Redis counter per `(user_id, UTC day)` — incremented post-call via cost-recording hook
- Pre-call check in `llm_registry.call()` rejected with `CostCeilingExceeded`
- Default ceiling $1.00/user/day, env-overridable via `LLM_PER_USER_DAILY_CEILING_USD`
- Fail-open on Redis-unreachable
- Sentry alert on ceiling-hit, NX-deduped per user-day
- 10 source-pin tests

**Why closed (Rishi's 2026-06-08 comment):**
> Closing in favor of a fresh rebuild after PR #293 changed the substrate.
> Dispatch + cost-recording moved into a new `_do_complete()` helper
> Primary + fallback paths share `_do_complete()` — the breaker needs to gate BOTH attempts, not just primary

**Salvageable**: the threshold concept (per-user-daily), the fail-open posture, the NX-deduped Sentry alerting pattern.
**Not salvageable**: the chokepoint location (was `llm_registry.call()` directly; now needs to be inside `_do_complete()` or above the primary/fallback split), the Redis-counter as primary state (we'll use Postgres aggregation instead — see §2 below), the env-var-only config (we'll use hot-edit table).

**Explicit gaps PR #289 noted as follow-up (still owed):**
1. Admin PATCH `/admin/cost-ceiling/{user_id}` — we'll do this via the hot-edit config table instead
2. Per-user override table — deferred; ships with global threshold first
3. 19.6 dashboard tile — yes, with the shadow event count
4. `CostCeilingExceeded → 402` wiring in `chat.py` (the 21α.B6a row) — replaced by 503 response per §7
5. Live-Redis integration test — addressed by §10

### Phase 19.1 rate limiter (`app/rate_limiter.py`) — proven hot-edit pattern

The pattern to mirror:
- DB config table: `rate_limit_config(key VARCHAR PRIMARY KEY, value, updated_at, updated_by)` (migration 025)
- Mirrored to Redis hash `rate:config` so middleware reads O(1)
- `hydrate_from_db(pool)` at startup populates Redis
- `update_limit(pool, key, value, updated_by)` writes BOTH DB (durable, first) + Redis (live cache, second)
- DEFAULTS in code: cold-start fallback if Redis empty/down
- `SKIP_PREFIXES` for `/health`, `/admin/`, `/ws/` — observability paths always work even if limiter is broken
- **DEGRADE OPEN: any Redis failure → middleware lets request through with warning log**
- Counter math: minute + hour bucket keys with 2× TTL for clock skew

B6 reuses all of this verbatim except the counter math (we use Postgres `llm_costs` aggregation, not per-request Redis INCR — see §2).

### H11 cost alerting (`app/services/cost_alerts.py`) — proven aggregation pattern

The pattern to reuse for the actual cost rollup:

```sql
SELECT COALESCE(SUM(cost_usd), 0)::float AS cost_usd,
       COUNT(*)::int                     AS call_count
FROM llm_costs
WHERE provider = 'gemini'
  AND created_at > now() - interval '1 hour'
```

H11's docstring explicitly flags the upgrade path B6 should take:
> If hot-editing without redeploy becomes important, the follow-up is the rate_limiter pattern: a DB-config table + Redis cache that the loop re-reads each tick.

B6 IS that follow-up — applied for the same `llm_costs` source but with per-user scoping added.

### Redis caching pattern

Both 19.1 + H11 use `_get_redis()` from `redis_config.get_redis_url()` (file-first Swarm secret, env fallback). B6 uses the same.

---

## 1. Threshold scoping — hybrid (per-user + global)

**Two thresholds running in parallel:**

| Scope | Default | Purpose |
|---|---|---|
| Per-user-daily | `$1.00 / user / UTC-day` | Catches a single bypass user. Mirrors #289's choice. |
| Global-hourly | `$20.00 / hr` | Catches distributed attack / system-wide leak. **2× H11's $10/hr alert threshold** so H11 fires (Sentry alert) BEFORE B6 blocks anyone. |

Either threshold trip = block.

**Trade-off vs alternatives:**

- **Per-user only**: misses a distributed attack (many user IDs each just under their cap = global cost runaway invisible). Worse: doesn't protect background processes that aren't keyed to a user.
- **Global only**: a single $400/hr-burn user gets nicked alongside everyone else; unfair UX for innocent users while the offender is the one to be stopped.
- **Per-org**: yral has 1 org today; same as global. Re-enable if/when org tenants exist.
- **Per-process**: H11 already alerts on per-process errors (`async_error_spike`); B6 doesn't need to duplicate.

Hybrid wins: each threshold catches what the other can't.

**Future extensibility**: schema supports per-user override rows for high-trust users (e.g. internal accounts) without a migration. Not built in v1; flagged for follow-up after shadow data shows actual user distribution.

---

## 2. Cost-window arithmetic — Postgres SQL aggregation (NOT Redis counter)

**Decision**: read from `llm_costs` table directly, cache result in Redis with 10s TTL.

```sql
-- Per-user-daily check
SELECT COALESCE(SUM(cost_usd), 0)::float AS spent_today
FROM llm_costs
WHERE user_id = $1
  AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC');

-- Global-hourly check
SELECT COALESCE(SUM(cost_usd), 0)::float AS spent_this_hour
FROM llm_costs
WHERE created_at > now() - interval '1 hour';
```

**Why SQL aggregation, not Redis counter (PR #289's approach):**

- **Single source of truth**: H11 already reads `llm_costs`. Two state stores (Redis counter + Postgres ledger) drift over time — what does the user see when they don't match?
- **Cost ledger is durable**; Redis counter can be lost on Redis restart.
- **PR #289's breakage** was that primary + fallback both increment, so the counter is wrong when both fire (double-counted) or when the fallback bypasses post-cost-recording. Reading from the canonical ledger sidesteps this entirely.
- **Rolling window matches user intent better**: "last 1 hour rolling" is what an operator means by "cost in the last hour" — calendar-hour buckets would let an attacker time bursts to span the boundary.

**Cache layer to hit the latency budget:**

| Key | Value | TTL |
|---|---|---|
| `cb:user_daily:{user_id}` | `cost_usd` numeric | 10s |
| `cb:global_hourly` | `cost_usd` numeric | 10s |

10s TTL trade-off: a user can spend within the window before the cache refreshes. For our cost-circuit-breaker purpose, that's fine — we're catching $10/hr leaks, not single-cent precision. Worst-case slip: 10s × $X/sec from one user. If a user can spend > $0.10 in 10s (~$36/hr) we have bigger problems and H11 catches it.

**Required index for fast aggregation:**

```sql
CREATE INDEX IF NOT EXISTS idx_llm_costs_user_recent
    ON llm_costs (user_id, created_at DESC)
    WHERE user_id IS NOT NULL;
```

Per-user-daily query bounded to a few rows per call (~10-100 chats/user/day in steady state) → sub-millisecond on a populated index.

**Performance budget:**

| Path | p50 latency |
|---|---|
| Cache hit (Redis GET) | <1ms |
| Cache miss → Postgres aggregation (per-user) | ~3ms |
| Cache miss → Postgres aggregation (global) | ~5ms |
| Total added to chat-send (worst case) | <10ms |
| Total added to chat-send (steady state, cache hot) | <2ms |

Comfortably under the brief's <5ms steady-state budget.

---

## 3. Where the check lives — `llm_registry._do_complete()`

**Decision**: place the gate inside `_do_complete()` (the post-PR-#293 chokepoint shared by primary + fallback) — NOT in chat.py middleware.

**Why the registry, not chat.py:**

| Path | Routes through chat.py? | Routes through `llm_registry`? |
|---|---|---|
| `POST /messages` user chat | yes | yes |
| `POST /messages/stream` SSE | yes | yes |
| `POST /images` image gen | yes | yes (Gemini for prompt-gen step) |
| Coach session `coach_reply()` | no (creator_coach.py) | yes |
| Coach session `coach_opening()` | no | yes |
| `proactive_generation` loop | no (background) | yes |
| `bot_quality_scorer` | no (background) | yes |
| Influencer summary cache | no | yes |
| Skill check-ins | no | yes |
| ETL chat-ai sync | no | n/a (no LLM calls) |

**chat.py middleware would miss 6+ paths.** Background processes can leak Gemini cost just like a bypass user; B6 must gate them too. The `llm_registry._do_complete()` chokepoint is the lowest common ancestor of every LLM call.

**Specific insertion point:** at the top of `_do_complete()`, before the primary attempt. Check returns `(allowed: bool, reason: str)`. If not allowed, return a cost-breaker `LlmResponse` synthetically (same shape as a fail) so the caller path stays uniform.

**Bypass for kill-switch'd processes:** `_do_complete()` already knows the `process` name. The config table includes an optional `b6_process_allowlist` (CSV) that bypasses the check for named processes (e.g. internal admin tools). Default: empty (all gated).

---

## 4. Kill-switch table — schema + hot-edit semantics

**Schema** (migration 040 — see §8):

```sql
CREATE TABLE IF NOT EXISTS circuit_breaker_config (
    key            VARCHAR(64)  PRIMARY KEY,
    value          TEXT         NOT NULL,
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by     VARCHAR(255) NOT NULL DEFAULT 'system'
);
```

**Seed rows** (also in migration 040):

| key | value | purpose |
|---|---|---|
| `b6_enabled` | `false` | **MASTER kill switch.** False = B6 module loaded but does nothing; calls pass through. The DEFAULT OPEN posture. |
| `b6_enforce` | `false` | False = shadow mode (log but allow). True = block on threshold trip. **Stays false until ≥7 days of shadow data proves no false positives.** |
| `b6_per_user_daily_usd` | `1.0` | Per-user daily $ threshold. Matches PR #289 default. |
| `b6_global_hourly_usd` | `20.0` | Global hourly $ threshold. 2× H11's $10/hr alert. |
| `b6_process_allowlist` | `` (empty) | CSV of `llm_registry` process names that bypass B6. |
| `b6_cache_ttl_sec` | `10` | Redis cache TTL on the cost-rollup values. |
| `b6_response_retry_after_sec` | `3600` | `Retry-After` header value on the 503 response when B6 trips. 1 hour = enough to outlast the per-user-daily window if it's the trip cause. |

**Hot-edit path** (mirrors `rate_limiter.update_limit`):

```
PATCH /admin/cost-breaker/config
  body: {"key": "b6_enabled", "value": "true"}
  auth: admin JWT
```

Writes DB (durable, first) → Redis (live cache, second). Redis hash key: `cb:config`. 60s TTL on the cache so the worker pool picks up changes within a minute even if a Redis SET fails to fan out.

**The "1-second disable" path:**

```sql
UPDATE circuit_breaker_config SET value = 'false', updated_at = NOW(), updated_by = 'rishi-emergency' WHERE key = 'b6_enabled';
```

Workers re-read on next cache-miss (within 60s) OR via Redis pub/sub invalidation (B6 publishes `cb:config:changed` on every update; workers subscribed). Both paths fail-safe back to allow-traffic.

---

## 5. Shadow mode logging — `circuit_breaker_events` table

**Schema** (migration 040):

```sql
CREATE TABLE IF NOT EXISTS circuit_breaker_events (
    id             BIGSERIAL    PRIMARY KEY,
    occurred_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    user_id        VARCHAR(255),
    process        VARCHAR(64),
    provider       VARCHAR(32),
    scope          VARCHAR(32)  NOT NULL CHECK (scope IN ('per_user_daily', 'global_hourly')),
    cost_seen_usd  NUMERIC(10,4) NOT NULL,
    threshold_usd  NUMERIC(10,4) NOT NULL,
    enforce_mode   BOOLEAN      NOT NULL,
    call_blocked   BOOLEAN      NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cb_events_recent
    ON circuit_breaker_events (occurred_at DESC);
```

**What lands here:**

- Every B6 trip — whether shadow or enforce mode.
- `enforce_mode` tells you whether the system COULD have blocked.
- `call_blocked` tells you whether it ACTUALLY blocked (`enforce_mode=true AND check tripped`).
- In shadow mode: `enforce_mode=false`, `call_blocked=false`, but the row is still written so we know B6 would have tripped.

**Sentry breadcrumb on every shadow event:**

```python
sentry_sdk.add_breadcrumb(
    category="cost-breaker",
    level="warning",
    message=f"B6 shadow trip: {scope} {cost:.4f}/{threshold:.4f} for user={user_id}",
    data={"scope": scope, "user_id": user_id, "cost": cost, "threshold": threshold},
)
```

Breadcrumb (NOT capture_message) because shadow events are expected during calibration — Sentry-page floods would desensitize.

**Dashboard tile** (Phase 19.6 `/admin/dashboard`): "B6 trips last 24h" — count from `circuit_breaker_events WHERE occurred_at > now() - interval '24 hours'`. Cheap query on the partial index.

**Retention**: keep 30 days. After that a daily cron deletes rows older than 30 days. Plenty for shadow-mode calibration + post-incident audits.

---

## 6. Failure modes — fail-open everywhere

| Failure | B6 response | Why |
|---|---|---|
| `circuit_breaker_config` table missing (migration 040 not applied) | Allow the call | DEFAULT OPEN; B6 silently dormant. Same "code can deploy before migration" pattern as PR #260. |
| Config row `b6_enabled` missing | Allow the call | Treat as `false`. |
| Config row `b6_enforce` missing | Default to shadow (log but allow) | Safer than defaulting to enforce. |
| Threshold row missing | Treat as $infinity (allow) | DEFAULT OPEN; B6 never trips. |
| Redis unreachable (config cache) | Fall through to DB read | Slower but functional. |
| Redis unreachable (cost cache) | Fall through to DB aggregation | Slower but functional. |
| Postgres unreachable | Allow the call | We can't measure → don't block. |
| `llm_costs` aggregation returns NULL | Treat as 0 (allow) | `COALESCE` in the SQL handles this. |
| `llm_costs` aggregation raises | Allow the call + log warning + Sentry capture | One log per minute (deduped) so a sustained failure doesn't flood. |
| Shadow-log INSERT raises | Allow the call + log warning | NEVER block on observability failure. |
| `b6_process_allowlist` value malformed (e.g. not CSV) | Treat as empty | All processes gated. |
| Cache TTL value malformed | Default to 10s | In-code fallback. |

**Source-pin test for each path** (~12 tests, all on the source side; behavioural where possible via monkeypatch). The H2 incident proved that fail-closed semantics in any one of these paths breaks production; pinning fail-open on every path is the only defense.

---

## 7. Mobile contract — 503 + `Retry-After` (proposed)

**Recommended shape:**

```
HTTP/1.1 503 Service Unavailable
Retry-After: 3600
Content-Type: application/json

{
  "detail": "Service temporarily unavailable. Please try again later."
}
```

**Why 503:**

- Mobile already handles 503 — any backend hiccup surfaces as 503 today. The mobile UX shows generic "Try again later" which is **the right message** for B6 ("system is taking a break, come back later").
- 503 is HTTP's "I can't serve you right now, retry later." Cost breaker is exactly that.
- `Retry-After: 3600` (1 hour) outlasts the per-user-daily window worst case (if it's the per-user trip that fired, the user will be on a new bucket after the rollover anyway — but the 1-hour minimum prevents tight-loop retry storms).

**Why NOT 429** (the rate-limiter response):

- 429 semantically means "you're hammering us, slow down." B6 trips are NOT about request rate; they're about $ spent. Confusing the two breaks the operator mental model + future telemetry roll-ups ("how many 429s?" vs "how many B6 trips?").
- Phase 19.1 already owns 429. Sharing the response code makes the dashboard tile ambiguous.

**Why NOT 402** (H2's mistake):

- Mobile has no 402 parser. H2 proved this. A new 402 envelope = same broken UX H2 produced. **HARD NO.**

**Why NOT a 200 with status payload:**

- Adding a new shape mobile doesn't parse = guaranteed silent UX bug. The "always 200 with status" pattern works only when mobile already has a status-payload reader for the endpoint — chat-send doesn't.

**OPEN QUESTION for mobile expert (Rishi to forward)**: confirm mobile shows a friendly "Try again later" UI on a chat-send 503. If mobile has divergent behaviour (e.g. silent failure), we need to confirm before flipping `b6_enforce=true`. Documented as `21γ.P31`-class follow-up to surface to mobile expert before enforce-flip.

---

## 8. Migration SQL (040) — additive, backwards-compatible

```sql
-- migrations/040_circuit_breaker.sql
--
-- Phase 21α.B6 — cost circuit breaker config + event log tables.
-- Rule 9 (pg_dump before schema change) applies. Auto-pg_dump runner
-- (PR #309) handles it. 4 prior dumps this week from migrations
-- 033/034/035/036/038/039 prove the path.
--
-- Both tables additive: no impact on any existing query. ALL B6 code
-- gates on `b6_enabled=true` in the config table, which the migration
-- seeds as `false`. So the migration itself doesn't change any
-- runtime behaviour — flipping `b6_enabled=true` via admin endpoint
-- (or SQL UPDATE) is what activates B6.

SET lock_timeout = '3s';
SET statement_timeout = '60s';

CREATE TABLE IF NOT EXISTS circuit_breaker_config (
    key         VARCHAR(64)  PRIMARY KEY,
    value       TEXT         NOT NULL,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by  VARCHAR(255) NOT NULL DEFAULT 'system'
);

INSERT INTO circuit_breaker_config (key, value) VALUES
    ('b6_enabled',                'false'),
    ('b6_enforce',                'false'),
    ('b6_per_user_daily_usd',     '1.0'),
    ('b6_global_hourly_usd',      '20.0'),
    ('b6_process_allowlist',      ''),
    ('b6_cache_ttl_sec',          '10'),
    ('b6_response_retry_after_sec', '3600')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS circuit_breaker_events (
    id             BIGSERIAL    PRIMARY KEY,
    occurred_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    user_id        VARCHAR(255),
    process        VARCHAR(64),
    provider       VARCHAR(32),
    scope          VARCHAR(32)  NOT NULL
                   CHECK (scope IN ('per_user_daily', 'global_hourly')),
    cost_seen_usd  NUMERIC(10,4) NOT NULL,
    threshold_usd  NUMERIC(10,4) NOT NULL,
    enforce_mode   BOOLEAN      NOT NULL,
    call_blocked   BOOLEAN      NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cb_events_recent
    ON circuit_breaker_events (occurred_at DESC);

-- Performance index for the per-user-daily cost rollup.
-- Partial index keeps it slim — only the ~10-100 chats/user/day matter.
CREATE INDEX IF NOT EXISTS idx_llm_costs_user_recent
    ON llm_costs (user_id, created_at DESC)
    WHERE user_id IS NOT NULL;
```

**Backwards-compatible.** No ALTER on existing tables (the `llm_costs` index is a CREATE INDEX, no schema change). No DROP. No DEFAULT on a populated column.

---

## 9. Rollout plan

| Day | Action | Rollback path |
|---|---|---|
| Day 0 (today) | This design doc reviewed by Rishi | n/a — doc only |
| Day 1 | Migration 040 applied (auto-pg_dump runner). `b6_enabled=false`, `b6_enforce=false`. Code ships dormant. | Set `b6_enabled=false` via SQL — it's already false. |
| Day 2 | `UPDATE circuit_breaker_config SET value='true' WHERE key='b6_enabled'` via admin endpoint. **B6 in SHADOW mode.** Cache picks up within 60s. | `UPDATE … SET value='false' WHERE key='b6_enabled'` — 1-second disable. |
| Day 2-9 | Watch `circuit_breaker_events` table. Count shadow trips per user/global per day. | n/a — shadow doesn't block. |
| Day 9+ | If zero or near-zero shadow trips on innocent users: `UPDATE circuit_breaker_config SET value='true' WHERE key='b6_enforce'`. **B6 in ENFORCE mode.** Cache picks up within 60s. | `UPDATE … SET value='false' WHERE key='b6_enforce'` — 1-second back to shadow. |
| Anytime | If false positive observed: `UPDATE circuit_breaker_config SET value='10.0' WHERE key='b6_per_user_daily_usd'` (raise threshold). | Lower threshold via same UPDATE. |
| Anytime | If B6 itself misbehaving: master kill — `UPDATE circuit_breaker_config SET value='false' WHERE key='b6_enabled'`. | n/a — kill switch is the rollback. |

**Operator UX**: every step is a single SQL UPDATE (or one admin PATCH). No `docker service update`, no redeploy, no restart. The full rollback surface is < 5 seconds wall time.

---

## 10. Kill-switch testing

**Unit tests** (no Redis / Postgres needed):

- Cache `b6_enabled=true` + threshold tripped + `b6_enforce=false` → returns `(allowed=True, reason="shadow")`
- Cache `b6_enabled=true` + threshold tripped + `b6_enforce=true` → returns `(allowed=False, reason="per_user_daily")` or `("global_hourly")`
- Cache `b6_enabled=false` (any other config) → returns `(allowed=True, reason="disabled")`
- Cache miss → reads DB → caches result → next call hits cache (1-call round-trip)
- DB query raises → returns `(allowed=True, reason="db_error")`
- Redis cache write raises → returns `(allowed=True, reason="cache_error_but_check_ok")`
- `b6_process_allowlist` includes the calling process → returns `(allowed=True, reason="process_allowlist")`

**Integration test** (against a real Postgres pool, mock Redis):

- Insert `llm_costs` rows totalling $0.50 for a test user. Set `b6_per_user_daily_usd=1.0`. Check returns allowed.
- Insert one more row pushing total to $1.10. Check returns NOT allowed in enforce mode.
- Flip `b6_enabled=false`. Check returns allowed within 60s (cache TTL).

**Behavioural test against the real chat path** (post-deploy in staging-like env):

- Set `b6_enabled=true`, `b6_enforce=true`, `b6_per_user_daily_usd=0.001` from admin endpoint.
- Fire 2 chats from a test principal. Expect: 1st chat 200, 2nd chat 503 + `Retry-After: 3600`.
- Set `b6_enforce=false`. Wait 60s. Fire chat. Expect: 200 + shadow event row in `circuit_breaker_events` with `enforce_mode=false, call_blocked=false`.
- Set `b6_enabled=false`. Fire chat. Expect: 200 + no new shadow event row.
- Test runs in <5 min, fully reversible via the same UPDATEs.

---

## 11. Module surfaces (next-PR scope, NOT this PR)

When implementation lands tomorrow (Rishi's go), the PR will ship:

| File | Lines (est.) | Purpose |
|---|---|---|
| `migrations/040_circuit_breaker.sql` | 50 | Schema + seed rows |
| `app/services/cost_breaker.py` | ~150 | The check + cache + event logging |
| `app/routes/admin_cost_breaker.py` | ~80 | GET/PATCH config + GET events list |
| `app/services/llm_registry.py` | ~10 | Insertion point in `_do_complete()` |
| `app/routes/admin_dashboard.py` | ~15 | Dashboard tile |
| `tests/test_21α_B6_cost_breaker.py` | ~200 | Unit + integration tests |

Total ~500 LOC + 1 migration. Single PR. Will exceed the rule-8 100-LOC checkpoint — flagged for Rishi review at code-PR time per CLAUDE.md rule 8 ("If >100 lines of new code, stop and check with Rishi").

---

## 12. Cross-cutting alignment with existing safety nets

| Layer | What it does | When B6 changes the picture |
|---|---|---|
| **H11 cost alerting** | Detects + Sentry alert on hourly Gemini > $10/hr | Unchanged. H11 still fires the alert. B6 then prevents the user from continuing past $20/hr (global threshold 2× H11's alert). Operator sees Sentry alert FIRST, then B6 acts as the automatic backstop. |
| **Phase 19.1 rate limiter** | Blocks > 60 req/min OR > 1000 req/hr per user (request count) | Unchanged. 19.1 catches request floods regardless of cost. B6 catches cost spikes regardless of request count (e.g. 10 expensive chats/min). The two layers compose: 19.1 catches DOS; B6 catches DOC (denial-of-cost). |
| **B6 cost circuit breaker** | (this design) | The missing ENFORCE layer for cost. Fail-open everywhere; hot-edit kill switch; shadow mode first. |
| **H2 paywall** | (closed WON'T FIX) | If B6 ships clean + shadow-mode-to-enforce path proves no false positives, the H2 WON'T FIX decision is genuinely safe. Otherwise it remains theoretical and we revisit. |

---

## 13. Three open questions for Rishi (decide before implementation)

### Q1 — Per-user-daily threshold default

**Recommendation: $1.00/user/day** (PR #289's choice).

**Alternative**: $5.00 — closer to a single power-user's expected ceiling, fewer shadow trips during calibration.

**Trade-off**: $1 = paranoid; trips fast even on legitimate users. $5 = generous; gives bypass attacker more headroom before the block. Both are operator-adjustable via hot-edit; the question is what shadow mode starts with so we measure realistic-day data.

### Q2 — Mobile response shape

**Recommendation: 503 + Retry-After: 3600** (per §7).

**Required confirmation**: mobile expert verifies mobile shows a friendly "Try again later" UI on a chat-send 503 (and that 503 is what mobile expects on any backend hiccup today). If mobile silently fails on 503 from chat-send → we need a coordinated mobile PR before flipping `b6_enforce=true` (parking the enforce flip until both sides are wired, same lesson as H2).

### Q3 — Shadow mode duration before enforce-flip

**Recommendation: ≥7 days OR ≥1 weekly traffic cycle** (whichever is longer), AND zero shadow trips on the YRAL team's own principals during that window.

**Alternative**: 3 days if no shadow trips fire at all (extreme caution unnecessary if the data is clean) — but that risks calibrating during an atypically quiet week.

**Recommendation rationale**: weekly cycles include both peak + off-peak + weekend traffic patterns. A 3-day window during a quiet Mon-Wed could mask a peak-hour trip. 7 days catches the full cycle.

---

## What this doc does NOT cover

- **Per-user override rows** (high-trust accounts bypass higher thresholds). Deferred to follow-up after shadow mode reveals user distribution. Schema supports adding without migration.
- **Per-provider thresholds** (e.g. block Gemini before OpenRouter because Gemini costs more). Add later if shadow data shows asymmetric leak risk.
- **Auto-rollover detection** ("user just hit threshold, log them out gracefully" UX). Out of scope; mobile-side concern.
- **Cost reconciliation with Google billing** (sanity-check that `llm_costs` matches actual provider invoice). Already handled by H11 + Rishi's morning billing-page glance.
- **Integration with H8 weekly drill workflow** — B6 trip from a synthetic-attack drill should be expected, not a Sentry page. Add to `weekly-security-drill.yml` after enforce-flip.

---

## Branch + PR

- **Branch**: `docs/b6-cost-circuit-breaker-design-2026-06-16`
- **Stacked on**: PR #395 (drill plan doc) → PR #394 (Langfuse trace fix) → PR #393 (PROGRESS/DAILY-LOG surface) → `main`
- **Status**: DRAFT, NOT for merge. Pure design doc — no implementation, no code. Rishi reviews + decides §13 before code lands tomorrow.

## Cross-references

- **PR #289** — closed unmerged; substrate-shift rationale + salvageable concepts
- **PR #306** — H11 cost alerting (`app/services/cost_alerts.py`) — aggregation pattern reused
- **PR #293** — `_do_complete()` substrate that #289's chokepoint missed
- **PR #395** — safety-net drill plan that surfaced this gap
- **PR #392** — H2 paywall revert + WON'T FIX decision that depends on B6 landing
- **PR #391** — H2.2 Phase 1 discovery doc
- `app/rate_limiter.py` — hot-edit config table pattern that B6 mirrors
- `app/services/cost_alerts.py` — `llm_costs` SQL aggregation pattern
- PROGRESS.md rows `21α.B6`, `21α.B6a`, `19.2`, `21γ.P29`
