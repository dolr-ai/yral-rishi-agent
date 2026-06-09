-- Phase 21αβ — Coach Fix 1 PR-B
-- Lets Coach propose a per-bot global-rule OVERRIDE (writes to
-- ai_influencers.global_rule_overrides — see migration 033) instead of
-- a system_instructions edit. Used when the creator asks for behavior
-- that conflicts with a platform-wide rule like "1-3 sentences max".
--
-- Storage: a JSONB column on coach_messages, sibling to
-- proposed_changes. NULL for non-override turns.
-- Shape when set: {"key": "response_length", "value": "long_allowed"}
--
-- Why a new column instead of overloading proposed_changes:
--   The apply endpoint needs to know WHICH column on ai_influencers to
--   update — system_instructions vs global_rule_overrides. A typed
--   column makes the dispatch trivial and keeps proposed_changes'
--   TEXT contract intact for the historical reading.
--
-- Safety: ADD COLUMN NULLABLE (no DEFAULT) is metadata-only on pg11+,
-- safe on the coach_messages table.

ALTER TABLE coach_messages
    ADD COLUMN IF NOT EXISTS proposed_global_rule_override JSONB;

COMMENT ON COLUMN coach_messages.proposed_global_rule_override IS
    'Coach Fix 1 PR-B. When non-NULL, this coach turn proposed flipping '
    'a per-bot GLOBAL_RULES override (see ai_influencers.global_rule_overrides). '
    'Shape: {"key": "<override-slug>", "value": "<arbitrary>"}. The apply '
    'endpoint dispatches on this column; if NULL it falls back to writing '
    'proposed_changes to ai_influencers.system_instructions.';
