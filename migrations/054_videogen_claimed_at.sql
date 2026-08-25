-- Atomic claiming for the videogen poll loop.
--
-- The service runs 2 swarm replicas x 4 uvicorn workers, so EIGHT copies of the
-- poll loop run concurrently. They all read the same `pending` rows and all
-- process them: one generation on 2026-08-25 was fetched from the GPU box and
-- uploaded to Storj six times, and every loser of the race got
-- `DuplicatePostId` back from SpacetimeDB.
--
-- `claimed_at` makes a row claimable by exactly one worker via a conditional
-- UPDATE. It deliberately does NOT introduce a new `status` value: the Drafts
-- spinner filters on `status = 'pending'`, and a claimed row is still pending
-- from the app's point of view.
--
-- A claim expires so a worker that dies mid-generation cannot strand a row —
-- another loop re-claims it once the lease lapses.

SET lock_timeout = '3s';
SET statement_timeout = '60s';


ALTER TABLE videogen_requests
    ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

-- The claim query filters on (status, claimed_at); the existing pending index
-- covers status alone, so this keeps the scan cheap as the table grows.
CREATE INDEX IF NOT EXISTS idx_videogen_requests_claimable
    ON videogen_requests (status, claimed_at)
    WHERE status = 'pending';
