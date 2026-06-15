# Safety-net drill plan — B6 + H11 + Phase 19.1

**Phase**: 21γ.P29 (the drill execution itself stays Pending until run)
**Date**: 2026-06-16
**Audience**: Rishi (operator) — runs this drill himself this weekend
**Status**: ⚠️ RUNBOOK ONLY — no commands executed by this PR

---

## Why this doc exists

v2 went 100% prod 2026-06-15 via Sarvesh's Firebase Remote Config flip. The **H2 paywall was closed WON'T FIX** on 2026-06-14 based on three claimed safety-net layers covering the original "motivated bypass user → unbounded Gemini cost" risk:

1. **21α.B6** — per-user daily LLM cost circuit breaker
2. **21αβ.H11** — real-time cost alerting (Sentry + daily email digest), shipped 2026-06-09 via #306
3. **Phase 19.1** — per-user rate limiter, live

**None of these have been synthetically triggered end-to-end with real users on the platform.** This doc gives you a turnkey runbook for exercising each one. If any drill fails, the H2 WON'T FIX decision needs revisiting.

---

## ⚠️ Pre-drill finding — read before scheduling

> **21α.B6 cost circuit breaker has NOT shipped.** This is the load-bearing layer in the H2 WON'T FIX rationale and it does not exist as deployed code today.

Evidence:
- `PROGRESS.md` row `21α.B6`: "🔄 DRAFT PR #289 — service layer complete… **Must land before α**"
- **`gh pr view 289` shows CLOSED, not merged**
- `PROGRESS.md` row `21α.B6a` (the chat.py enforcement wire): "⏳ Pending — depends on #289 merge"
- `Phase 19.2` row (cost circuit breaker): "⏳ Pending"
- `grep -r 'cost_breaker\|CostCeilingExceeded\|circuit_open' app/`: **zero matches in production code**
- `app/services/email_digest.py:170`: "Cost circuit breaker" listed as a section placeholder (`_section_placeholder` — content TBD)
- `app/routes/admin_dashboard.py:391`: "Cost circuit breaker" tile is a placeholder

**Implication.** The H2 WON'T FIX safety net is currently a 2-of-3 stack:
- ✅ H11 (live)
- ✅ Phase 19.1 (live)
- ❌ B6 (not shipped)

If a motivated bypass user pushes cost past the H11 hourly threshold ($10/hr default), H11 fires a Sentry alert — but there is **no automatic enforcement** stopping subsequent LLM calls. H11 is detect-only.

**Recommendation before this weekend's drill**: surface the gap to Rishi. Two paths forward:

- **Path 1 (this drill stays valid)**: Section B in this doc becomes a "drill we cannot execute, here's the gap." Sections A + C execute as designed. The H2 WON'T FIX decision is re-anchored on H11 detect + Phase 19.1 rate limit (which IS automatic enforcement) + manual Rishi response to Sentry alert.
- **Path 2 (ship B6 first)**: pause this drill until B6 actually lands. Per PROGRESS.md, that's a 1-day implementation tracked under 21α.B6 + 21α.B6a.

This doc proceeds assuming Path 1 — Section B is documented as a non-executable placeholder so the moment B6 ships, this runbook is ready.

---

## Drill structure

Each section follows the same shape:
1. **What we're proving** — the one-sentence claim
2. **Where the code lives** — file + line ranges
3. **Pre-condition read-only checks** — verify the system is in a clean state BEFORE you trigger
4. **Synthetic trigger command** — exact command to run
5. **Expected signal** — what you should see, and within what time
6. **Rollback** — undo the trigger when done
7. **Failure modes** — what each failure tells you

**Do not execute multiple sections back-to-back without cleanup.** Each drill changes runtime state (env vars, DB rows, Redis keys); roll back fully before moving to the next.

---

## Section A — H11 cost alerting drill

### What we're proving

