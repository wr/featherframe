-- The waitlist (W-850): someone who asked to be let in, from the marketing
-- page's form ('site') or by trying to sign in uninvited ('login'). The admin
-- page invites from here.
CREATE TABLE waitlist (
  email      TEXT PRIMARY KEY,
  source     TEXT,
  created_at INTEGER NOT NULL,
  invited_at INTEGER
);
