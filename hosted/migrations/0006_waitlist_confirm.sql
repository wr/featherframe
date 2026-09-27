-- Double opt-in for the waitlist: an address from the marketing page's form
-- is pending until its owner follows the link emailed to it. Only a hash of
-- that link's token is kept, as for sign-in links; sent_at spaces re-sends
-- (one per 10 minutes). An uninvited sign-in attempt ('login') joins pending
-- and is not emailed.
ALTER TABLE waitlist ADD COLUMN confirmed_at INTEGER;
ALTER TABLE waitlist ADD COLUMN token_hash TEXT;
ALTER TABLE waitlist ADD COLUMN token_expires_at INTEGER;
ALTER TABLE waitlist ADD COLUMN sent_at INTEGER;
CREATE UNIQUE INDEX waitlist_token ON waitlist (token_hash) WHERE token_hash IS NOT NULL;

-- Every address already here signed up before confirmation existed: each
-- counts as confirmed, as of the day it signed up.
UPDATE waitlist SET confirmed_at = created_at;

-- Sign-ups per IP and per address, for the rate limits (a hash of each, never
-- the IP or address itself). Rows older than a day are pruned as new ones land.
CREATE TABLE rate_hits (
  key TEXT NOT NULL,
  at  INTEGER NOT NULL
);
CREATE INDEX rate_hits_key ON rate_hits (key, at);
CREATE INDEX rate_hits_at ON rate_hits (at);
