-- Confirming a new owner's email (W-889). An account made on the setup page
-- is signed in on the phone at once, before its email is proven; until the
-- emailed link is followed the page asks for it, so a mistyped address is
-- found before the phone's session ends. Following any emailed link (a
-- sign-in, an invitation) proves it too. Everyone here already signed in
-- by link.
ALTER TABLE users ADD COLUMN verified_at INTEGER;
UPDATE users SET verified_at = created_at;

CREATE TABLE email_verifications (
  token_hash TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  email      TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at    INTEGER
);