When the hourly Gemini-cost rolling sum exceeds the `COST_ALERT_HOURLY_GEMINI_USD` threshold within a 60-second tick, the loop fires a Sentry `capture_message` with the alert + the daily 08:00 IST email digest includes the same incident the next morning.

### Where the code lives

- **Module**: `app/services/cost_alerts.py` (196 lines)
- **Main loop**: `cost_alerts_loop()` — registered in `app/main.py:117-119`
- **Hourly Gemini check**: `cost_alerts.py:127` (`if cost > COST_ALERT_HOURLY_GEMINI_USD`)
- **Sentry wrapper**: `cost_alerts.py:96-105` (`_sentry_warn` — never raises)
- **Tick interval**: `COST_ALERT_TICK_SEC` default 300s (env-overridable)
- **Default threshold**: `COST_ALERT_HOURLY_GEMINI_USD` default 10.0 (env-overridable)
- **NX dedupe**: Redis key per UTC hour bucket, 1h TTL — prevents flooding Sentry
- **Email digest call site**: `app/services/email_digest.py` (adds a `cost_alerts` section to the daily 08:00 IST digest)
- **Kill switch**: `kill_switch.py:62` entry `"cost_alerts": "ENABLE_COST_ALERTS"` — must be enabled

### Pre-condition read-only checks

```sh
# (1) cost_alerts loop is actually running on the deployed image
ssh rishi-deploy@rishi-4 "docker service logs --since 10m yral-rishi-agent 2>&1 | grep -i 'cost_alerts'" | head -20
# expected: at least one "cost_alerts: ..." log line in the last 10 min (the tick loop log)

# (2) the kill switch is ENABLED — drill is no-op if disabled
ssh rishi-deploy@rishi-4 "docker exec yral-rishi-agent-... env | grep ENABLE_COST_ALERTS"
# expected: ENABLE_COST_ALERTS=true (or unset, which defaults to enabled per kill_switch.py)

# (3) current threshold value (so you know what to compare against)
ssh rishi-deploy@rishi-4 "docker exec yral-rishi-agent-... env | grep COST_ALERT_HOURLY_GEMINI_USD"
# expected: COST_ALERT_HOURLY_GEMINI_USD=10.0 (default) or whatever Rishi has set

# (4) Sentry connectivity from the container — no point firing if Sentry is unreachable
ssh rishi-deploy@rishi-4 "docker exec yral-rishi-agent-... curl -sS -o /dev/null -w '%{http_code}\\n' https://sentry.rishi.yral.com/api/0/"
# expected: 200 or 401 (401 still proves connectivity; 401 just means no creds on the bare curl)

# (5) Capture baseline llm_costs hourly sum so you know your delta
ssh rishi-deploy@rishi-4 'docker exec yral-rishi-agent-... psql -U postgres -d yral_agent_db -c "
  SELECT date_trunc(\"hour\", created_at) AS h, SUM(cost_usd)::numeric(10,4) AS hourly_cost
  FROM llm_costs WHERE created_at > NOW() - INTERVAL \"3 hours\" AND provider=\"gemini\"
  GROUP BY 1 ORDER BY 1 DESC LIMIT 3;
"'
# expected: 3 recent hourly buckets, each well under threshold
```

### Synthetic trigger

The cleanest path is to lower the threshold via env, not to fire artificial cost rows (which would pollute `llm_costs` analytics):

```sh
# Lower threshold to $0.01 so the next tick sees real traffic exceed it
ssh rishi-deploy@rishi-4 'docker service update --env-add COST_ALERT_HOURLY_GEMINI_USD=0.01 yral-rishi-agent'

# Wait ~5 min for service to roll + next tick (COST_ALERT_TICK_SEC=300)
# Then send 1-2 chats via the alpha Motorola to ensure llm_costs records ≥1 row
# (real traffic on prod should already be > $0.01/hr so the tick will trigger
# without any synthetic load)
```

### Expected signal — within 5-10 min of the threshold lowering

