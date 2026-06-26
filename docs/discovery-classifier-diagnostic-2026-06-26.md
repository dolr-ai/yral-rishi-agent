# Diagnostic Brief — Discovery classifier: is the loop on, and is it retrying stuck bots?

**For:** developer session · **From:** planning session (read-only) · **Date:** 2026-06-26
**Trigger:** Rishi saw ~40 classifier LLM calls/day on the dashboard. The loop
**ships OFF** (`kill_switch._DEFAULT_OFF_LOOPS`), so either (a) it was enabled in prod,
or (b) someone ran the on-demand sample endpoint. We need to confirm which, and fix a
real quirk if the loop is on.
**Process:** normal flow — small single-concern PR, CI + Codex, **Rishi approves before
merge/deploy.** Part A is read-only investigation; Part B only if Part A confirms it.

---

## Part A — Diagnose (read-only, no code change)

Run these three checks and report findings back to Rishi:

1. **Is the loop actually enabled?** Open `/admin/dashboard` (it renders
   `kill_switch.current_state()`). Read the `influencer_classification` row:
   - `effective: true` → the loop is running in prod (env `ENABLE_INFLUENCER_CLASSIFICATION_LOOP=true` was set).
   - `effective: false` → the loop is OFF; the ~40 calls were the **sample endpoint**
     (`POST /admin/discovery/classify-sample`, which ignores the switch and doesn't
     write to the DB). If so, **nothing to fix** — it was manual review activity.

2. **What do the calls look like?** In Langfuse, filter `process = influencer_classification`:
   - Tight bursts of ≤10 calls spaced ~1 hour apart → the **loop**.
   - Irregular clusters at human-click times → **sample** runs.
   - Note the per-day count and whether the *same bot_ids* recur each day (the smoking
     gun for the stuck-unknown quirk below).

3. **How much of the catalog is unclassified?** Read-only SQL (replica is fine):
   ```sql
   SELECT
     COUNT(*) FILTER (WHERE gender='unknown' AND archetype='unknown') AS both_unknown,
     COUNT(*) FILTER (WHERE archetype <> 'unknown')                    AS has_archetype,
     COUNT(*)                                                          AS total_active
   FROM ai_influencers WHERE is_active='active';
   ```
   Then list the stuck ones (these are what the loop retries every hour):
   ```sql
   SELECT id, display_name, (avatar_url IS NULL OR avatar_url='') AS no_avatar,
          length(COALESCE(system_instructions,'')) AS prompt_len
   FROM ai_influencers
   WHERE is_active='active' AND gender='unknown' AND archetype='unknown'
   ORDER BY created_at DESC;
   ```
   If `both_unknown` is a small, **stable** number (~handful) and those same bots recur
   in Langfuse daily → that's the quirk in Part B, and it explains the ~40/day.

**Decision:** loop OFF → close it out (samples, no action). Loop ON + a stable
stuck-unknown set → do Part B. Loop ON + `both_unknown` legitimately shrinking →
healthy backfill, no action (calls will trail off on their own).

---

## Part B — Fix the "retry stuck-unknown bots forever" quirk (only if confirmed)

**Root cause.** `influencer_classification._validate_classification()` returns `None`
when BOTH labels come back `unknown` (so it won't overwrite a possible future better
label). The loop's candidate query only picks bots where `gender='unknown' AND
archetype='unknown'`. So a bot the model *can't* classify (no avatar + vague prompt)
**is never written, stays unknown, and is re-attempted on every hourly pass — forever.**
A handful of these = a permanent ~40 calls/day drip.

**Fix (recommended — no migration):** stamp an *attempt* timestamp in the existing
`ai_influencers.metadata` JSONB, and skip recently-attempted bots in the loop's
candidate query. This separates "we tried" from "we got a label" without a schema change.

- After a classification attempt that yields no usable label, write
  `metadata = jsonb_set(metadata, '{classification_attempted_at}', to_jsonb(now()))`
  for that bot (a successful label keeps writing gender/archetype as today and needs no
  stamp — it's excluded by no longer being `unknown`).
- Change the loop's candidate selection to exclude recently-attempted bots:
  ```sql
  WHERE is_active='active'
    AND gender='unknown' AND archetype='unknown'
    AND (
      metadata->>'classification_attempted_at' IS NULL
      OR (metadata->>'classification_attempted_at')::timestamptz < NOW() - INTERVAL '30 days'
    )
  ```
  → an unclassifiable bot is retried at most **once a month** (in case its avatar/prompt
  improved), not every hour. Calls/day drop to ≈ new-bots-created/day.

**Why not the alternatives:**
- *Write `unknown` explicitly to exclude it* → breaks the "don't overwrite a future
  better label" intent; the metadata stamp keeps that intent.
- *Redis TTL set of attempted ids* → works but not durable (a Redis flush re-triggers
  the full retry storm); metadata is durable and needs no new infra.

**Keep it small + safe:**
- One PR, behavior-preserving on the happy path (successfully-classified bots are
  unaffected — they're already excluded by not being `unknown`).
- Add one log line per pass: `"classifier: N candidates (skipped M recently-attempted)"`
  so the drip is visible going forward.
- No new env var; reuse the existing `influencer_classification` gate.

**Nice-to-have (same or follow-up PR), matches the observability baseline:** add a
classifier tile to `/admin/dashboard` — `{total_active, classified, both_unknown,
attempted_last_30d}` — so Rishi can see catalog coverage at a glance instead of reading
Langfuse. Optional; flag if it pushes the PR over ~100 lines.

---

## Acceptance
- Part A findings reported to Rishi (loop on/off + call source + `both_unknown` count).
- If Part B ships: classifier calls/day fall to roughly the new-bot creation rate;
  the same stuck bot_ids stop recurring hourly in Langfuse; happy-path classification
  unchanged.
