-- Phase 4.6: user profile memory (cross-influencer identity facts).
--
-- Identity-category memories (name, age, location, occupation, language) are
-- stored with influencer_id=NULL so they apply across every bot the user
-- chats with. The existing get_all_for_user query already unions
-- per-influencer + global memories, so retrieval is unchanged.
--
-- The gap is in the unique-key index: by default Postgres treats NULL as
-- distinct, so two (user_id=X, influencer_id=NULL, key='name') rows would
-- BOTH be allowed and the ON CONFLICT path on memory_repo.upsert would
-- skip the update. Postgres 15 added NULLS NOT DISTINCT — we rebuild the
-- index with that clause so global rows dedupe correctly.

DROP INDEX IF EXISTS idx_user_memories_unique_key;

CREATE UNIQUE INDEX idx_user_memories_unique_key
    ON user_memories (user_id, influencer_id, key)
    NULLS NOT DISTINCT;
