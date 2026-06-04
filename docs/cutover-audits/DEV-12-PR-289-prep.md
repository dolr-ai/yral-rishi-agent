# DEV-12-prep — pre-drafted answers to PR #289's 4 review questions

Goal: walk into the PR review with a starting position grounded in three priors:
- `feedback_adhd_observability_and_security_baseline.md` — every protective system ships with dashboard + daily email + hot-edit knob
- `project_incident_2026_05_30_gemini_rate_limit.md` — the $400/24h that motivated this breaker
- Phase 25.5 `llm_costs` table + Phase 19.1 rate limiter — symmetric patterns to reuse

Each section: **Recommended:** + 1-paragraph reasoning. Rishi can override; this is a starting position, not a decision.

---

## Q1 — Default ceiling at $1.00/user/day — too tight, too loose?

**Recommended:** Keep $1.00/user/day as the default. Revisit after 1 week of 19.6 dashboard data showing the actual distribution.

**Reasoning:** The $400 incident was driven by background loops, not user-attributable calls — this breaker catches a different failure mode (abusive/compromised user account hitting agent.rishi.yral.com). For that:
- Typical alpha user sends 20-50 messages/day × ~$0.0001-0.001/call = $0.002-$0.05/day. $1 gives 20-500× headroom — comfortable for legit heavy users.
- Worst-case bounded exposure at ceiling × concurrent abuser-accounts. At $1/user × 100 attacker accounts = $100/day cluster-wide. Far below the $400 incident floor.
- Setting it tighter (e.g. $0.10) risks paging Rishi for legit heavy users; setting it looser (e.g. $5) reduces signal value.

The right default is "high enough to never block legit usage" + "low enough to bound abuse to acceptable damage." $1 hits that. The dashboard tile lets him tune per-user once the data lands.

---

## Q2 — Fail-open on Redis-unreachable — correct tradeoff?

**Recommended:** Keep fail-open. Surface Redis-unreachable as a red banner on the 19.6 dashboard so Rishi knows the breaker is offline.

**Reasoning:** Two failure modes weighted against each other:
- **Fail-open** during a Redis outage: abusive users get free chat until Redis is back. Redis Sentinel has historical >99.9% uptime; per-minute spend exposure is `concurrency × per-call-cost` = tiny. A 5-minute outage = max ~$0.10 of overrun even at $1/user × 10 attackers.
- **Fail-closed** during a Redis outage: ALL chat is blocked. v2 becomes useless for the duration. Far worse user-impact than the small overrun.

Per the [$400 incident playbook](project_incident_2026_05_30_gemini_rate_limit.md), "discipline ≠ visibility — visibility must be physical." Fail-open with **a visible "breaker offline" banner on the dashboard** is the discipline + visibility combo. The banner itself prompts Rishi to investigate when Redis is misbehaving, without the breaker being a self-DoS vector.

---

## Q3 — Sentry alert at warning vs error level?

**Recommended:** Keep `warning`. Don't escalate to `error` (which triggers Sentry's alerting / on-call paging).

**Reasoning:** A ceiling-hit is a **normal operational event**, not a runtime fault. It means either (a) a legit very-heavy user hit the cap — no action needed beyond a possible per-user tuning later, OR (b) abuse — Rishi can't act in the moment anyway; the value is the data, not the wake-up.

The `feedback_adhd_observability_and_security_baseline.md` rule covers visibility via: (a) the Phase 19.6 dashboard tile showing "users near/over ceiling today," (b) the Phase 24.5 daily email digest line. Those are the ADHD-friendly observability channels. Sentry-warning shows up in the standard Sentry feed for trend-tracking but doesn't wake anyone.

Counterargument: if ceiling-hit is treated as a security event (compromised account), `error` makes sense. **Mitigation without the wake-up:** add a separate Sentry-error trigger on the anomaly "this user's ceiling was raised >2× in last 24h" (covered in Q4 below). That catches the actually-alarming security case while keeping normal ceiling hits at warning.

---

## Q4 — HARD second ceiling that admin-override can't unblock?

**Recommended:** **NO** hard second ceiling. Instead, audit-log every ceiling-edit (who, when, what old/new), surface on the dashboard, and add an anomaly detector at "ceiling raised >2× within 24h" that fires Sentry-error (paging-eligible).

**Reasoning:** A hard ceiling protects against the runaway-attack case where a compromised admin JWT bumps the ceiling for their own user account. But the cost of a hard ceiling is real:
- A legit very-heavy user (think Rishi himself running 200 manual tests) would lock out and need a special unbypass mechanism — operational toil
- The unbypass mechanism itself creates a new attack surface (in-person Rishi, second admin JWT, etc.)
- Worst-case math: cluster-wide soft ceiling × N attacker accounts = bounded. At $1 × 100 accounts = $100/day. Even unmitigated for 24h that's 4× LESS than the incident floor.

The actual threat model is "compromised admin JWT raises ceilings unnoticed." Counter-measure that's symmetric with Phase 19.1's rate-limiter audit log + dashboard:
- Every PATCH to a per-user ceiling writes an audit row (who, when, old value, new value)
- 19.6 dashboard tile: "ceiling changes in last 24h" with the rows clickable
- Anomaly: `count(audit_rows WHERE user_id=X AND raised_by_factor > 2) > 1` in 24h → Sentry-error → operator sees + can revert

This gets the security signal without the operational risk of a hard ceiling. Symmetric with how `rate_limit_config` updates already audit-log + dashboard-surface (Phase 19.1).

---

## Architectural note — reuse Phase 25.5 `llm_costs`, don't roll a separate counter

PR #289 currently uses a Redis-only counter. **Recommend a follow-up refactor** to read the current-day spend from `llm_costs` (via a SUM query) at startup + maintain Redis as a hot cache. This makes Redis a perf shortcut, not a source of truth — aligning with the Phase 25.5 architecture where `llm_costs` is the cost-truth table.

Cheap to add later: the breaker can lazily SUM from `llm_costs` when Redis is unreachable (instead of returning 0.0 = fail-open). That converts the fail-open from "no enforcement" to "slow-but-correct enforcement during Redis outage." Best of both worlds.

Not blocking the current PR; flag as follow-up.

---

## Summary

| Question | Recommended | Why (1 line) |
|---|---|---|
| Q1: $1/user/day default? | **Keep $1, tune from dashboard data** | 20-500× headroom over normal usage; bounded abuse exposure |
| Q2: Fail-open on Redis-down? | **Keep fail-open + dashboard banner** | Self-DoS is worse than minor overrun during a brief Redis blip |
| Q3: Sentry warning vs error? | **Keep warning** | Normal-event; ADHD-observability covered by 19.6 + 24.5; escalation goes in Q4 |
| Q4: Hard second ceiling? | **No — use ceiling-edit audit + anomaly detector** | Avoids legit-user lockout; same security signal via 19.1-style audit log |
| Follow-up | **Read llm_costs as source-of-truth on Redis miss** | Aligns with Phase 25.5 architecture; promotes fail-open to "fail-correct-but-slow" |

Walking-in position: all 4 questions answered, follow-up scope identified.
