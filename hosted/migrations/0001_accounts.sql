-- Hosted Featherframe's directory (W-845): who is who, and which frame is
-- whose. Everything else about a household lives in its own Durable Object
-- and its server's data dir.

CREATE TABLE households (
  id            TEXT PRIMARY KEY,
  tz            TEXT NOT NULL DEFAULT 'UTC',
  apprise_token TEXT,                         -- routes a push to its household
  created_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX households_apprise ON households (apprise_token)
  WHERE apprise_token IS NOT NULL;

-- One login per household.
CREATE TABLE users (
  id           TEXT PRIMARY KEY,
  email        TEXT NOT NULL UNIQUE,
  household_id TEXT NOT NULL UNIQUE REFERENCES households (id),
  created_at   INTEGER NOT NULL
);

-- Invite-only: an email here may sign up.
CREATE TABLE invites (
  email      TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  used_at    INTEGER
);

-- Magic links and sessions: only a hash of the secret is kept.
CREATE TABLE login_links (
  token_hash TEXT PRIMARY KEY,
  email      TEXT NOT NULL,
  tz         TEXT,
  expires_at INTEGER NOT NULL,
  used_at    INTEGER
);
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users (id),
  expires_at INTEGER NOT NULL
);

-- A paired frame: its ID (the MAC), the hash of the key it made for itself
-- at first boot, and its household.
CREATE TABLE frames (
  device_id    TEXT PRIMARY KEY,
  household_id TEXT NOT NULL REFERENCES households (id),
  key_hash     TEXT,
  paired_at    INTEGER NOT NULL
);

-- A code on a frame's glass, waiting for its owner to type it.
CREATE TABLE pairing (
  code       TEXT PRIMARY KEY,
  device_id  TEXT NOT NULL,
  key_hash   TEXT NOT NULL,
  report     TEXT,                           -- what it said about itself (panel, board)
  expires_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX pairing_device ON pairing (device_id, key_hash);
