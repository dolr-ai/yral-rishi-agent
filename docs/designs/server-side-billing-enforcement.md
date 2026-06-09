# Server-side billing paywall enforcement — design

**Status:** Draft for Rishi review. Phase 21αβ.H2 (PROD BLOCKER). Implementation gated on Rishi sign-off of this doc.

## Problem

The current paywall is client-side only. Mobile calls `billing.yral.com` before chat-send and trusts the response. V2's `BILLING_URL` config is unused. A motivated user who hits `agent.rishi.yral.com/api/v1/chat/conversations/.../messages` directly with a valid JWT bypasses the gate entirely → unbounded free chat → unbounded Gemini cost.

This was acceptable for the α cohort (internal team; trust boundary holds) but is a PROD BLOCKER for β (real users on Play Store + App Store).

## Goal

Server-side, fail-loud paywall enforcement that:

1. **Blocks chat-send when the user is over their billing-determined quota.**
2. **Adds < 50ms p50 to chat-send.** (chat-send is already Gemini-bound at ~2.8s p50; we cannot make it noticeably slower.)
3. **Doesn't introduce a new SPoF.** `billing.yral.com` is an external dependency; if it goes down, chat must keep working for paid users.
4. **Stays under 150 LOC** per the PROGRESS.md estimate.

## Three architectural options

### Option A — Token introspection (synchronous)

Server calls `billing.yral.com/v1/check-quota?user_id=X` on every chat-send.

| Pro | Con |
|---|---|
| Always fresh — no staleness | One network round-trip per chat-send (~50-200ms typical, billing.yral.com p95 unknown) |
| Stateless on agent side | New SPoF: billing.yral.com down → chat blocked or fail-open question |
| Simplest mental model | Hammers billing.yral.com; concurrent chat sessions = N concurrent quota calls |

**Verdict:** rejected. Latency budget is the blocker — chat-send is the user-facing critical path and we can't bolt 50-200ms onto every send. Also creates the new SPoF.

### Option B — JWT-with-quota (stateless)

billing.yral.com embeds remaining-quota into the JWT at issue time (e.g., `quota.daily_remaining: 24`). Server reads from the JWT; no network call on chat-send.

| Pro | Con |
|---|---|
| Zero added latency | Quota is stale until next JWT refresh (typically 1h+) — user could chat through their refresh-window quota and a fresh budget on each refresh |
| No new SPoF | Decrement-on-send needs server-side state anyway (the JWT is read-only) — so we're back to a counter |
| | Coordinated change with auth service required |

**Verdict:** rejected unless combined with C. JWT can carry the *limit* but the *used-count* must live somewhere mutable. JWT-with-limit + Redis-counter-with-used is workable but adds a coordinated rollout with the auth team — too much surface area for one PR.

### Option C — Periodic reconciliation (Redis-cached) ✅ RECOMMENDED

Agent maintains a per-user quota cache in Redis. Read on every chat-send (sub-ms). Refresh from billing.yral.com on cache-miss OR every N seconds via a lazy TTL. Atomic INCR on send.

```
chat-send → INCR user:quota:{uid}:used → if exceeds limit, 402 → else continue
                                       ↑
                                       limit comes from cache → refreshed lazily on miss
                                                              + every N min in background
```

| Pro | Con |
|---|---|
| Sub-ms steady-state read (Redis-local) | Bounded staleness — user can over-spend by up to (refresh_interval × rate) tokens |
| Survives billing.yral.com outage — uses cached limit | Cache miss latency on first request per user per period |
| Reuses existing Redis substrate (rate limiter, session memory, cost alerts) | Need to handle cache cold-start carefully (deny vs allow) |
| Atomic INCR is exactly the pattern Redis is good at | |

**Recommendation: Option C.**

## Redis schema (Option C)

```
KEY                          TYPE   FIELDS                          TTL
user:quota:{user_id}:limit   STR    integer limit (e.g., 50)        14 days
user:quota:{user_id}:used    STR    counter (INCR each send)        2 × period (~50h for daily)
user:quota:{user_id}:period  STR    ISO period start (e.g., 2026-06-08) 14 days
user:quota:{user_id}:meta    HASH   refreshed_at, plan, source_at   14 days
```

Notes:
- `limit` and `used` are separate keys (not a hash) so atomic INCR on `used` doesn't conflict with limit refreshes from billing.yral.com.
- TTL is generous (14 days) so a long-idle user's cache doesn't disappear mid-period and force a billing.yral.com call mid-chat — the period rollover logic resets `used` to 0 when `period` mismatches today's date.
- `meta.refreshed_at` lets us detect "this cache entry is older than the refresh interval — go pull fresh from billing."

## Integration point

`app/routes/chat.py:521` — right after `_can_access_conversation` returns 200. Before any LLM call, before any DB write of the user message. Reject with `402 Payment Required` if quota exceeded, with a JSON body the mobile client recognizes (matches the existing `billing.yral.com` rejection shape for client-side parity).

