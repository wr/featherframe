# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Source of truth
- GitHub: github.com/wr/featherframe
- Linear project: Featherframe (id: cb3f0377-9778-49b2-a57f-e9f39cfb3de5), Personal team (W)
- Branch prefix: wells/
- PR mode: ready

## What this is

Featherframe: a wall-mounted e-paper frame that renders the birds your BirdNET-Pi
hears as Audubon lithograph plates. Two components in one repo:

- **`server/`** — Python/FastAPI, runs *on the BirdNET-Pi* as a systemd service in
  its own venv. Owns all logic and all image processing.
- **`firmware/`** — ESP32-S3 (PlatformIO), a deliberately dumb deep-sleep client
  that fetches a pre-packed framebuffer and pushes it to the panel.

The wall frame is a Seeed XIAO ePaper Kit EE03 (XIAO ESP32-S3 Plus + 10.3"
ED103TC2, 1404×1872, 16-level gray, IT8951). See `README.md` for the full spec,
wiring, and battery numbers. A second panel is being ported (W-812): the EE02
kit's 13.3" E Ink Spectra 6 (T133A01, 1200×1600, six inks, ~30 s full refresh,
no partial refresh). One server instance drives one panel (`config.panel`,
`featherframe/panels.py`); it follows the device's `X-Panel` report on first
check-in, and `FEATHERFRAME_PANEL` seeds a fresh install. A frame also
describes its panel as facts (W-813: `X-Panel-Width`/`-Height` native canvas,
`X-Panel-Format` gray16|gray2|mono|spectra6, `X-Panel-Rotations`), so a name
`panels.py` does not know becomes a `Panel` built from them, keyed
`custom:WxH:format:rotations` (`panels.custom`; `panels.get` resolves the key,
so `Config` stays flat). A panel that is not 3:4 gets the same sheet,
contain-fitted on paper (`pipeline._fit_to_panel`); an unknown format is sent
gray16 at the right size with a page note, never a wrong-size image. The
panel is state, not a setting (W-821/W-833): every frame is drawn for the
panel IT reports (`frames.panel_for`) and there is no override on the page. The firmware side of a port is `-DFF_GENERIC_PANEL` (W-819, the
`generic_bench` env: the EE03's own glass under a label the server does not
know): the panel comes from build flags, its screens from
`bake_screens.py --size WxH --format gray16|spectra6 --rotation N --out …`
(baked at build time by `tools/bake_generic.py`, not committed), and it runs
the EE02's full-refresh path. The app branches on two facts, never on the
panel: `FF_FULL_REFRESH` (no windowed update) and `FF_FRAME_INKS` (nibbles are
ink codes); `fullPaint()` is the one driver call on that path. `gray2`/`mono`
have no baked screens or push path yet.

## Commands

All Make targets run from the repo root; they drive `server/.venv`.

```bash
make venv              # create server venv + install requirements
make plates            # download Audubon plates (scripts/fetch_plates.py)
make plates-all        # cache every Havell plate (~2.9 GB, idempotent, retries)
make preview           # END-TO-END render of a fake Cardinal -> test_output/*.png + *.fff
make preview-all       # one PNG per curated species
make preview-collage   # a daily collage
make preview-fallback  # the typographic (no-plate) fallback
make preview-ee02      # the Cardinal for the EE02 colour panel (six-ink dither)
make preview-views     # the Cardinal as viewers get it (TRMNL X, Kobo, Kindle, TRMNL OG, tablet)
make serve             # run the server on :8080
make test              # pytest
```

`make preview` is the headline verification path — the whole art pipeline with no
hardware and no birds. Use it after any render change and eyeball `test_output/`.

More granular:

```bash
cd server
./.venv/bin/python -m pytest tests/test_names.py -q                  # one test file
./.venv/bin/python -m pytest -k cursor -q                            # by keyword
./.venv/bin/python -m featherframe.preview --species "Blue Jay"      # any species
./.venv/bin/python -m featherframe.preview --dither stucki           # bench override, never persisted
./.venv/bin/python scripts/fetch_plates.py --dry-run                 # resolve plates, no download
./.venv/bin/python -m featherframe --port 8080                       # run the server directly

# Firmware
cd firmware && pio run -t upload && pio device monitor  # build/flash + serial (115200)
cd firmware && pio run -e ee02                          # the EE02 colour-panel build (W-812)
cd firmware && pio run -e ee02_bench                    # Spectra 6 refresh-speed bench (serial-driven; test_bench_ee02/)
cd firmware && pio run -e release                       # the binary a kit ships with (release_ee02: colour kit)
cd firmware && pio run -e generic_bench                 # a panel the server has never heard of (W-819); bakes its own screens
```

