-- A sign-in one browser asked for (W-947). Its ff_signin cookie names the
-- row, so the six digits emailed beside the link work only in that browser,
-- five tries. link_hash and code_hash are NULL when nothing was sent (an
-- address that may not sign in, or asked too often): the page is the same.
CREATE TABLE signin_requests (
  id_hash    TEXT PRIMARY KEY,
  email      TEXT NOT NULL,
  kind       TEXT NOT NULL,
  link_hash  TEXT,
  code_hash  TEXT,
  attempts   INTEGER NOT NULL DEFAULT 0,
  tz         TEXT,
  pair_code  TEXT,
  frame      TEXT,
  back       TEXT,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);
CREATE INDEX signin_requests_link ON signin_requests (link_hash);
