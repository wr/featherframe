# Server architecture

How the server in `server/featherframe/` is put together: the parts you
would otherwise have to read several files to understand. `AGENTS.md`
has the short version and the rules. The code wins when the two disagree;
fix this page when you change what it describes.

## Panels

Two kits are supported. The EE03 is a Seeed XIAO ePaper Kit (XIAO
ESP32-S3 Plus + 10.3" ED103TC2, 1404×1872, 16-level gray, IT8951). The EE02
is the 13.3" E Ink Spectra 6 (T133A01, 1200×1600, six inks, ~15 s full
refresh, no partial refresh). The wiki (github.com/wr/featherframe/wiki) has
the full spec, wiring, and battery numbers.

`featherframe/panels.py` describes each known panel. Every frame is drawn
for the panel it reports in `X-Panel` (`frames.panel_for`); the panel is
state, not a setting, and there is no override on the page.
`FEATHERFRAME_PANEL` only seeds the default on a fresh install. A frame also
describes its panel as facts (`X-Panel-Width`/`-Height` native canvas,
`X-Panel-Format` gray16|gray2|mono|spectra6, `X-Panel-Rotations`), so a name
`panels.py` does not know becomes a `Panel` built from them, keyed
`custom:WxH:format:rotations` (`panels.custom`; `panels.get` resolves the key,
so `Config` stays flat). A panel that is not 3:4 gets the same sheet,
contain-fitted on paper (`pipeline._fit_to_panel`); an unknown format is sent
gray16 at the right size with a page note, never a wrong-size image.

The firmware side of a port is in `docs/firmware.md`. `gray2` and `mono`
have no baked screens or push path yet.

## Data flow and the tick

**Data flow.** `BirdNET birds.db (read-only) → server ingest cursor → render →
packed framebuffer + ETag → firmware GET /api/frame (If-None-Match) → panel`.

**`service.py` is the hub.** A background thread runs `tick()` on the poll
interval. `tick()` is two passes and nothing else: `_tick_pictures()` decides
what each picture is OF (quiet hours + the optional collage held overnight, the
collage interval, the blocklist, new-species corroboration; nothing holds a
plate) and composes at most one sheet per picture; `_tick_frames()` then finishes
each picture into one output per frame that shows it. Every web handler just
reads bytes — nothing is ever rendered in a request. The default path is to do
nothing (priority: few panel refreshes).
Quiet hours are asked of the service (`quiet_window` / `in_quiet_hours`),
never of `Config` alone: Sunset → Sunrise is the sun at the source's
`location()`, read once per tick and never in a request, on the server's
`TZ` with daylight saving; with no location, 40° in the Region's hemisphere
on the zone's standard meridian (`config._sun_window`).

## Frame, household, picture, output

**Frame / Household / Picture / Output.** Four things, and everything
else follows from them.

*A frame* is any screen this server draws for: a kit, a second
kit, a TRMNL, a tablet. `frames.py` is the one registry (`frame_rows` in the kv
store) and there is no primary, no "active" seat: a row is `asking`, `on` or
`ignored`. Each row holds how it is fed (`transport`: kit | trmnl | page),
what the *device* reported (`reported`), and what the *owner* chose (`set`) —
kept apart, so a check-in never undoes a choice. `frames.capabilities(row)`
derives what a screen can be asked for from its transport and its own report,
never from a table of model names. `frames.panel_for(row)` is its panel: what
it reported, else the default (`FEATHERFRAME_PANEL` seeds a fresh install).

*The household* is `Config`: the source, quiet hours, the blocklist, image
generation, the collage interval — everything that is the same for every
screen, and the whole of the `/settings` form. `Config` still carries the
render fields (`panel`, `panel_rotation`, `mat_*`, `power_mode`,
`wake_interval_minutes`, `device_poll_seconds`, `mode`) because that is the
shape `pipeline.*` takes, but **the household's stored values for them are not
read for any frame and are not fields on the page**, and `Config.mode` /
`Config.panel` decide nothing. `frames.frame_config(row, household)` is the ONE place a
frame's effective config comes from: the household's, this frame's panel, that
panel's display defaults (`panels.PANEL_SETTINGS` bar `mode`), then the row's
`set` — all through `Config.sanitize`. `frames.shows_of(row)` answers which
picture it shows: the owner's choice, else its panel's own default.