Deploy to the Pi: `cd server && ./install.sh` (venv + plates + systemd unit).

## Architecture — the parts you must read several files to grasp

**Data flow.** `BirdNET birds.db (read-only) → server ingest cursor → render →
packed framebuffer + ETag → firmware GET /api/frame (If-None-Match) → panel`.

**`service.py` is the hub.** A background thread runs `tick()` on the poll
interval. `tick()` is two passes and nothing else: `_tick_pictures()` decides
what each picture is OF (quiet hours + the optional collage held overnight, the
collage interval, the blocklist, new-species corroboration, the dwell hold)
and composes at most one sheet per picture; `_tick_frames()` then finishes
each picture into one output per frame that shows it. Every web handler just
reads bytes — nothing is ever rendered in a request. The default path is to do
nothing (priority: few panel refreshes).

**Frame / Household / Picture / Output (W-833).** Four things, and everything
else follows from them.

*A frame* is any screen this server draws for: the kit on the wall, a second
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
dithered and packed with `frame_config(row)`, kept as `data/frames/out/<id>.fff`
(+ `.png` preview) and redrawn only when its picture or its settings change
(`_out[id].src` names both). `/api/frame` resolves the asking frame's row and
serves its bytes, its ETag, its own `X-FF-Rotation` / `X-Power-Mode` /
`X-Wake-Minutes` / `X-Poll-Seconds`, and its button views drawn with its
config. Its two intervals are `service.frame_intervals`: on plates the owner's,
on the collage the wait to just after `collage_next_at`, so a frame on the
collage checks in when there is something new and never on a clock of its own.
**Push, don't poll (W-841).** A kit on USB also holds a WebSocket to
`/api/frame/push` (same identity headers; only an `on` frame is kept). It is
sent `service.push_message` — `{etag, rotation, power, ota}`, everything that
changes what its next `/api/frame` would answer — on connect and whenever
that changes (`push.PushHub`, woken after every `tick()` and by a frame's
settings save/answer), and it answers each message with its usual GET, so
`/api/frame` stays the one contract. On the socket the firmware's timed poll
is a 15 min heartbeat (`FF_PUSH_HEARTBEAT_MS`), which it names in `X-FF-Push`
so overdue is measured against it (`0` = speaks push, no socket: polls as
told); an open socket reads as heard from, and the row's *Update interval*
is locked to *Instant*. Battery frames never open one. A server without the
endpoint costs the firmware three refused opens, then a retry every 10 min.
uvicorn needs `websockets`; its ping timeout is 60 s (`__main__`) because a
colour paint holds the frame's loop ~30 s. A hosted hub (Durable Object)
speaks the same protocol.
A viewer's output is the same idea as a PNG (`view_png`), drawn on
first ask and cached; `GET /api/frames/<id>/preview.png` serves either kind.
Telemetry is per frame too: a check-in lands on its own row
(`_record_checkin`), the battery log carries a `frame_id` (`GET
/api/battery?frame=<id>`), and `frame_health(row)` is the health block for
every frame. `frames_list()` / `frame_view(row)` is the one shape all of this
reaches the page in.

