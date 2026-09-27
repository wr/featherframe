-- Setting up a frame from the phone (W-888). Two things let a new owner make
-- an account without an emailed invitation: a kit registered when it was
-- flashed for shipping (its MAC and a hash of its own key, the one it makes
-- at first boot), or a setup code printed on the card in the box. Each is
-- used once; after that the frame is paired like any other.
CREATE TABLE kits (
  device_id     TEXT PRIMARY KEY,
  key_hash      TEXT NOT NULL,
  kit           TEXT,
  note          TEXT,
  registered_at INTEGER NOT NULL,
  used_at       INTEGER,
  household_id  TEXT
);

CREATE TABLE setup_codes (
  code          TEXT PRIMARY KEY,   -- 8 letters, stored without the dash
  note          TEXT,
  created_at    INTEGER NOT NULL,
  used_at       INTEGER,
  household_id  TEXT
);

-- A sign-in link that also adds a frame: an existing account's owner who
-- scanned a new frame's code is sent one, and following it pairs that code.
ALTER TABLE login_links ADD COLUMN pair_code TEXT;

-- The setup page's own secret for a pairing code: the QR on the frame's
-- glass carries it, so the page can't be reached by guessing six letters.
ALTER TABLE pairing ADD COLUMN setup_token TEXT;
