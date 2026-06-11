-- Phase 21αβ — Coach Bucket 2 PR-2
-- Adds the storage columns for the new `proposed_section_change` Coach
-- proposal shape (see migration 038 + the Bucket 2 contract at
-- docs/designs/coach-bucket-2-sections-contract.md).
--
-- Two columns on coach_messages, both nullable:
--
--   proposed_section_change  JSONB  — full proposal blob when Coach
--     committed to a section-scoped edit this turn. Shape:
--       {
--         "section_id":            "voice_and_tone",
--         "section_heading":       "Voice and tone",
--         "section_editable":      true,
--         "new_body":              "<full new body for the section>",
--         "previous_body_sha256":  "<sha of body as Coach read it>"
--       }
--     The /apply endpoint dispatches on this column: when non-NULL it
--     writes the section UPDATE into ai_influencers.system_instructions_sections
--     via jsonb_set. previous_body_sha256 is the optimistic-concurrency
--     handle — /apply returns 409 stale_proposal if the live body's sha
--     has drifted since Coach read it.
--
--   target_section_id        VARCHAR(64) — denormalised slug of the
--     section the proposal targets. Cheap path for the future "show me
--     all proposals against the voice_and_tone section" filter query.
--     Always equals proposed_section_change->>'section_id' when the
--     proposal column is non-NULL, NULL otherwise. We could compute this
--     on the fly from JSONB but a typed column lets us index it cheaply.
--
-- Why two columns instead of overloading proposed_changes (TEXT):
--   /apply already dispatches on `proposed_global_rule_override`
--   (migration 034) — the dispatch contract is "exactly one proposal
--   column populated per turn". Adding a third typed column keeps that
--   contract clean. proposed_changes' TEXT shape stays load-bearing for
--   the historical flat-text path; new section path lives in its own
--   JSONB lane.
--
-- Safety: ADD COLUMN NULLABLE (no DEFAULT) is metadata-only on pg11+,
-- safe on the coach_messages table at any size. Same proven shape as
-- migration 034 `proposed_global_rule_override`.
--
-- Rule 9: covered by the auto-pg_dump runner (#309) — 5 clean dumps
-- this week from 033/034/035/036/038.

-- squawk: cap lock-wait + statement duration per the I-Mig2 rule (#340).
-- Same 3s/60s as 033 + 034 + 035 + 036 + 038.
SET lock_timeout = '3s';
SET statement_timeout = '60s';

ALTER TABLE coach_messages
    ADD COLUMN IF NOT EXISTS proposed_section_change JSONB;

ALTER TABLE coach_messages
    ADD COLUMN IF NOT EXISTS target_section_id VARCHAR(64);

COMMENT ON COLUMN coach_messages.proposed_section_change IS
    'Coach Bucket 2 PR-2. When non-NULL, this coach turn proposed a '
    'section-scoped edit (see ai_influencers.system_instructions_sections, '
    'migration 038). Shape: {section_id, section_heading, section_editable, '
    'new_body, previous_body_sha256}. The /apply endpoint dispatches on '
    'this column; previous_body_sha256 drives optimistic concurrency.';

COMMENT ON COLUMN coach_messages.target_section_id IS
    'Coach Bucket 2 PR-2. Denormalised slug from '
    'proposed_section_change->>section_id. Cheap query path for '
    '"show me all proposals targeting section X". NULL on non-section '
    'turns.';