```python
# app/routes/chat.py:send_message — pseudocode of the new pre-check
allowed, info = await billing_quota.check_and_increment(user_id)
if not allowed:
    raise HTTPException(
        status_code=402,
        detail={
            "error": "quota_exceeded",
            "limit": info["limit"],
            "used": info["used"],
            "period_resets_at": info["resets_at"],
        },
    )
```

Same pattern is applied to the SSE path (`chat.py:835`).

## Fallback semantics

Three failure modes, each documented + tested:

| Failure | Behavior | Why |
|---|---|---|
| **Redis down** | Fall back to direct `billing.yral.com` check (slow, ~Option A semantics for the outage window). If billing is ALSO down → **fail-open with Sentry alert** | Refusing service to paid users during our own infra outage is worse than briefly accepting over-quota. The Sentry alert means we know about it. |
| **billing.yral.com down (cache hit)** | Continue using cached limit. Mark `meta.refreshed_at` stale; emit Sentry breadcrumb. | Cache is the whole point — survives upstream outage. |
| **billing.yral.com down (cache miss)** | **Fail-open with Sentry alert** for new users / new periods until billing recovers. | Same reasoning as Redis-down + billing-down: prefer over-grant during double outage to total outage. |

Fail-open is the right call here because:
1. The cost of denying paid users (churn, support tickets, reputation) > the cost of brief over-grants during an outage.
2. The Sentry alert + email digest section make over-grants visible within hours, not days.
3. The Phase 19.2 per-user cost breaker (separate PROGRESS.md item) is the *cost* backstop — it stops a single bad actor regardless of the billing service state.

## Module shape

New module: `app/services/billing_quota.py` (~120 LOC). Mirrors the shape of `app/rate_limiter.py`:

```python
async def check_and_increment(user_id: str) -> tuple[bool, dict]:
    """Returns (allowed, info_dict). Atomic INCR + limit-comparison.
    info_dict carries limit/used/resets_at for the 402 response body."""

async def _get_limit_from_cache_or_refresh(user_id: str) -> int:
    """Cache-first; on miss or stale, fetches from billing.yral.com.
    Caches the result. Sentry alert on billing-down + cache-miss."""

async def _refresh_loop():
    """Background loop: every N min, pre-warm the top-K users' cache
    by hitting billing.yral.com in batch. Reduces cold-miss latency
    for active users."""
```

Background loop wiring follows the cost_alerts pattern (`app/main.py` create + cancel + await; kill_switch gate `billing_quota` → `ENABLE_BILLING_QUOTA`).

## Open questions for Rishi (please answer before implementation starts)

1. **billing.yral.com API contract.** What's the endpoint shape? `GET /v1/quota?user_id=X` returns `{limit, used, period_resets_at}`? If different, point me at the docs and the right URL.
2. **Period model.** Is it daily, monthly, or per-conversation? The PROGRESS.md "25-50 msg limit" in 21α.C4 suggests daily; confirm.
3. **Refresh interval default.** I propose 5 min for the lazy TTL and 15 min for the proactive top-K pre-warm. Comfortable? Or want it tighter?
4. **402 response shape.** The proposed body above matches what mobile already shows from `billing.yral.com`. Confirm the field names match so the mobile client doesn't need a new branch.
5. **Cold-start policy.** If a brand-new user hits chat-send before billing.yral.com is reachable: deny (fail-closed) or allow up to a free-trial threshold (fail-open with a hardcoded "1 message free")? I lean fail-closed because new users aren't a critical-path retention concern.

## Implementation plan (after Rishi sign-off)

| PR | Scope | Est |
|---|---|---|
| H2-1 | `app/services/billing_quota.py` module + tests (no wiring yet) | 0.5 day |
| H2-2 | Wire pre-check into chat-send + SSE path; kill_switch entry; main.py loop; 402 response shape | 0.5 day |
| H2-3 | Sentry alerts + email digest section "Quota enforcement (yesterday)" + dashboard tile | 0.5 day |

Total: ~1.5 days. The PROGRESS.md 2-day estimate has slack for billing.yral.com API surprises.

## What this does NOT do

- **Per-user cost breaker** (Phase 19.2) — that's a separate guard, tracking $ not msg count. Different concern, lives in its own PR.
- **Plan tiers** — assumes one limit per user. If billing.yral.com returns tier info we'll persist it in `meta`, but the gate decision is just `used < limit`.
- **Refunds / chargebacks** — billing service's responsibility, not the agent's.

## Related

- PROGRESS.md 21α.C4 (DEV-3 audit finding) — the source of this requirement
- PROGRESS.md 21αβ.H2 (this work)
- `app/rate_limiter.py` — the existing Redis-counter pattern this mirrors
- `app/services/cost_alerts.py` — the sibling guard for $ cost (PR #306)
- `feedback_adhd_observability_and_security_baseline` (memory) — every protective system ships with dashboard tile + digest line + hot-edit knob; this design honors that
