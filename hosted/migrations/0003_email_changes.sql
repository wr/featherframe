-- Changing a household's email (W-773): the new address is confirmed by a
-- link sent to it before the account's email is replaced.
CREATE TABLE email_changes (
  token_hash TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users (id),
  email      TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at    INTEGER
);
