-- Phase: video generation on the agent service.
--
-- One row per generation, and the entire state of the feature. Written before
-- the job is submitted to ComfyUI so the poll loop's recovery path is just
-- "scan pending" — there is no separate resume mechanism to get wrong.
--
-- video_id is deliberately one identifier doing four jobs: the operation id
-- returned to mobile, the storage object name, the SpacetimeDB post id, and our
-- primary lookup key. The service it replaces carried five distinct ids.
--
-- user_token holds the caller's yral-auth id_token, needed minutes later to
-- write the post to SpacetimeDB as that user. Cleared on any terminal state.

CREATE TABLE IF NOT EXISTS videogen_requests (
    id             BIGSERIAL PRIMARY KEY,
    user_id        TEXT NOT NULL,
    video_id       TEXT NOT NULL UNIQUE,
    prompt         TEXT NOT NULL,
    model_id       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'complete', 'failed')),
    comfy_id       TEXT,
    video_url      TEXT,
    user_token     TEXT,
    failure_reason TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Serves the Drafts-tab poll: this user's pending rows, newest first.
CREATE INDEX IF NOT EXISTS idx_videogen_requests_user_status
    ON videogen_requests (user_id, status, created_at DESC);

-- The poll loop scans pending across all users every tick.
CREATE INDEX IF NOT EXISTS idx_videogen_requests_pending
    ON videogen_requests (status, created_at)
    WHERE status = 'pending';

-- updated_at is maintained by trigger so no future write can forget it.
CREATE OR REPLACE FUNCTION videogen_requests_touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_videogen_requests_touch ON videogen_requests;
CREATE TRIGGER trg_videogen_requests_touch
    BEFORE UPDATE ON videogen_requests
    FOR EACH ROW EXECUTE FUNCTION videogen_requests_touch_updated_at();
