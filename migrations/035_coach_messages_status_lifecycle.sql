-- Coach PR-3: typed proposal lifecycle on coach_messages.
-- Fixes the wrong-proposal-applied trust bug (Codex review §3): today
-- POST /apply commits coach_repo.latest_proposal() (ORDER BY created_at
-- DESC LIMIT 1) with no client-side proposal_id. Scrolling up + tapping
-- Save on an older card applies the NEWER proposal silently.
--
-- After this migration:
--   /apply takes proposal_id in the body, looks up by id, checks
--   status='pending', transactionally supersedes other pending in the
--   same session, then applies. /discard is the explicit counterpart.
--   GET /messages surfaces the per-row status so mobile can render
--   active/passive/applied/discarded card states.
--
-- Schema:
--   status              VARCHAR(20) NOT NULL DEFAULT 'pending'
--                         CHECK IN ('pending','applied','discarded','superseded','na')
--   status_changed_at   TIMESTAMPTZ NULL — set on transition;
--                         NULL means "still in default state"

ALTER TABLE coach_messages
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'applied', 'discarded', 'superseded', 'na')),
    ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ;

COMMENT ON COLUMN coach_messages.status IS
    'Coach PR-3 lifecycle. pending/applied/discarded/superseded for proposal '
    'rows (role=coach AND proposed_* IS NOT NULL); na for creator messages, '
    'opening greetings, and receipts. /apply enforces status=pending; on '
    'success transitions the chosen row to applied and any other pending '
    'in the same session to superseded.';
COMMENT ON COLUMN coach_messages.status_changed_at IS
    'Timestamp of the last transition out of pending. NULL while the row is '
    'still in its default state. Saves a join into system_instructions_history.';

-- ─── Backfill (idempotent — re-runs are no-ops thanks to the WHERE clauses) ─

-- Pass 1: Mark already-applied proposals via the system_instructions_history
-- audit trail. These are the proposals creators tapped Save on before PR-3.
-- system_instructions_history.coach_message_id is a UUID; coach_messages.id
-- is the same; cast both sides to text for the equality so the join
-- doesn't depend on either column's declared type.
UPDATE coach_messages cm
SET status = 'applied',
    status_changed_at = sih.applied_at
FROM system_instructions_history sih
WHERE sih.coach_message_id::text = cm.id::text
  AND cm.role = 'coach'
  AND cm.status = 'pending';

-- Pass 2: Of the rows still in default 'pending', any non-latest pending
-- in each session is actually 'superseded' — older proposals the creator
-- never applied because they kept asking Coach for more options. Latest
-- pending stays 'pending' (the current actionable proposal).
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY coach_conversation_id
               ORDER BY created_at DESC
           ) AS r
    FROM coach_messages
    WHERE status = 'pending'
      AND role = 'coach'
      AND (proposed_changes IS NOT NULL
           OR proposed_global_rule_override IS NOT NULL)
)
UPDATE coach_messages cm
SET status = 'superseded',
    status_changed_at = NOW()
FROM ranked
WHERE cm.id = ranked.id AND ranked.r > 1;

-- Pass 3: Mark non-proposal rows (creator messages, opening greetings,
-- receipts) as 'na' so the DEFAULT 'pending' doesn't leave them in a
-- semantically wrong state. These rows never enter the apply/discard flow.
UPDATE coach_messages
SET status = 'na'
WHERE status = 'pending'
  AND (role = 'creator'
       OR (role = 'coach'
           AND proposed_changes IS NULL
           AND proposed_global_rule_override IS NULL));

-- ─── Index for the per-session "is there a pending proposal?" query ──
-- pending_proposal() runs on every send-message and list-messages call
-- (PR-4); a partial index on the pending rows only keeps lookups O(log
-- pending) rather than O(log session_size).
CREATE INDEX IF NOT EXISTS idx_coach_messages_pending_per_session
    ON coach_messages (coach_conversation_id, created_at DESC)
    WHERE status = 'pending';