**Viewers (W-822) are the frames fed over plain HTTP** — a TRMNL, an
e-reader, a tablet. They show a picture like every other frame and decide
nothing about it: a commit keeps the composed sheet (`RenderResult.sheet`,
before the panel fit, the mat and the dither) as the picture's `sheet.png`, and
`GET /api/view.png?w=&h=&format=&rotation=` (`service.view_png` →
`pipeline.render_view`) draws it again at the asked size: `gray16`/`gray2`/
`mono` blue-noise dithered, `gray256`/`color` smooth, no mat, a PNG. Its ETag
is the picture's plus the variant; renders are cached in `data/frames/views/`
(a handful, dropped with the picture). A view never touches a frame's
output, its row or the cursor. `gray16` at 1404×1872 is the EE03
preview pixel for pixel (a test holds it): TRMNL X is the same glass. Colour
for a gray frame's server is a second sheet (the picture's `sheet_color.png`),
never any frame's pixels: every render hands `_commit` a `recompose` (the same
spec, art in colour), drawn while a colour kit shows that picture or a colour
viewer has asked within `COLOR_VIEWER_DAYS` (`color_viewer_at` in the DB). The
first ask draws the twin from the kept `recompose`; after a restart it costs
one re-render of the subject.
TRMNL's bring-your-own-server protocol is the first viewer client (W-824):
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
FEATHERFRAME PAGE" and its short id), drawn for that screen's own size, depth
and rotation, `filename` `waiting-<variant>`, `refresh_rate`
`WAITING_REFRESH_SECONDS` (`IGNORED_REFRESH_SECONDS` once it is ignored).
Its image is `GET /api/viewers/<id>/<name>.png`, and that path stays where it
is: a screen asleep holds the URL it was handed.
The kiosk page (W-825) is the second client: `GET /view`
(`templates/view.html`, ES5 and XHR on purpose, for old iPads; a home-screen
web app via `/view.webmanifest`) names itself from localStorage, reports its
device pixels to `GET /api/view/state` every `viewers.PAGE_POLL_SECONDS`, and
is told which image to cross-fade to — or, while it is still `asking` or
`ignored`, `{"waiting": true, "id": …}` and no image, which the page shows as
the wordmark over "Add this frame on the Featherframe page" and its short id.
It keeps polling and takes the picture by itself once the owner adds it. A page
viewer (`transport: "page"`) is always
`color`, upright, long side capped at `PAGE_MAX_SIDE`, and shows the plate
whatever the hour (`/api/view/state` still answers `"dark": false` so a tab
open since an older build keeps working, and a `fmt`/`dark_quiet` left on a row
is ignored). A viewer has no card of its own: it is a row in the Frames
card like every other frame, offered *Rotation* and a pixel size only when it
reported none. How deep a viewer is drawn follows what the *device* reported,
never the owner (a Kobo script client stays smooth once sized).

