-- Every admin action (W-863): who did what to whom, and how it went. Kept.
CREATE TABLE admin_log (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  at     INTEGER NOT NULL,
  admin  TEXT NOT NULL,             -- the admin's email, or "api" for the bearer token
  action TEXT NOT NULL,             -- invite, resend, revoke, waitlist.remove, household.suspend, …
  target TEXT,                      -- the email or household it was done to
  ok     INTEGER NOT NULL,
  result TEXT NOT NULL              -- what the page said
);
CREATE INDEX admin_log_at ON admin_log (at);
