# PR-3 design — bind /apply to an explicit proposal_id + status

**Status:** Draft for Rishi review. **DO NOT MERGE.** Ships tomorrow after pg_dump (Rule 9) + column-shape confirm (Rule 8).

## Problem (the trust bug)

`POST /apply` today calls `coach_repo.latest_proposal(coach_conversation_id)` which executes:

```sql
SELECT … FROM coach_messages
WHERE coach_conversation_id = $1::uuid
  AND role = 'coach'
  AND (proposed_changes IS NOT NULL
       OR proposed_global_rule_override IS NOT NULL)
ORDER BY created_at DESC
LIMIT 1
```

→ commits whatever the **most recent** proposal is, with no client-side proposal_id.

**Failure mode**: creator scrolls up to an older proposal card, taps Save → backend applies the **newer** proposal silently. This is the Codex review's residual trust bug. PR-4 (`pending_proposal_exists`) hides the Save button when no pending exists, but it does NOT fix the wrong-proposal-applied case.

## Fix shape

Make Save explicit: the request carries `proposal_id`; the server looks up that exact row + checks `status='pending'`. Each proposal moves through a typed lifecycle: `pending → applied | discarded | superseded`.

## Schema change (needs pg_dump + Rule 8 confirm)

Add **two columns** to `coach_messages`:

```sql
ALTER TABLE coach_messages
    ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'applied', 'discarded', 'superseded', 'na')),
    ADD COLUMN status_changed_at TIMESTAMPTZ;
```

Rationale per column:

| Column | Why |
|---|---|
| `status` | The 4-value lifecycle the route checks at `/apply`. `na` covers the rows where status is irrelevant (creator messages, opening greetings, receipts) — they default-insert that value. |
| `status_changed_at` | Audit trail. When we mark a proposal `superseded`, we want to know when it happened. Saves a join into `system_instructions_history`. NULL for `pending`. |

**Backfill** (one-shot at deploy):

```sql
-- All rows that look like proposals (have non-null proposed_* fields)
-- start as 'pending' so they enter the new lifecycle cleanly.
UPDATE coach_messages
SET status = 'pending'
WHERE role = 'coach'
  AND (proposed_changes IS NOT NULL
       OR proposed_global_rule_override IS NOT NULL);

-- Mark already-applied proposals via the audit table:
UPDATE coach_messages cm
SET status = 'applied',
    status_changed_at = sih.applied_at
FROM system_instructions_history sih
WHERE sih.coach_message_id::text = cm.id::text
  AND cm.role = 'coach';

-- The remaining `pending` rows are either truly-pending (the active
-- proposal at the bottom of each session) OR stale proposals from
-- pre-PR-3 sessions where the creator never tapped Save. Old `pending`
-- in resumed sessions becomes `superseded` if a newer pending exists
-- in the same session — second pass:
WITH ranked AS (
    SELECT id, coach_conversation_id,
           ROW_NUMBER() OVER (
               PARTITION BY coach_conversation_id
               ORDER BY created_at DESC
           ) AS r
    FROM coach_messages
    WHERE status = 'pending'
)
UPDATE coach_messages cm
SET status = 'superseded',
    status_changed_at = NOW()
FROM ranked
WHERE cm.id = ranked.id AND ranked.r > 1;

-- Non-proposal rows get 'na' explicitly so the DEFAULT 'pending'
-- doesn't leave them mislabeled:
UPDATE coach_messages
SET status = 'na'
WHERE role = 'creator'
   OR (role = 'coach'
       AND proposed_changes IS NULL
       AND proposed_global_rule_override IS NULL);
```

## API change

### Existing endpoint, breaking change to behavior (but additive for clients)

```
POST /api/v1/creator/coach/conversations/{coach_conversation_id}/apply
Body: { "proposal_id": "<coach_messages.id>" }
```

Server enforces:
- Lookup row by `id` (not `ORDER BY created_at DESC LIMIT 1`).
- Row must be in the same `coach_conversation_id` as the URL parameter.
- Row must have `role = 'coach'` AND a non-null proposal column.
- Row must have `status = 'pending'` — anything else returns 409 with the current status in the body.
- On success: `UPDATE coach_messages SET status='applied', status_changed_at=NOW() WHERE id=$1`.

**Mobile contract**: existing clients that don't send `proposal_id` get a 422 with a clear message. Mobile expert needs to land its change (use the `proposed_changes` message's `id` from the existing list-messages response) before this PR can deploy.

### Companion endpoint

```
POST /api/v1/creator/coach/conversations/{coach_conversation_id}/discard
Body: { "proposal_id": "<coach_messages.id>" }
```

- Same lookup + status checks. On success: `UPDATE … SET status='discarded'`.
- Mobile uses this when creator taps "Throw away" / "Discard" on an explicit proposal card.

### Existing endpoint, additive: list-messages returns the per-message status

`GET /conversations/{id}/messages` already includes proposal fields; PR-3 adds `status` so mobile can render each card with the right state (active card → Save button enabled; superseded card → grayed out with "Replaced by newer proposal"; applied card → "✅ Applied" badge).