**The page is two halves, and one row component.** Left, narrow:
metadata only, never a setting — the live preview with a chip per frame under
it (the picked frame is kept in `localStorage`; a kit's own `out/<id>.png`, a
viewer's own view — both at `GET /api/frames/<id>/preview.png`, and always the
upright picture as that frame draws it, never the device's canvas shape or its
rotation: a TRMNL's is stood up and a page's is the sheet at 3:4) and the plate's
tools; then the detection source's own small card, titled by the source name;
then History. **There is no Health card**: a frame's health is the frame's row.
Right, wide: a **Frames** card FIRST — just the list, no heading — then the
household's sections in one `/settings` form that carries no frame field at
all, in the order the day runs: Detection source, Image generation (its two AI
switches `locked` until a key is stored), Individual detections, Collage — and
quiet hours IS the overnight collage, so it sits in that section and has no
toggle of its own (`Config.quiet_hours_render_collage` is a property: the
window being on is the whole of it) — then Generated plates.
Every frame is the same row (the `frame_row` macro), and that row **is** the
page's own disclosure (`details.disc`), so it hovers, turns its chevron and
slides open exactly as *Advanced* does. Collapsed it is a conventional
device-list line: the status dot (`card.state`, the one place it is decided —
green heard from on time, amber overdue, red battery critical, grey never or a
page not open), the name, an *Overdue* / *Battery low* badge, `frames_list()`'s
`summary` muted under it, then fixed columns for battery, Wi-Fi and last seen
(the Wi-Fi column goes at ≤ 520 px). The battery column is a cell and a percent
only on a frame set to Battery — hover it for that frame's own 24 h voltage
trend — and the plug on USB; the Wi-Fi bars go faint on a frame that is not
being heard from. Open, its own settings **by capability** —
Name, Content, Rotation in degrees, Power (USB | Battery), one *Update
interval* dropdown, a minute to a day, whose value swaps with Power (seconds →
`device_poll_seconds`, minutes → `wake_interval_minutes`; only the shown one is
posted) and which is `locked` to the collage's own interval while that is what
the frame shows, Screen size only when `needs_size` — then
*Advanced* (the mat inset and offset, the *Mat guide* switch — `mat_guide`, a
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
(*{what it is} wants to connect*, *Add* / *Ignore*), ignored ones fold at the
bottom, and with no frames at all the card is an invitation: `<host>/view` on a
tablet, or build the kit. There are no per-frame banners: the row's badges say
it. `status()["frames"]["list"]` is the whole of it, one shape per frame;
`status()["current"]` is what the pictures are of, not any frame's view.
**Two pictures (`pictures.py`).** There are exactly
two, `plates` and `collage`, and they are the same kind of thing: each owns its
meta, its ETag, and its composed sheet (`data/frames/pictures/<kind>/
sheet[_color].png`; the `pictures` kv row holds the rest). A picture is drawn
only while some frame shows it — a kit per `frames.shows_of`, a viewer per
`viewers.shows_of` and only if it asked within `VIEWER_SHOWS_DAYS` — and is
dropped when the last one looks away (`_kinds_shown`, `_drop_picture`, which
keeps the one a frame is showing right now and the last one there is, so no
glass is ever blanked). A picture is only ever composed (`_commit`): the
gray sheet always, plus the colour twin while some screen showing it draws in
colour (`_color_wanted`). `picture_for(shows, now)` is the ONE place that
answers which picture a frame gets: it also carries Wells's rule that in quiet
hours, once the nightly collage has been drawn, every frame on plates shows
that same collage picture for the rest of the window (`_kind_for`). A hold pins
plates and nothing else; the blocklist is global; a frame's ETag/filename is
its own picture's (`picture_etag`), so a TRMNL on the collage does not repaint
for a new plate.
**Adding and removing frames.** A kit names itself with `X-Device-Id` (its
MAC). **Every frame of every transport is approved on the server**, the first
kit on a fresh install included: a new row is `asking` until the owner answers
on the page — *Add this frame* (`answer_frame(…, "add")`, which takes any
transport), *Ignore it*, or *forget*. A kit that is asking gets a 403 and shows
its own baked "Add this frame on the Featherframe page"; a viewer gets the
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
release comes first (W-836/W-838): a `v*` tag makes CI publish
`firmware-manifest.json` + per-kit images (`.github/workflows/release.yml`,
`firmware/tools/release_assets.py`); `firmware_release.ReleaseStore` asks
GitHub's `releases/latest` daily from `tick()`, downloads a kit's image only
once a frame is pending and keeps it only if it matches the manifest. A frame
is pending when the owner pressed *Update* on its row (`set.update_firmware`)
or `Config.firmware_auto_update` is on and it already runs an official
`MAJOR.MINOR.PATCH` (a dev build is never replaced unasked);
`service.release_image_for` hands it over, and a `make ota` image older than
that hand-over is not served to that frame. The frame shows nothing about it. The
Frames card's *Add a frame over USB* (W-840) installs the same release with
esp-web-tools (vendored in `server/static/flash/`, from
`/api/flash/<kit>/manifest.json`: bootloader, partitions, boot_app0, app —
never NVS, so a board keeps its Wi-Fi unless the owner ticks Erase). Web
Serial needs a secure page, so on plain http the dialog links to the same
flasher on GitHub Pages (`flasher/index.html`, published by the release
workflow). The firmware speaks Improv over USB (`ff_improv.cpp`, W-839) so the
flasher can give a blank board its Wi-Fi; the new frame then asks to connect
like any other. mDNS
advertises the first `on` kit's panel key; a frame whose panel no server claims
takes any that answers.

**Ingest (`birdnet.py`) is strictly read-only.** Opens `?mode=ro`, never writes
or locks BirdNET's DB. The cursor is `WHERE rowid > :last`. Every method
soft-fails to a safe default (None/[]/0) so a missing or odd DB keeps the current
frame instead of crashing. Fixture schema in `tests/_fixtures.py` is verbatim
from the Nachtzuster fork.

**The collage is one sheet set two ways (`render/collage.py`).** The free grid
(`render_collage`, as many plates as `collage_species_max`) and the generated composite
(`render_generated_collage`, one painted scene) share `_bottom_block`: no
header at all, the art from the top margin down, then the date in the engraved
capitals, spaced wide, over a numbered key ("1. BLUE JAY") in prominence order
packed into columns along the bottom. The grid's cells carry the matching
figure numerals; there are no script names and no "×count" under them. AI
collages are all or nothing (`config.collage_generated`): with the toggle on
and image generation to hand, EVERY collage is the generated sheet — a daytime
rebuild, the nightly one, the button, a settings re-render. `genart.day_composite`
is what bounds the cost: one sheet per day, reused for every redraw of that day
and bought again only when the day's *species list* changes under it (a count
moving is not a change, `collage.same_species`), with the per-key cooldown and
the soft-fail to the grid intact.

