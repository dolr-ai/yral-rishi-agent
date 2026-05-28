-- Bug 1 fix: timer should reset on CREATOR activity (keeps creator responsive),
-- not user activity. Add a separate timestamp for the creator's last message.

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS human_creator_last_message_at TIMESTAMP;