## Supersede semantics

When a new proposal lands and there's already a pending proposal in the same session, we DON'T auto-supersede on insert — the creator might want to scroll back to compare. Mobile-side rule:

- Save button visible on the latest pending proposal.
- Older pending proposals render with a passive "Replaced by newer proposal" badge.

Backend rule (the trust bug fix):

- `/apply` accepts ANY pending proposal_id, not just the latest.
- BUT: if `proposal_id` is older than the latest pending, server auto-supersedes the newer one(s) before applying so the lifecycle is consistent:

```sql
BEGIN;
-- Lock the conversation's pending rows
SELECT id FROM coach_messages
WHERE coach_conversation_id=$1 AND status='pending'
FOR UPDATE;
-- Mark every pending row newer than $proposal_id as superseded
UPDATE coach_messages
SET status='superseded', status_changed_at=NOW()
WHERE coach_conversation_id=$1
  AND status='pending'
  AND created_at > (SELECT created_at FROM coach_messages WHERE id=$proposal_id);
-- Mark older pending rows superseded too (creator went back further still)
UPDATE coach_messages
SET status='superseded', status_changed_at=NOW()
WHERE coach_conversation_id=$1
  AND status='pending'
  AND id != $proposal_id;
-- Apply the chosen one
UPDATE coach_messages SET status='applied', status_changed_at=NOW() WHERE id=$proposal_id;
COMMIT;
```

Net effect: after any `/apply`, the session has at most ONE applied proposal and zero pending. The lifecycle is provable from `coach_messages` alone — no derived state.

## What this PR does NOT change

- The applied path still writes to `ai_influencers.system_instructions` (or `global_rule_overrides`) per Coach Fix 1 PR-B.
- `system_instructions_history` still records the audit trail. The status column is duplicate info for the proposal side; the history table is the canonical "what bot state changed when" log.
- `latest_proposal()` repo helper stays for back-compat (sibling endpoints + `pending_proposal()` consume it).
- `pending_proposal()` helper updates its filter to `status='pending'` — same semantics, typed source of truth.

## Open questions for Rishi (answer BEFORE I write code tomorrow)

1. **`status_changed_at` NULL for `pending`?** Or default `NOW()` at insert? Recommendation: NULL — the row's `created_at` is the proposal's birth time; `status_changed_at` is reserved for transitions.

2. **`status='na'` vs `NULL` for non-proposal rows.** I chose explicit `'na'` for readability + CHECK constraint enforceability. Alternative: drop NOT NULL, use NULL for non-proposals. Recommendation: stick with `'na'`.

3. **Discard endpoint scope.** Today there's no `/discard` — Codex review §2 noted this as a Fix-4 follow-up. Bundle into PR-3 or ship later? Recommendation: bundle — the lifecycle is only consistent if discard is the explicit counterpart to apply.

4. **Migration number.** Next free is 037 (the WAL-G drill PR took 036). Confirm before tomorrow.

5. **Rollout flag.** Per `feedback_all_agent_features_need_flags_until_cutover`, every new agent feature ships behind a flag with `defaultValue=false` until cutover. Does this rule apply to a backend-only schema change? Recommendation: NO flag — the schema's there once it's applied; mobile can choose when to consume the new contract.

## Rule 9 + Rule 8 checklist (BEFORE merge)

- [ ] **pg_dump** of `coach_messages` + `system_instructions_history` taken before migration 037 applies.
- [ ] **Column shape confirmed** with Rishi: `status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK IN (5 values)` + `status_changed_at TIMESTAMPTZ NULL`.
- [ ] Backfill SQL runs in a transaction; verify counts pre+post.
- [ ] Mobile expert confirms client change (use `proposal_id` in /apply body) is ready in parallel.

## What ships in PR-3 tomorrow

1. Migration 037 with the two columns + backfill.
2. `coach_repo.pending_proposal()` updated to filter on `status='pending'` (semantics identical; typed source).
3. New `coach_repo.supersede_older_pending()` helper for the apply transaction.
4. `app/routes/creator_coach.py:apply_coach_proposal` rewrites to take `proposal_id` from body + the supersede-then-apply transaction.
5. New `app/routes/creator_coach.py:discard_coach_proposal` endpoint.
6. `_format_message` surfaces `status` on every message row.
7. Tests: source-pin on the migration, behavioral on the supersede transaction, source-pin on the route shape change.

Estimated 8-10 tests total. PR is small but schema-touching → needs Rishi's go-ahead before code is written.

## Related

- Plan §3 #3 (single Save UX) — PR-4 lands today; this is its trust-fix counterpart.
- Codex review §3 (proposal_id binding) — Codex explicitly flagged this.
- `feedback_a21_fix_pr_shape` — single concern, <50 strict-code lines target. This PR is closer to 100-150 LOC of strict code (route + helper + migration); justified because the fix doesn't decompose.