*A picture* is what is drawn (see the two pictures, below). It is composed
once, as a sheet, whatever any frame's panel is.

*An output* is one frame's picture finished for that frame: fitted, matted,
dithered and packed with `frame_config(row)`, kept in the drawn store (below)
and pointed at by `_out[id].file`, and redrawn only when its picture or its
settings change (`_out[id].src` names both). `/api/frame` resolves the asking frame's row and
serves its bytes, its ETag, its own `X-FF-Rotation` / `X-Power-Mode` /
`X-Wake-Minutes` / `X-Poll-Seconds`, and its button views drawn with its
config. Its two intervals are `service.frame_intervals`: on plates the owner's,
on the collage the wait to just after `collage_next_at`, so a frame on the
collage checks in when there is something new and never on a clock of its own.

## Push

**Push, don't poll.** A kit on USB also holds a WebSocket to
`/api/frame/push` (same identity headers; only an `on` frame is kept). It is
sent `service.push_message` — `{etag, rotation, power, ota}`, everything that
changes what its next `/api/frame` would answer — on connect and whenever
that changes (`push.PushHub`, woken after every `tick()` and by a frame's
settings save/answer), and it answers each message with its usual GET, so
`/api/frame` stays the one contract. On the socket the firmware's timed poll
is a 15 min heartbeat (`FF_PUSH_HEARTBEAT_MS`), which it names in `X-FF-Push`
so overdue is measured against it (`0` = speaks push, no socket: polls as
told); an open socket reads as heard from, and a USB row offers no *Update
interval* at all. Battery frames never open one. A socket that closes
is a check-in at once (a removed frame finds its pairing code; the server
also sends a last message before closing one), and the firmware reopens it
every ~30 s (`FF_PUSH_RETRY_MS` + up to `FF_PUSH_JITTER_MS`) for as long as
it takes; meanwhile it polls at the served interval, which for a USB frame
that speaks push is at most `PUSH_FALLBACK_POLL_S` (60 s) whatever it shows —
never the collage's schedule. The offline corner mark (`FF_MARK_FAILS` failed
checks and `FF_MARK_MINUTES` = 30 min) is what an owner sees of an outage.
uvicorn needs `websockets`; its ping timeout is 60 s (`__main__`) because a
colour paint holds the frame's loop ~15 s. A hosted hub (Durable Object)
speaks the same protocol.

## Per-frame previews and telemetry

A viewer's output is the same idea as a PNG (`view_png`), drawn on
first ask and cached; `GET /api/frames/<id>/preview.png` serves either kind.
Telemetry is per frame too: a check-in lands on its own row
(`_record_checkin`), the battery log carries a `frame_id` (`GET
/api/battery?frame=<id>`), and `frame_health(row)` is the health block for
every frame. `frames_list()` / `frame_view(row)` is the one shape all of this
reaches the page in.

## Viewers