**Render pipeline (`render/`).** `pipeline.py` orchestrates:
`compose.render_single` (or `collage.render_collage`) → `finish.to_levels`
(dither) → `framebuffer.pack`. The provider seam is `provider.py`: `ArtProvider`
returns bird artwork or `None`. The live chain is
`ChainedProvider([AudubonProvider, GeneratedArtProvider])` → typographic
fallback. `genart.py` is the AI side: `ImageModel` is the vendor seam
(`OpenAIImageModel` first, plain `requests`, default `gpt-image-2.5-sunburst`
via `/v1/images/edits` with real plates as style references — chosen over
`-flare` in the W-726 bake-off, which pads a lone figure into a pair and so
desyncs the printed legend; the 2.5 pair are the only models that accept the
`xhigh` and `max` qualities — `OpenAIImageModel.effective_quality` clamps those to
`high` on anything older, since the stored config outlives a model switch);
generated plates are
cached forever in `data/generated/` (PNG + JSON sidecar) and only a manual
regenerate from the config page replaces one; failures soft-fail to the
fallback with a per-species cooldown. The user's API key lives only in our DB
and is masked in `status()` and the UI. `plate.py` does the content-aware crop
of a scan (a generated PNG is `plate.extract_generated`: paper-normalised,
never cropped — it is composed to fill the sheet, and the scan crop once
decapitated a tern): the
heaviest ink band, extended through faint contiguous ink (hanging straw) up
to a real paper gap, then mirrored about the plate centre so Audubon's own
placement survives. The art is full-bleed to the mat opening (W-707):
`compose.py` cover-fits a plate whose edges are inked (Snowy Owl) only if
that crops ≤ 25 % of it, else contain-fits it centred; the date and plate marks
share one footer baseline with the gone-quiet note;
`typography.py` sets the caption (W-708): a copperplate script title
(Pinyon Script, OFL, bundled in `featherframe/fonts/`; Garamond italic is the
stand-in if it is ever absent), the engraved
Latin name, and the plate's own legend lines from
`scripts/legends.yaml` (Audubon's printed figure key and plant, transcribed
per Havell plate; `featherframe/legends.py` reduces a composite sheet to the
detected species' line);
the date · time and "Plate CLIX" sit in the bottom
corners: the Havell plate number (`Artwork.audubon_plate`, W-821), "Plate" in
the same script and the roman numeral in the engraved capitals, since a run
of script capitals is unreadable. A generated sheet carries a ✦ there
instead, and the bough of a species with no plate carries nothing. `theme.py` holds all geometry/tone constants.

**The plate library (W-842, `plate_library.py`)** is the scans' crops taken
once: `library.json` (index.json's species, each naming its crop by
`library` key) + `lib/<key>.gray.png` (`plate.extract`'s output) +
`lib/<key>.color.webp` (the raw colour crop, normalised at load). Both
lossless, so a plate from the library is the plate from the scan (a test
holds it); ~1.2 GB for the edition. `FEATHERFRAME_PLATE_LIBRARY` (a directory
or a URL, fetched on first use into `data/plate-library/`) puts
`LibraryProvider` in place of `AudubonProvider` — for a server with no scans,
the hosted render Container first. Build: `python -m featherframe.plate_library
build OUT_DIR` on a machine with every scan.

**Hosted (W-841–W-845, `hosted/`).** A household is `<name>.featherframe.app`
(the apex is the marketing page, not routed). The Worker (`hosted/src/index.ts`)
has two Durable Objects: `Household`, the front door, answers `GET /api/frame`
from its own table + R2 and holds the push sockets, keeps check-ins until the
server takes them, and wakes the server on its alarm (the server's
`next_wake_epoch`, else every 5 min unless it said `poll: false`, i.e. quiet
hours); `HouseholdServer` is THIS Python server in a Container
(`hosted/Dockerfile`, `sleepAfter` 30 s). There is no TypeScript copy of any
rule: hosted mode (`featherframe/hosted.py`, on when `FEATHERFRAME_HOSTED_URL`
+ `_KEY` are set) pulls the data dir from the front door's `/_internal/` API
before the database opens, and after every tick and every POST pushes what
changed (the DB as a snapshot), applies queued check-ins through
`app.parse_checkin` → `service.apply_checkin`, and reports
`service.hosted_state()`. A wake is `POST /api/hosted/run` (one tick, answered
when done). Plates come from `plates.featherframe.app` (the W-842 library).
The page is behind the household's password until accounts (W-845).
Provision: `POST https://<name>.featherframe.app/_admin/provision` with the
admin token (keychain `featherframe-hosted-admin-token`) and
`{password, tz}`. Deploy: `cd hosted && npx wrangler deploy` (Docker running;
retry on a registry push drop). New households need a DNS record: a custom
domain in `wrangler.jsonc` routes, until a proxied `*` record exists.

**Non-obvious invariants — do not break one side of these without the other:**

- **Never a wrong bird.** No match / no-plate species → provider returns `None` →
  `compose.render_fallback` (typographic plate). Composite plates are shown
  *whole*, never cropped. The fuzzy name resolver in `names.py` is **build-time
  only** (used by `fetch_plates`); live matching uses the curated index exactly,
  because token overlap mismatches (e.g. "European Starling" → a Blackbird plate).
- **The crosswalk lives in `scripts/species.yaml`** (modern species → verified
  Havell plate number; Audubon's titles are archaic — Cardinal = "Cardinal
  Grosbeak"). `fetch_plates.py` turns it into `plates/index.json` (+ downloaded
  images, gitignored). `test_crosswalk.py` guards the tricky numbers.
- **Framebuffer format (FFF) is a contract with the firmware.** 16-byte header +
  packed pixels: 4bpp = 2px/byte, **high nibble = left pixel, 0=black 15=white**
  (identical to Seeed's sprite). The server emits **native landscape 1872×1404**
  because the panel's `setRotation()` is a no-op — rotation happens in
  `pipeline._finish` via `config.panel_rotation` (90 or 270 only — the
  firmware's `displayFrame()` rejects anything that isn't native 1872×1404),
  while the PNG preview stays upright portrait. `framebuffer.py` and
  `firmware/src/main.cpp displayFrame()` must agree.
- **The colour panel reuses the gray layout.** Both panels are 3:4, so the art
  is always composed on the theme's 1404×1872 sheet and `pipeline._finish`
  scales it to the panel. For a colour panel `compose` still makes every layout
  decision on the gray art and keeps the type on the gray field; the art's
  colour twin (`Artwork.color_pair`, loaded lazily) goes on a separate RGB
  layer and `compose.merge_color` lays the type over it. `render/spectra.py`
  then dithers to the six *measured* inks (convex mix per colour from a cached
  64³ table, walked against the blue-noise mask): image white is exactly the
  white ink and a neutral gray is only black + white, so paper never speckles
  and type never turns to confetti. The wire format is FFF with `FLAG_INKS`:
  4bpp nibbles are Seeed_GFX's colour-sprite codes (0x0 white, 0xF black —
  the reverse of the gray levels), native portrait 1200×1600, rotation 0/180.
- **Config** is one flat `Config` dataclass (`config.py`), persisted as a JSON
  blob in our own SQLite (`db.py`, a kv store, separate from BirdNET's DB).
- **What an owner never tuned is a constant, not a setting (W-821).** The
  poll interval, confidence floor (BirdNET-Go's own threshold always wins;
  the floor is for sources with none), dwell, both alarm thresholds and the
  corroboration numbers live as named constants at the top of `service.py`;
  saturation is `spectra.SATURATION`. Single mode always shows the latest
  qualifying detection. Think twice before turning one back into a field.
- **Dithering** is the panel's own (`Panel.dither`, `panels.py`); the only
  override is `pipeline.DITHER_OVERRIDE` (`preview.py --dither`, and tests
  that want a cheap render). Gray: blue-noise (vectorized, Pi-friendly); Stucki there is a
  correct but slow per-pixel Python loop — don't make it the gray default on a
  Pi Zero. Colour: Stucki (`spectra._diffuse_stucki`), chosen side by side on
  the glass (19 Sep 2026) — with six inks, diffusion holds engraving lines and
  grains much tighter than the ordered mix, and a 30 s panel can afford the
  loop. It diffuses the gamut-mapped image and picks each pixel's ink only
  from that pixel's own ink set (its table decomposition): a free choice of
  all six turns neutral gray into green/blue/red dots.
- Keep the render single-threaded and memory-frugal (target: Pi Zero 2W, 512MB).
  Plates load downscaled; `Image.MAX_IMAGE_PIXELS` is lifted for the big scans.
- Paths are env-overridable: `FEATHERFRAME_DATA_DIR`, `FEATHERFRAME_PLATES_DIR`,
  `FEATHERFRAME_DB`, `FEATHERFRAME_PORT` (see `paths.py`). `install.sh` sets these
  in the systemd unit.
- **A dev server never advertises mDNS (W-827).** Any server you start on the
  owner's LAN that is not the box — `.claude/launch.json` entries, a bench
  instance, a one-off `python -m featherframe` — runs with
  `FEATHERFRAME_NO_MDNS=1` (`discovery.disabled_by`; `FEATHERFRAME_MDNS=0` is
  the same switch; `/api/status` then says `mdns.advertised: false`, error
  `disabled (…)`). The firmware rediscovers on its first failed fetch and
  adopts any server advertising its panel: on 20 Sep 2026 the wall frame
  latched onto a Mac preview server while the box rebooted. `launch.json` is
  untracked, so put the variable in each time you write one. The test suite
  turns advertising off for itself (`conftest._no_mdns`). `make serve`
  advertises, because it is also how an owner runs the server; a frame on the
  bench that should find a dev server gets its URL typed into the portal.

**The wordmark is the plate title, everywhere.** The dashboard serves the
bundled script at `/fonts/script.ttf`, the favicon is its F
(`server/scripts/make_favicon.py`), the baked boot screens draw it through
`typography.draw_script` (`firmware/tools/screens/bake_screens.py`; the
limb and wren above it come from `boot_art.py`, three draws through the
same `OpenAIImageModel` + Havell-plate references as the AI plates, laid out as
a plate and cut so the setting stays pixel-identical across the four boot
screens; the draws are colour, so every cut has a `_color` twin for a colour
panel's screens, and the setting with no bird on it is the server's
fallback-plate art, `featherframe/art/bough.png` + `bough_color.png`), and the
captive portal embeds a WOFF subset generated into the committed
`firmware/src/ff_portal_font.h` by `firmware/tools/portal_font.py` — run the
favicon, bake, and portal tools after any change to the script face or its
theme sizes.

**Firmware (`firmware/src/main.cpp`).** Deep-sleep model: all logic in `setup()`,
`loop()` empty. Wake → Wi-Fi (WiFiManager captive portal on first boot / held
button) → `GET /api/frame` with stored ETag → 304 sleeps, else `pushImage` +
`update()` (full refresh) → deep sleep (timer + button ext1 wake). Uses Seeed's
`Seeed_GFX` via `TFT_eSPI.h`; `begin(1)` is the fast re-init after a sleep wake.
Panel/board selection is `lib/driver/driver.h` (combo 511). Battery voltage is
`analogReadMilliVolts(A0) * 2 * VBAT_TRIM` (10k/10k divider; raw counts flatten
near a full cell, see W-693; the trim defaults to 1.0 so one binary ships to
every unit, and `FF_VBAT_TRIM=…` in the environment trims a bench build, W-768) and the button pins are known (KEY0=2, KEY1=3, KEY2=5,
active-low); the remaining on-hardware unknown is whether ext1 button wake
works from deep sleep at all — the keys read only while the panel's T-CON is
awake (see `PIN_PANEL_PWR` in `include/ff_config.h`). The power model is a
runtime setting (W-736): the server sends `X-Power-Mode` (awake|sleep) and
`X-Wake-Minutes` on every `/api/frame` response, the firmware stores both in
NVS, and `setup()` branches on `g_alwaysAwake`; a switch takes effect at the
end of the cycle that learned it (awake→sleep from `loop()`, sleep→awake by a
restart). `FF_DEFAULT_ALWAYS_AWAKE` is only the mode of a unit with no stored
value; `X-Poll-Seconds` sets the awake poll gap the same way (3 s default; the
page offers a minute to a day, and firmware from before 22 Sep 2026 ignores
anything over `FF_POLL_MAX_S` = 60 s until it is updated). `X-FF-Rotation`
(the page's panel rotation) rides along too and is kept in NVS (dark mode is
gone, W-821: the server still says `X-FF-Invert: 0` so fielded firmware
clears the flag it stored, until every frame runs firmware without it):
the baked art is baked at one rotation (`FF_BAKED_ROTATION`), so when the
frame hangs the other way up the firmware turns every baked screen and tile
180° (`rotate180`, and `flipX`/`flipY` for a tile's window).
The wall runs always-awake today; deep sleep is the
less-tested branch. The EE02 build (`-e ee02`, `FF_PANEL_SPECTRA6`) is the same
app with a full-refresh equivalent for everything partial (W-817): the plate
is retained in PSRAM, and a toast or the corner mark is a baked black/white-ink
tile blitted into a copy of it, then one ~30 s repaint (`paintPlate`; cleared
by painting the plate again, 60 s later for a toast). A press fetches first
and paints one thing, so only the outcome pills exist (the in-progress ones
have no tile); out of deep sleep there is no retained plate, so "Up to date"
refetches the plate with the pill armed and the corner mark becomes the full
error screen. Boot is one baked "Connecting" screen (every boot stage maps to
`FF_SCR_BOOT_WIFI`, painted while Wi-Fi joins underneath) that gives way to a
specific error screen only on failure; no loading sweep. Baked screens and
tiles live in `ff_screens_ee02.h`, from the same bake (the screens are the
colour art under black/white type, dithered as the server dithers a plate:
`bake_screens.on_color_art`; the stamp tiles stay black/white ink), and a 180 s floor sits
between resident repaints. One server serves as many frames as are added to it, and it knows them apart
(`service.admit_frame`): each frame names itself with `X-Device-Id` (its MAC).
Every kit gets a 403 and waits as "pending" — the first one on a fresh install
too — until the owner answers at the top of the Frames card: add it, or ignore
it (ignored frames fold at the bottom of that card, where they can be added or
forgotten; a frame removed from a row asks again the next time it checks in). Each frame is drawn for the panel it reports in its own `X-Panel`, so
there is no notice to answer when a different kit connects. Firmware without
the header is one frame called "legacy", which becomes its real ID in place
after an update. A parked frame
shows "Add this frame on the Featherframe page" (gray: error pill 3; EE02:
`FF_SCR_PENDING`) and keeps asking. Discovery prefers a server whose mDNS TXT
`panel` matches and otherwise takes any, and `X-Board` on the OTA request
keeps one board's image off the other. A
kit row's Advanced has "Reset to this panel's defaults" (a client-side fill
from that frame's own `Config.defaults_for(panel)`, applied only on Save). Low battery (`FF_LOW_BATT_V`: gray < 3.45 V, EE02 < 3.55 V) skips Wi-Fi and
sleeps 4 h at a time, saying "Battery low, charge me" on the glass once at
the crossing (`markLowBattery`: gray paints the baked `FF_TOAST_LOW_BATTERY`
pill over the plate, the EE02 the baked `FF_SCR_LOW_BATT` full screen, which
is why its hold starts 0.1 V earlier — a 30 s refresh needs the headroom;
"once" lives in NVS `lowmark`, written before the paint so a brownout can't
loop it; the always-awake loop enters the same hold after `FF_LOW_BATT_POLLS`
low polls, W-736), and the page puts a red *Battery low* badge on that frame's
own row at its `card.battery_critical` (≤ 10 % or ≤ the panel's hold,
`Panel.low_battery_volts`, which a test keeps equal to `FF_LOW_BATT_V`); OTA is refused under 3.70 V and a bad image rolls back.
