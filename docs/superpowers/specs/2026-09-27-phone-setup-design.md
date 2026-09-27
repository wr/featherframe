# Setting up a new frame from the phone (W-888)

## Why

A kit given as a gift is set up by someone who has never seen the
Featherframe webapp, may not be the person who bought it, and has only the
card in the box. Today they would have to: join the portal, type
`https://app.featherframe.app` into it, be invited by email, sign in, find
*Pair a frame*, type the code off the glass, then find and type a BirdWeather
station ID. This design makes it: join the portal's Wi-Fi, scan the code on
the frame, type an email, tap **Set up frame**.

Most pre-order owners will use a public BirdWeather station near them rather
than a detector of their own, and have no account.

## What the owner does

1. **The card in the box** says: plug in the frame; on your phone, join the
   Wi-Fi network **Featherframe-Setup**. It also carries a setup code, used
   only when the kit was not registered (below).
2. **The portal** is Wi-Fi only, as today. A release build already points at
   `https://app.featherframe.app`. After saving: "Connecting to Wi-Fi. When
   your frame shows a code, scan it with your phone to finish setting up."
3. **The frame**, online and claimed by no one, shows its pairing screen as
   today plus a QR code for `https://app.featherframe.app/setup/ABCDEF` (the
   pairing code's letters), with "SCAN TO SET UP THIS FRAME" under it.
4. **The setup page** (phone, real internet, https): Email; Setup code (only
   if the kit is not registered); a nearby BirdWeather station, nearest
   preselected; **Set up frame**.
5. **Done**: the phone is signed in and lands on the webapp; the frame shows
   its first picture within about a minute; a welcome email arrives.

## Why not all in the portal

The portal is the ESP32's own access point: the phone has no internet while
on it, so nothing can be checked there (the code, the email, stations), and
browser geolocation needs a secure page, which a captive portal is not.
The phone's own connection, after the frame is online, has all of that.

## Who may set up an account

Signing up stays invite-only. Two things count as an invitation:

- **A registered kit.** After a kit is flashed for shipping,
  `firmware/tools/register_kit.py` asks it over USB (Improv `0xF0`, which
  already returns `id` and `key`) who it is and records `device_id` and
  `sha256(key)` in D1 `kits`. The Worker already checks every frame by MAC
  *and* key, so a frame that matches a registered, unused kit may create an
  account with only an email. The firmware is open source but each kit's key
  is its own random 32 hex, made at first boot and kept in NVS; a DIY build
  on the same firmware is not in `kits`. Erasing NVS (the flasher's *Erase*)
  makes a new key and so un-registers the kit; ordinary updates keep it.
- **A setup code** (D1 `setup_codes`), printed on the card, 8 letters from
  the pairing alphabet, shown `ABCD-EFGH`. One use. Covers a kit that was
  not registered, and anyone Wells wants to let in by hand.

Today's email invitations (`invites`) are unchanged.

## Parts

### 1. D1 — `hosted/migrations/0007_setup.sql`

```sql
CREATE TABLE kits (
  device_id TEXT PRIMARY KEY,
  key_hash TEXT NOT NULL,
  kit TEXT,                 -- ee03 | ee02 | other
  note TEXT,
  registered_at INTEGER NOT NULL,
  used_at INTEGER,          -- the setup that consumed it
  household_id TEXT
);
CREATE TABLE setup_codes (
  code TEXT PRIMARY KEY,    -- 8 letters, no dash
  note TEXT,
  created_at INTEGER NOT NULL,
  used_at INTEGER,
  household_id TEXT
);
```

A kit's `used_at` is set by the setup that made an account with it; after
that it is a frame like any other (removing and re-pairing it goes through
*Pair a frame*, or `/setup` signed in).

### 2. Fulfillment

- `POST /_admin/kit {device_id, key_hash, kit, note}` (bearer admin token,
  as the other `/_admin/*`), `INSERT OR REPLACE`, logged in `admin_log`.
- `POST /_admin/setup-codes {count, note}` → `{codes: [...]}`.
- `firmware/tools/register_kit.py --port /dev/cu.usbmodem… [--kit ee03]
  [--note …]`: Improv `0xF0` identity request over serial (reusing the
  framing in `ff_improv.cpp`), hashes the key locally, posts it with the
  token from the keychain (`featherframe-hosted-admin-token`). Never prints
  or sends the key itself.
- `/admin`: a *Kits* card (device id's last 6, kit, note, registered, set up
  → household email) and a *Setup codes* card (**Generate 10**, the list of
  unused codes to print, used ones with the household).

### 3. The pairing screen — `render/welcome.py`, `lobby.py`

- `render_pairing(code, color, expires, url="")`: when `url` is given, a QR
  code of it, drawn with `segno` (pure Python, no dependencies; added to
  `server/requirements.txt`) in black on the field at whole-pixel modules,
  quiet zone included, placed in the paper the bough leaves clear (the exact
  spot and size set on `make preview` of both panels; at least 30 mm across
  on the 13.3" and 10.3" glass), with "SCAN TO SET UP THIS FRAME" under it
  in the engraved capitals at the pairing line's size.
- The URL is upper case (`HTTPS://APP.FEATHERFRAME.APP/SETUP/ABCDEF`) so the
  QR uses alphanumeric mode (a smaller symbol, larger modules). The Worker
  matches `/setup/` case-insensitively.
- The existing line "PAIR THIS FRAME AT APP.FEATHERFRAME.APP" and the code
  stay: someone signed in on a laptop still types it.
- Lobby `/render` takes `url`; the Worker passes it; `LOBBY_DRAWING` →
  `boot-art-4` so every cached code is drawn again. Viewers' pairing
  (`/render-view`) gets the QR too (a tablet page shows the code in HTML;
  it gets a link, not a QR).

### 4. Firmware

- `release` and `release_ee02` build with
  `-DDEFAULT_SERVER_URL=\"https://app.featherframe.app\"`. Dev builds stay
  blank (mDNS). Only a frame with no stored `server` uses the default.
- Portal: the `server` parameter's label becomes "Server URL (optional)";
  WiFiManager's saved page says "Connecting to Wi-Fi. When your frame shows
  a code, scan it with your phone to finish setting up." (via its strings
  override, `WM_STRINGS_FILE`, or the save-page hook, whichever the vendored
  version supports).
- No protocol change.

### 5. Worker — the setup page

Routes (before the session check in `index.ts`; `setup.ts`):

- `GET /setup/<code>` → the page. The code is looked up in `pairing`
  (unexpired). Not found: "This code has expired. Your frame will show a new
  one within a minute." Signed in already: one button, **Add this frame to
  your account**, which posts the existing `/api/pair`.
- `GET /api/setup/stations?code=&lat=&lon=` or `&q=` → up to 5 stations.
  Needs a live pairing code (so it is not an open proxy). One BirdWeather
  GraphQL call: `stations(ne:, sw:, first: 100)` over a box of ±0.5° lat
  and the matching longitude span (about 55 km) around the point, or
  `stations(query:, first: 20)` for a name search; keep those with
  `latestDetectionAt` in the past 7 days; sort by great-circle distance;
  answer `{id, name, km, species, continent}` (`species` =
  `counts.species`, today's). Cached in the Cache API for an hour per 0.1°
  cell or query. No location from the phone: Cloudflare's `request.cf`
  latitude/longitude. BirdWeather unreachable or nothing found: `[]`.
- `POST /api/setup {code, email, setup_code?, station?, tz}`:
  1. Same-origin; rate limit 5 per hour per IP (`rate_hits`, shared with
     the waitlist's helper).
  2. The pairing row for `code`, unexpired, else the expired message.
  3. `email` normalised (`normEmail`), else "Enter an email address."
  4. An existing user with that email: create nothing; email a sign-in link
     that pairs this code on arrival (`login_links` gains a nullable
     `pair_code` column, migration 0007; `auth` pairs it after signing in,
     if the code is still live). Answer: "You already have an account. We
     sent a link to {email} to add this frame to it."
  5. Otherwise an invitation: the pairing row's `(device_id, key_hash)` in
     `kits` with `used_at` null, else a `setup_codes` row for the typed code
     with `used_at` null, else "Enter the setup code from the card in the
     box." / "That setup code is not valid." (counts against the limit).
  6. One D1 batch: household (tz from the phone), user, mark the kit or code
     used, `frames` row, delete the pairing row. Then
     `Household.init(hid, tz, seed)` and `Household.adopt(device, report)`.
  7. A session, as `auth` makes one; 303 to `/?welcome=1`.
  8. The welcome email (below), sent in `ctx.waitUntil`.
- The page is the Worker's own (the sign-in pages' style in `pages.ts`),
  no framework. Station list: radio rows, the nearest checked; "Use my
  location" (browser geolocation, then refetch); a search field (name,
  debounced). Copy:
  - Heading: "Set up your frame"
  - Email label: "Email" — hint: "Used to sign in. No password."
  - Setup code label: "Setup code" — hint: "On the card in the box."
  - Station heading: "Detection source"; intro: "Your frame shows species
    heard by a BirdWeather station near you. You can change this later."
  - Row: "{name} · {km} km · {n} species today"
  - Buttons: "Use my location", "Set up frame"
  - Search placeholder: "Search stations by name"
  - None found: "No stations nearby. You can choose a detection source
    later in Settings."
  - Flash on landing (`welcome=1`, the webapp's own flash): "Your frame is
    set up. It will show its first illustration in a minute."

### 6. The seed

- `Household.init(hid, tz, seed?)`: a seed is queued in the front door's
  `ingest` table as `POST /api/hosted/seed` with the seed as body, which the
  wake already posts to the server before `/api/hosted/run`.
- Server: `POST /api/hosted/seed` (hosted only, like `/api/hosted/run`)
  merges `{detection_backend, birdweather_station_id, region}` into the
  config through `Config.sanitize` and `svc.update_config`. It is a
  settings save, so a repeat is harmless.
- Seed: `detection_backend: "birdweather"`, `birdweather_station_id`, and
  `region` from the station's `continent`: North America →
  `north-america`, Europe → `europe`, Oceania → `australia`, Asia → `asia`;
  anything else leaves the default. No station: no seed.

### 7. The welcome email — `pages.ts` `welcomeEmail(station, km)`

Subject: "Your Featherframe is set up"

> Your frame shows the species heard at {station}, a BirdWeather station
> {km} km away. Each time the station hears a new species, the frame
> changes to that species' illustration.
>
> Sign in: go to app.featherframe.app and enter this email. We'll send you
> a link each time; there's no password.
>
> In the Featherframe webapp you can:
> - Choose a different station, or connect your own BirdNET-Pi or
>   BirdNET-Go detector (Settings → Detection source).
> - Add an OpenAI API key to draw species that have no illustration, and
>   to paint a daily collage (Settings → AI image generation).
> - Set quiet hours, the collage interval, and each frame's rotation and
>   update interval.
>
> Questions? Reply to this email.

With no station the first paragraph is: "Your frame is connected. Choose a
detection source in Settings so it can start showing species."

## Failure cases

- BirdWeather down: an empty list; setup still works; no seed.
- The phone's IP location is wrong (VPN, carrier): the list is for the
  wrong place; "Use my location" and search fix it.
- The pairing code expires mid-form (24 h TTL): the expired message; the
  frame is already showing its next code.
- Resend fails: setup still completes (the phone is signed in); logged.
- The frame is claimed by two people racing: the second `POST` finds no
  pairing row and gets the expired message.

## Testing

- Vitest, `hosted/test/setup.test.ts` (the pattern of `pairing.test.ts`):
  registered kit needs no code; unregistered needs one; a code works once;
  a kit works once; an existing email gets a link and no account; the link
  pairs the frame; the rate limit; the station filter and sort against a
  recorded GraphQL response; the seed's region mapping.
- pytest: the seed endpoint applies and is hosted-only; the pairing screen
  with a URL carries a QR that decodes to it (decode with `segno`'s own
  matrix, compared to a fresh encode, no zbar); both panels still render.
- On glass: the EE03 removed from the household shows the new pairing
  screen; scan it with a phone; run the whole flow with a test email.

## Out of scope

- Any change to the portal beyond its copy and the default URL.
- A map, or any BirdWeather UI beyond a list and a name search.
- BirdNET-Pi/Go detectors in the setup flow (the email points to Settings).
- Card design and printing.