**Viewers are the frames fed over plain HTTP** — a TRMNL, an
e-reader, a tablet. They show a picture like every other frame and decide
nothing about it: a commit keeps the composed sheet (`RenderResult.sheet`,
before the panel fit, the mat and the dither) as the picture's `sheet.png`, and
`GET /api/view.png?w=&h=&format=&rotation=` (`service.view_png` →
`pipeline.render_view`) draws it again at the asked size: `gray16`/`gray2`/
`mono` blue-noise dithered, `gray256`/`color` smooth, no mat, a PNG. Its ETag
is its name in the drawn store (the sheet it is drawn from, the variant and
the finishing version); waiting plates alone stay in `data/frames/views/`. A view never touches a frame's
output, its row or the cursor. `gray16` at 1404×1872 is the EE03
preview pixel for pixel (a test holds it): TRMNL X is the same glass. Colour
for a gray frame's server is a second sheet (the picture's colour twin),
never any frame's pixels: every render hands `_commit` a `recompose` (the same
spec, art in colour), drawn while a colour kit shows that picture or a colour
viewer has asked within `COLOR_VIEWER_DAYS` (`color_viewer_at` in the DB). The
first ask draws the twin from the kept `recompose`; after a restart it costs
one re-render of the subject.
TRMNL's bring-your-own-server protocol is the first viewer client:
`GET /api/setup`, `GET /api/display`, `POST /api/log`, shaped by the firmware's
own source (`usetrmnl/trmnl-firmware`: `request_headers.cpp`, `display.cpp`),
which also covers TRMNL's Kobo/Kindle/KOReader clients. `service.checkin_viewer`
records the ask on that frame's row in the one registry — the device's report
apart from the owner's choices, which are saved through `POST /api/frames/<id>`
like any frame's — and `viewers.view_of` turns a row into a `View`: 16-gray
models get `gray16`, other
firmware builds `gray2` (the firmware truncates anything deeper, so we dither),
a client that reports no size a smooth 1072×1448 page; a landscape canvas
hangs portrait (rotation 90), as `panels._rotations`. The device repaints only
when `filename` changes: the picture's ETag plus the variant. Dithered views go
out at their true PNG depth (`pipeline.encode_png`; Pillow cannot write gray
below 8 bits). `refresh_rate` is `viewers.REFRESH_SECONDS` (hourly in quiet
hours), or the collage's own next redraw for a viewer on the collage. A viewer
never reaches `admit_frame` — but it is approved on
the server like every other frame: a new one is `asking`, `/api/setup` still
hands it its key, and `/api/display` answers `status: 0` with the *waiting
plate* (`welcome.render_waiting`, the wordmark over "ADD THIS FRAME ON THE
FEATHERFRAME WEBAPP" and its short id), drawn for that screen's own size, depth
and rotation, `filename` `waiting-<variant>`, `refresh_rate`
`WAITING_REFRESH_SECONDS` (`IGNORED_REFRESH_SECONDS` once it is ignored).
Its image is `GET /api/viewers/<id>/<name>.png`, and that path stays where it
is: a screen asleep holds the URL it was handed.
The kiosk page is the second client: `GET /view`
(`templates/view.html`, ES5 and XHR on purpose, for old iPads; a home-screen
web app via `/view.webmanifest`) names itself from localStorage, reports its
device pixels to `GET /api/view/state` every `viewers.PAGE_POLL_SECONDS`, and
is told which image to cross-fade to — or, while it is still `asking` or
`ignored`, `{"waiting": true, "id": …}` and no image, which the page shows as
the wordmark over "Add this frame on the Featherframe webapp" and its short id.
It keeps polling and takes the picture by itself once the owner adds it. A page
viewer (`transport: "page"`) is always
`color`, upright, long side capped at `PAGE_MAX_SIDE`, and shows the plate
whatever the hour (`/api/view/state` still answers `"dark": false` so a tab
open since an older build keeps working, and a `fmt`/`dark_quiet` left on a row
is ignored). A viewer has no card of its own: it is a row in the Frames
card like every other frame, offered *Rotation* and a pixel size only when it
reported none. How deep a viewer is drawn follows what the *device* reported,
never the owner (a Kobo script client stays smooth once sized).

## The two pictures

**Two pictures (`pictures.py`).** There are exactly
two, `plates` and `collage`, and they are the same kind of thing: each owns its
meta, its ETag, and its composed sheet (named by its ETag in the drawn store;
the `pictures` kv row holds the rest). A picture is drawn
only while some frame shows it — a kit per `frames.shows_of`, a viewer per
`viewers.shows_of` and only if it asked within `VIEWER_SHOWS_DAYS` — and is
dropped when the last one looks away (`_kinds_shown`, `_drop_picture`, which
keeps the one a frame is showing right now and the last one there is, so no
glass is ever blanked). A picture is only ever composed (`_commit`): the
gray sheet always, plus the colour twin while some screen showing it draws in
colour (`_color_wanted`). `picture_for(shows, now)` is the ONE place that
answers which picture a frame gets: it also carries the rule that in quiet
hours, once the nightly collage has been drawn, every frame on plates shows
that same collage picture for the rest of the window (`_kind_for`). Nothing
holds a picture; the blocklist is global; a frame's ETag/filename is
its own picture's (`picture_etag`), so a TRMNL on the collage does not repaint
for a new plate.

## The drawn store

**The drawn store (`drawn.py`).** Everything drawn is kept once,
named by what it was drawn from, under `data/frames/drawn/`: `sheets/<etag>.png`
(a picture's gray sheet; the ETag is its pixels' hash) and its colour twin
`sheets/<etag>-c<COLOR_VERSION>.png`, `out/<etag>-<okey>.fff|.png` (a frame's
output; `okey` hashes its `src` and `fkey`), `views/<sheet>-<view>-<fkey>.png`.
Pictures, frames and viewers point at files and never delete one; a species
that comes back finds its gray sheet by what it is drawn from (the
kv row's `sheets`, keyed by `drawn.sheet_key`: `_drawn_from`, the art's
`ref`, `SHEET_VERSION` and the libraries), so it is shown without composing
it or loading its art, and then finds its twin, outputs and views. Every
provider names its art without drawing it (`ArtProvider.ref`: "" none, None
can't say, which composes as before), and a sheet is kept under a key only
when the art it was drawn with is the art the key named. A twin is kept only
if its colour loaded and its art is the gray sheet's (`ff_art`, `_same_art`). Refresh deletes nothing: a new
token in the kv row `drawn` redraws that picture's files in place. The
pruner (`_prune_drawn`, after every commit) keeps what is pointed at (per
file) and the 25 most recent pictures (`drawn.KEEP_ETAGS`). **Bump `compose.COLOR_VERSION` when
the colour path changes its pixels, `compose.SHEET_VERSION` when a gray
single sheet does (layout, type, fonts, the bough), and
`pipeline.FINISH_VERSION` when finishing does** (fit, mat, dither, pack,
views): a kept drawing is reused by name. `tests/test_drawn_versions.py` fails when the pixels move without
them (record with `FF_RECORD_DRAWN=1`). On hosted the folder is lazy, and a
sync uploads, reports, then deletes. Forward only from the old layout
(`_migrate_drawn`).

## Adding and removing frames, and firmware updates

**Adding and removing frames.** A kit names itself with `X-Device-Id` (its
MAC). **Every frame of every transport is approved on the server**, the first
kit on a fresh install included: a new row is `asking` until the owner answers
on the page — *Add this frame* (`answer_frame(…, "add")`, which takes any
transport), *Ignore it*, or *forget*. A kit that is asking gets a 403 and shows
its own baked "Add this frame on the Featherframe webapp"; a viewer gets the
waiting plate, a page the waiting screen. There is no "replace":
there is no current frame to replace, so handing the server to a new
kit is adding it and removing the old one. `POST /api/frames/<id>` saves ANY
frame's settings, whatever it is fed over — only what `frames.capabilities`
allows is taken, unknown keys are ignored, and `{"forget": true}` removes it.
Firmware without the header is one frame called `"legacy"`, which becomes its
real ID in place — with its output — after an update. There is no panel-swap
notice: a frame's panel is simply what it reports, so there is nothing to
answer; the "unrecognised panel" and "unknown format" notes stay, per frame
(`frame_notices`). OTA serves
each board its own image: `firmware.bin` and any `firmware-*.bin` in the data
dir are candidates, matched by the board string (`_firmware_for`). An official
release comes first: a `v*` tag makes CI publish
`firmware-manifest.json` + per-kit images (`.github/workflows/release.yml`,
`firmware/tools/release_assets.py`); `firmware_release.ReleaseStore` asks
GitHub's `releases/latest` daily from `tick()`, downloads a kit's image only
once a frame is pending and keeps it only if it matches the manifest. A frame
is pending when the owner pressed *Update* on its row (`set.update_firmware`)
or `Config.firmware_auto_update` is on and it already runs an official
`MAJOR.MINOR.PATCH` (a dev build is never replaced unasked);
`service.release_image_for` hands it over, and a dev image dropped into the data
dir (`firmware.bin`) older than that hand-over is not served to that frame. The frame shows nothing about it. The
Frames card's *USB firmware update* installs the same release with
esp-web-tools (vendored in `server/static/flash/`, from
`/api/flash/<kit>/manifest.json`: bootloader, partitions, boot_app0, app —
never NVS, so a board keeps its Wi-Fi unless the owner ticks Erase). Web
Serial needs a secure page, so on plain http the dialog links to the same
flasher on GitHub Pages (`flasher/index.html`, published by the release
workflow). The firmware speaks Improv over USB (`ff_improv.cpp`) so the
flasher can give a blank board its Wi-Fi; the new frame then asks to connect
like any other. On a hosted page *Connect* asks the frame first (Improv info +
state, then Featherframe's 0xF0): a frame already on the release and on Wi-Fi
pairs in the dialog with no flasher; anything else opens esp-web-tools' dialog
on that port, which steps aside to pair once it has installed or set up Wi-Fi. mDNS
advertises the first `on` kit's panel key; a frame whose panel no server claims
takes any that answers.

## The webapp's password

**The page's password (`auth.py`) is off by default.** *Require a
password* in the page's *General settings* card (with *Email* and the
firmware auto-update switch) turns on a sign-in page (`/login`: the owner's
email, `Config.owner_email`, plus the password) that sets a signed session
cookie (`ff_session`, bound to the password's salt, so a new password signs
everyone out). Only a PBKDF2 hash is kept, in the kv store, never in
`Config`. Every route asks for it except what screens use (`auth.is_open`).
Never on hosted: the Worker signs in there, says the account's email to the
page in `X-FF-Account-Email`, and takes the form's `owner_email` itself — a
change is confirmed by a link to the new address (`email_changes`, D1
migration 0003). `python -m featherframe --clear-password` is the way back
in. A generated plate's repaint is capped at `REGEN_PER_HOUR`.

## Detection sources

**Ingest (`birdnet.py`) is strictly read-only.** Opens `?mode=ro`, never writes
or locks BirdNET's DB. The cursor is `WHERE rowid > :last`. Every method
soft-fails to a safe default (None/[]/0) so a missing or odd DB keeps the current
frame instead of crashing. Fixture schema in `tests/_fixtures.py` is verbatim
from the Nachtzuster fork.

**BirdNET-Pi and BirdNET-Go push (`sources/pushed.py`).** One
`PushedSource`, two bodies: BirdNET-Pi's Apprise notification
(`/api/ingest/apprise/<token>`) and BirdNET-Go's webhook channel
(`/api/ingest/birdnet-go/<token>`, its default JSON, no template), both behind
`Config.ingest_token` (was `apprise_token`), which is part of the URL the page shows, never a form field: its ⟳ replaces it whole, saved at once (`POST /api/ingest/token`). BirdNET-Go is never polled: its
push needs a *Rules* entry (Detection → *Detection Occurred* → Push
notification, cooldown 0), or it pushes only new species; the built-in
new-species rule pushes the same detection again, deduped on `note_id`; its
channel *Test* posts *Testus birdicus*, kept as `test_at` and never shown. A
push feed keeps its own history as it goes: a per-day, per-species tally in
confidence tenths (the collage's counts outlast the 1000-item window) and,
from BirdNET-Go's `days_since_first_seen`, each species' first date.

## The webapp

**The page is two halves, and one row component.** Left, narrow:
metadata only, never a setting — the live preview with a chip per frame under
it (the picked frame is kept in `localStorage`; a kit's own `out/<id>.png`, a
viewer's own view — both at `GET /api/frames/<id>/preview.png`, and always the
upright picture as that frame draws it, never the device's canvas shape or its
rotation: a TRMNL's is stood up and a page's is the sheet at 3:4) and the plate's
tools, which are the previewed frame's picture's (`frame_view`'s `picture`, the
night rule included) and say they act on every frame showing it: Refresh
and Manual override, one line each (blocking a species is under
Detection source; nothing holds a picture); then History, which
leaves out the welcome plate. **There is no Health
card**: a frame's health is the frame's row, and the source's is its row below.
Right, wide: two groups, each a heading over its card. *Frames*, with
a ⋯ menu holding *USB firmware update*, *Check for updates* (asks for the latest release now, `POST /api/firmware/check`; otherwise daily), *Buy a frame* and *DIY instructions* (and *Pair a frame* on hosted), then the list.
Then *Settings*: the household's, one card of rows (`details.disc.set`, each
an icon, a name, a short value and a side chevron, which turns down when it
opens, as a frame row's does): General (email, password, firmware auto-update),
Detection source (its status dot + name; open, the gone-quiet and outage
warnings, Last detection / Species heard / Pending, the source, a push source's
Webhook URL and *Setup instructions* — Done once `_push_setup` says a test or a
detection arrived, live from `/api/status` — and Blocked species; a source that
is failing or not set up opens itself; not set up, `service.source_unset` (a
push source nothing reached, BirdWeather with no station, no BirdNET-Pi database
at the default path), it is gray, raises neither alarm, and the welcome plate
asks for it in a plain line instead of the "unreachable" pill), Illustrations (Region, "Audubon · North America"), Collage
(interval and quiet hours, "Every 6 hours · Sunset → Sunrise", each shown —
in the summary and as a row — only while some added frame follows it:
the interval a frame on the collage, quiet hours a frame on individual
detections, both with no frame yet; `collage_timings`, live on the page as
Content changes; a hidden one keeps its value; quiet hours IS the overnight
collage, `Config.quiet_hours_render_collage` is a property), AI
image generation (its first row is the master switch, `imagegen_enabled`; then
a **Monthly limit** in dollars; the summary is one state from
`service.ai_view`: Off, No API key, Key rejected, Out of credits, Paused, Limit
reached, or "OpenAI · $1.20 of $10.00"; its two AI switches, which live in
Illustrations and Collage, are `locked` until a key is stored and while the
master switch is off), then
Generated illustrations and Generated collages (each kept day's collage, same
list). Every section is its own `/settings` form carrying a `section` field:
`/settings` takes only the fields posted (a switch posts a hidden 0 before its
checkbox, so absent keeps the stored value) and lands back with that section open.
One save rule: a section's Save row and *Unsaved changes* appear
only once it changed (with a badge on the row if it is folded), each frame
row's Save likewise; a switch saves the moment it is flipped; a stored secret,
the email and the password are ✓ / ✕ rows whose ✓ waits for input; a hidden
first submit keeps Enter saving its section.
Every frame is the same row (the `frame_row` macro), and that row **is** the
page's own disclosure (`details.disc`), so it hovers, turns its chevron and
slides open exactly as *Advanced* does. Collapsed it is a conventional
device-list line: the status dot (`card.state`, the one place it is decided —
green heard from on time, amber overdue, red battery critical, grey never or a
page not open), the name, an *Overdue* / *Battery low* badge, `frames_list()`'s
`summary` muted under it (what it is, what it shows, and when that changes:
"Collage Sunset → Sunrise" on individual detections, the collage's interval
on the collage — each frame is told the schedule it follows), then
fixed columns for battery, Wi-Fi and last seen
(the Wi-Fi column goes at ≤ 520 px). The battery column is a cell and a percent
only on a frame set to Battery — hover it for that frame's own 24 h voltage
trend — and the plug on USB; the Wi-Fi bars go faint on a frame that is not
being heard from. Open, its own settings **by capability** —
Name, Content, Rotation in degrees, Power (USB | Battery), *Update interval*
on Battery only (a minute to a day, `wake_interval_minutes`, `locked`
to the collage's own interval while that is what the frame shows; on USB a
kit takes a change at once, so its `device_poll_seconds` is served but never
offered or posted), Screen size only when `needs_size` — then
*Advanced* (the mat inset and offset — the inset starts at its panel's `Panel.mat_inset_pct`, 4 % on both kits, which their mats need; `set` keeps a mat value only where it differs from that — the *Mat guide* switch — `mat_guide`, a
2 px line just inside the composition to set them by — and *Reset to
defaults*) and *Details*, which is what this
frame REPORTED and nothing the row above already says: IP address, firmware,
panel, board, frame id, each only where the frame reports one. Then Remove on
the left and Save on the right, where the household form's own Save sits. The
list is flush in its card — the card's vertical padding is 0 and it clips to
its own radius, so each row's own padding is the spacing and the first and last
rows' hover backgrounds reach the card's edges. Saving posts JSON to
`POST /api/frames/<id>` and updates the row in place; the status poll keeps
every summary, dot, badge and reading current and reloads only when the set of
frames itself changes. A kit that is asking is a notice at the top of the card
(*{what it is} wants to connect*, *Add* / *Ignore*; a plain notice, not a
warning), ignored ones fold at the bottom, and with no frames at all the
card is an invitation: `<host>/view` on a tablet, or the kit, each ending as
this server meets a new frame (self-hosted, it will ask to be added here; on
Cloud it will show a code, with *Pair a frame* in the card). There are no per-frame banners: the row's badges say
it. `status()["frames"]["list"]` is the whole of it, one shape per frame;
`status()["current"]` is what the pictures are of, not any frame's view.

## Config and constants

- **Config** is one flat `Config` dataclass (`config.py`), persisted as a JSON
  blob in our own SQLite (`db.py`, a kv store, separate from BirdNET's DB).
- **What an owner never tuned is a constant, not a setting.** The
  poll interval, confidence floor (BirdNET-Go's own threshold always wins;
  the floor is for sources with none), both alarm thresholds and the
  corroboration numbers live as named constants at the top of `service.py`;
  saturation is `spectra.SATURATION`. Single mode always shows the latest
  qualifying detection, the most novel of a tick's first; one whose sheet
  would be the one shown is not drawn again (`_drawn_from`), unless
  that sheet lacks art an illustration could now give it. Think twice before turning one back into a field.

## Paths and mDNS

- Paths are env-overridable: `FEATHERFRAME_DATA_DIR`, `FEATHERFRAME_PLATES_DIR`,
  `FEATHERFRAME_DB`, `FEATHERFRAME_PORT` (see `paths.py`). `install.sh` sets these
  in the systemd unit.
- **A dev server never advertises mDNS.** Any server you start on a
  LAN with frames on it that is not their server — `.claude/launch.json` entries, a bench
  instance, a one-off `python -m featherframe` — runs with
  `FEATHERFRAME_NO_MDNS=1` (`discovery.disabled_by`; `FEATHERFRAME_MDNS=0` is
  the same switch; `/api/status` then says `mdns.advertised: false`, error
  `disabled (…)`). The firmware rediscovers on its first failed fetch and
  adopts any server advertising its panel, so a frame whose own server is
  restarting takes a dev server instead. `.claude/launch.json` is
  untracked, so put the variable in each time you write one. The test suite
  turns advertising off for itself (`conftest._no_mdns`). `make serve`
  advertises, because it is also how an owner runs the server; a frame on the
  bench that should find a dev server gets its URL typed into the portal.