**Sentry**:
- Open https://sentry.rishi.yral.com
- Search for message starting with `"Hourly Gemini cost $"`
- Expect: 1 event in the last 10 min, level `warning`, message format `"Hourly Gemini cost $X.XX exceeded $0.01/hr threshold (Y calls in last hour)"`
- Sentry tags: `level=warning`, no extra tags by default (per `_sentry_warn` impl)
- Confirm via the Sentry sidebar: under the same UTC hour bucket, only ONE event (NX dedupe working). Multiple events = dedupe broken; investigate Redis connectivity from the container.

**Email digest** (next morning's 08:00 IST):
- Subject line includes the same cost alert
- Body has a `Cost alerts` section listing the breach with timestamp + final $ amount + call count
- If the digest doesn't arrive, check (a) `email_digest_loop` registered in main.py lifespan, (b) SMTP creds (Postmark/Sendgrid) configured, (c) Sentry shows no exception from the digest loop overnight

### Rollback

```sh
# Restore threshold to the original value (default 10.0; check pre-condition step 3 for what was set)
ssh rishi-deploy@rishi-4 'docker service update --env-add COST_ALERT_HOURLY_GEMINI_USD=10.0 yral-rishi-agent'

# Confirm Sentry no longer fires — next tick (~5 min) should be clean
# Confirm tomorrow's email digest is clean (no leftover alert from the drill)
```

### Failure modes

| Symptom | What it tells you |
|---|---|
| No Sentry event within 10 min | Either the loop isn't running (check main.py:117 wiring + cost_alerts service log), OR the tick interval is mis-set (check `COST_ALERT_TICK_SEC`), OR Sentry connectivity broken |
| Sentry event fires but is duplicated | NX dedupe Redis key not landing — Redis Sentinel issue OR the dedupe key TTL wrong. Check `cost_alerts.py:90` log line |
| Sentry event fires but email digest never arrives | SMTP creds missing OR email_digest loop crashed. Check Sentry for `email_digest_loop` exceptions overnight |
| Sentry event has wrong threshold in the message | Service roll didn't pick up the env change. Run `docker service ps yral-rishi-agent` and confirm the new tasks are running, not the old ones |
| Multiple events in same UTC hour | NX dedupe broken. Investigate Redis connectivity from the container |

---

## Section B — B6 cost circuit breaker drill

### ⚠️ STATUS: not shipped — drill cannot execute as designed

Per the pre-drill finding at the top of this doc, B6 has never landed. PR #289 (the service layer) was closed unmerged; 21α.B6a (the chat.py enforcement wire) remains pending.

This section stays in the runbook so the moment B6 ships, this drill is ready. **DO NOT attempt the synthetic trigger today — there's nothing to trigger.**

### What we want to prove (when B6 ships)

When a user's daily LLM cost crosses the per-user ceiling, the circuit opens — subsequent LLM calls return 402 (`CostCeilingExceeded`) until the breaker auto-resets at the next UTC day boundary or via admin reset.

### Where the code WILL live (when shipped)

- **Service layer**: planned for `app/services/cost_breaker.py` (per PR #289 design)
- **Route enforcement**: `app/routes/chat.py` after auth + before LLM call — `CostCeilingExceeded → 402` (the 21α.B6a wire)
- **Admin endpoint**: planned for `/admin/cost-breaker` — list open breakers + manual reset
- **Storage**: per-user counters in Redis with UTC-day rollover, mirror to `llm_costs` for audit

### Pre-condition (when B6 ships)

```sh
# Verify the breaker service is loaded
ssh rishi-deploy@rishi-4 "docker exec yral-rishi-agent-... python -c 'from services import cost_breaker; print(cost_breaker.__file__)'"

# Current ceiling
ssh rishi-deploy@rishi-4 "docker exec yral-rishi-agent-... env | grep COST_BREAKER_PER_USER_DAILY_USD"

# Currently-open breakers (should be empty)
curl -s https://agent.rishi.yral.com/admin/cost-breaker -H "Authorization: Bearer $ADMIN_JWT"
```

### Synthetic trigger (when B6 ships)

```sh
# Lower per-user daily ceiling to $0.01 + send 2-3 chats from a test principal
ssh rishi-deploy@rishi-4 'docker service update --env-add COST_BREAKER_PER_USER_DAILY_USD=0.01 yral-rishi-agent'

# Send chats from a known test principal via alpha Motorola
# Expect: first 1-2 chats succeed; subsequent chats return 402 CostCeilingExceeded
```

### Expected signal (when B6 ships)

- First 1-2 chats: 200 + assistant reply
- Subsequent chats: **402 with body `{"error": {"code": "cost_ceiling_exceeded", "message": ...}}`** (or whatever envelope ships)
- `/admin/cost-breaker` lists the test principal with open breaker + open-at timestamp
- Sentry capture (if wired): one event per breaker-open transition (NOT per blocked call — that would flood)
- Per-user breaker auto-resets at next UTC day OR via `POST /admin/cost-breaker/{principal_id}/reset`

### Rollback

```sh
# Restore ceiling
ssh rishi-deploy@rishi-4 'docker service update --env-add COST_BREAKER_PER_USER_DAILY_USD=10.0 yral-rishi-agent'

# Manual reset of the test principal's breaker
curl -X POST https://agent.rishi.yral.com/admin/cost-breaker/$TEST_PRINCIPAL/reset -H "Authorization: Bearer $ADMIN_JWT"

# Confirm test principal can chat again
```

### Failure modes (when B6 ships)

| Symptom | What it tells you |
|---|---|
| Subsequent chats succeed (no 402) | Breaker DETECTS but doesn't ENFORCE — 21α.B6a wire missing |
| 402 fires but admin endpoint doesn't list the breaker | State storage broken; Redis writes not persisting |
| 402 fires + admin lists it + breaker doesn't auto-reset at UTC day | Day-rollover logic broken; check the breaker service's rollover function |
| Sentry floods with one event per blocked call | Transition vs steady-state logic wrong; should fire only on open transition |

### What to do right now (since B6 hasn't shipped)

**Surface to Rishi**: the H2 WON'T FIX safety-net trio is currently 2-of-3 (H11 detect + 19.1 enforce). B6 is a planned automatic-enforcement layer that hasn't landed. Two practical options:

1. **Ship B6 first** (1 day per 21α.B6 + 21α.B6a estimate) so the drill can run.
2. **Accept B6 as a future enhancement** + lean on H11 alert + manual response + 19.1 rate limit as the actual safety net today.

Either way, this section in the doc is ready for the moment B6 lands.

---

## Section C — Phase 19.1 per-user rate limiter drill

### What we're proving

When a single principal sends more than the per-minute rate limit (default per `RATE_LIMIT_PER_MINUTE`), subsequent requests return 429 with `Retry-After` until the minute bucket rolls over.

### Where the code lives

- **Middleware**: `app/rate_limiter.py:264` — `class RateLimitMiddleware`
- **Registered**: `app/main.py:418` — `app.add_middleware(RateLimitMiddleware)`
- **Config storage**: `rate_limit_config` Postgres table (migration 025) + mirrored to Redis under `rate:config:<key>` for O(1) middleware reads
- **Limit defaults**: `RATE_LIMIT_PER_MINUTE` default 300, `RATE_LIMIT_PER_HOUR` default 5000 (per `config.py:90-91`)
- **Sliding window**: fixed-bucket approximation (per UTC minute) — over-counting near minute boundaries is by design (defense-not-correctness control)
- **Per-user keying**: JWT principal_id; per-IP keying is separate so unauthenticated abusers still get stopped
- **Degrade-open**: middleware logs warning + lets the request through if Redis is unreachable
- **429 response**: `app/rate_limiter.py:320-325` — `status_code=429` with `Retry-After` header set to the seconds remaining in the current minute

### Pre-condition read-only checks

```sh
# (1) Middleware is wired
ssh rishi-deploy@rishi-4 "docker exec yral-rishi-agent-... python -c 'from main import app; print([type(m).__name__ for m in app.user_middleware])'"
# expected: RateLimitMiddleware in the list

# (2) Current per-user limits (from rate_limit_config OR env defaults)
ssh rishi-deploy@rishi-4 'docker exec yral-rishi-agent-... psql -U postgres -d yral_agent_db -c "SELECT key, value FROM rate_limit_config WHERE key LIKE \"per_user%\";"'
# expected: rows for per_user_per_minute + per_user_per_hour OR empty (means env defaults apply)

# (3) Redis is reachable from the container (degrade-open would mask the drill otherwise)
ssh rishi-deploy@rishi-4 "docker exec yral-rishi-agent-... python -c 'import asyncio; from rate_limiter import _redis_for_test; print(asyncio.run(_redis_for_test()))'"
# (if no _redis_for_test helper, swap for any quick redis-cli probe)

# (4) Pick a test principal that's NOT a real alpha user
# Use a fresh JWT from auth.dolr.ai for a throwaway principal
TEST_JWT=...
TEST_PRINCIPAL=...

# (5) Capture the baseline for that principal's current minute bucket count (should be 0)
# Inspect Redis directly:
ssh rishi-deploy@rishi-4 "docker exec redis-primary redis-cli --pass $REDIS_PASS GET rate:user:$TEST_PRINCIPAL:$(date -u +%Y-%m-%dT%H:%M)"
# expected: nil (no prior count)
```

### Synthetic trigger

The cleanest path is to LOWER the per-minute limit for one minute, then fire a burst from a test principal:

```sh
# Lower per-user-per-minute to 5 (so a 10-request burst is guaranteed to trip)
curl -X PUT https://agent.rishi.yral.com/admin/rate-limits \
    -H "Authorization: Bearer $ADMIN_JWT" \
    -H "Content-Type: application/json" \
    -d '{"per_user_per_minute": 5}'

# Verify the change propagated to Redis
ssh rishi-deploy@rishi-4 "docker exec redis-primary redis-cli --pass $REDIS_PASS GET rate:config:per_user_per_minute"
# expected: "5"

# Fire 10 chats in 30s from the test principal
for i in $(seq 1 10); do
    curl -s -o /dev/null -w "%{http_code} " https://agent.rishi.yral.com/api/v1/influencers \
        -H "Authorization: Bearer $TEST_JWT"
    sleep 2
done
echo
```

(Read-only endpoint `/api/v1/influencers` is safer than `/messages` for the drill — proves the rate limit middleware works without burning Gemini cost. Use `/messages` only if you want to also smoke the chat path.)

### Expected signal

- First 5 requests: `200 200 200 200 200`
- Requests 6-10: `429 429 429 429 429`
- Each 429 response has `Retry-After: NN` header (seconds until next minute boundary)
- 429 response body matches the standard envelope (likely `{"detail": "rate limit exceeded"}` per `_rate_limit_response` in rate_limiter.py — verify exact shape in code)
- After 60s (new minute bucket), requests succeed again

### Rollback

```sh
# Restore per-user-per-minute to default 300
curl -X PUT https://agent.rishi.yral.com/admin/rate-limits \
    -H "Authorization: Bearer $ADMIN_JWT" \
    -H "Content-Type: application/json" \
    -d '{"per_user_per_minute": 300}'

# Confirm Redis mirror updated
ssh rishi-deploy@rishi-4 "docker exec redis-primary redis-cli --pass $REDIS_PASS GET rate:config:per_user_per_minute"
# expected: "300"

# Confirm test principal can hit endpoints freely again
curl -s -o /dev/null -w "%{http_code}\n" https://agent.rishi.yral.com/api/v1/influencers \
    -H "Authorization: Bearer $TEST_JWT"
# expected: 200
```

### Failure modes

| Symptom | What it tells you |
|---|---|
| All 10 requests succeed (no 429) | Middleware not running OR new limit didn't propagate (check Redis `rate:config:per_user_per_minute` value). Could also be degrade-open kicking in — check container logs for `rate_limiter: ...degrading open` |
| 429 fires but `Retry-After` header missing | `_rate_limit_response` builder broken; check rate_limiter.py:320 |
| 429 fires immediately on request 1 | The lowered limit propagated but the test principal already had ≥5 requests this minute. Either wait for the minute to roll or pick a fresher principal |
| 429 keeps firing >60s past the burst | Redis bucket key TTL not set properly; check the `_record_hit` function (or equivalent) for `EXPIRE` call |
| Drill works but alpha team can't chat afterwards | The rollback PUT didn't propagate. Re-check Redis `rate:config:per_user_per_minute` value. If stuck at 5, manually `redis-cli SET rate:config:per_user_per_minute 300` |

### Phase B — friendly UX check (mobile-side)

The drill's signal so far is HTTP 429 + JSON body. Worth also checking what the mobile app DOES when it gets a 429 from chat-send:

```sh
# Use the alpha Motorola pointed at agent.rishi.yral.com
# With a test principal at lowered-limit, try to send 6+ chats in 60s
# Expected: 5 succeed + last few show "Try again in a few seconds" or
#   similar friendly UX, NOT a generic crash
```

If the mobile UX is "Message failed to send" with no retry guidance, that's a mobile-side gap worth filing as a follow-up (`21γ.P31`-style). The drill's pass signal is the 429 + Retry-After at the server level; the mobile UX is a separate quality bar.

---

## Drill-day checklist (in order)

- [ ] Read the **pre-drill finding** at the top of this doc — confirm with Rishi the Section B handling (skip vs ship first)
- [ ] Schedule a low-traffic window (02:00-05:00 UTC ideal)
- [ ] Pre-flight: `/admin/backup-health` is GREEN; Patroni 3/3 healthy; Sentry connectivity OK
- [ ] Take a fresh pg_dump as belt-and-braces (Rule 9)
- [ ] **Section A (H11 cost alerting)**: pre-conditions → trigger → wait 10 min → check Sentry → wait until next 08:00 IST for email digest → rollback
- [ ] **Section B (B6 cost breaker)**: SKIP per pre-drill finding; document the gap in DAILY-LOG + decide next step with Rishi
- [ ] **Section C (Phase 19.1 rate limiter)**: pre-conditions → trigger → check 429 + Retry-After → rollback → Phase B mobile UX check
- [ ] Write up the drill report in DAILY-LOG.md: timestamps, what fired, any unexpected behaviour
- [ ] Flip PROGRESS.md row `21γ.P29` from ⏳ Pending → ✅ Done with date + drill report reference
- [ ] If any drill FAILED: surface to Rishi immediately + decide whether H2 WON'T FIX still stands

## What this doc does NOT cover

- **Actual execution** — Rishi runs the drill himself this weekend per the brief. This is a runbook, not an automation.
- **Production rollback for prod breakage** — covered separately in cutover playbook (`docs/runbooks/cutover-day-mini-rebootstrap.md`)
- **Network partition + split-brain drills** — separate concern (H4 + H5 cover failover; partition needs its own design)
- **Mobile-side telemetry** — `21γ.P31` covers the broader audit (Crashlytics/Sentry-mobile wired?)

## Cross-references

- **PR #394** — Langfuse trace input/output rollup fix (Task A from the same overnight brief)
- **PR #392** — H2 server-side paywall revert (the decision this drill plan is validating)
- **PR #391** — H2.2 Phase 1 chat-ai paywall discovery doc
- **`docs/runbooks/walg-restore-drill.md`** — runbook style template this doc mirrors
- **`PROGRESS.md`** rows `21α.B6`, `21α.B6a`, `21αβ.H11`, `19.1`, `21γ.P29`
