# DEV-12 — Phase 19.2 cost circuit breaker DRAFT PR (21α.B6)

## TL;DR

**🟢 GREEN — DRAFT PR #289 opened.** ~170 LOC service layer + 2 hooks in `llm_registry` + 10 source-pin tests, all pass. Branch: `feat/phase-19-2-per-user-cost-breaker-DRAFT`. Tagged DRAFT per the spec; will not auto-merge.

PR URL: https://github.com/dolr-ai/yral-rishi-agent/pull/289

## What's in the PR

- **`app/services/llm_cost_breaker.py`** (NEW): per-user-day Redis counter, fail-open semantics, Sentry alert with NX-dedup
- **`app/services/llm_registry.py`** (2 hooks): pre-call `check_or_reject` before LLM dispatch, post-call `increment` in `_record_cost`
- **`tests/test_phase_19_2_cost_breaker.py`** (10 tests, all pass, ruff clean)

## Defaults baked in

- Ceiling: **$1.00/user/UTC-day**, overridable via `LLM_PER_USER_DAILY_CEILING_USD` env var
- TTL on Redis keys: **48h** so day-rollover auto-clears
- Sentry alert: **once per user per day** (NX-SET dedup)
- Fails OPEN on Redis-unreachable

## NOT in this PR (follow-up after Rishi reviews)

1. Admin PATCH `/admin/cost-ceiling/{user_id}` route wiring (service layer ready)
2. Per-user override table `llm_user_cost_ceilings`
3. 19.6 dashboard tile
4. Wire `CostCeilingExceeded` → structured 402 response in `chat.py`
5. Integration test with a real Redis (CI is source-pin only)

## Recommendation

**Cutover gate B6: DRAFT submitted — review in morning meeting.** This is structural work that should merge, but not without Rishi reading the 4 review questions in the PR body:

1. Is $1/user/day the right starting default?
2. Fail-open on Redis-down — correct tradeoff?
3. Sentry alert level (warning vs error)?
4. HARD second ceiling that admin-override can't unblock?

After Rishi answers + reviews, the merge is straightforward. The follow-up PRs are scoped + small.
