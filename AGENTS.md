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
page's Panel select is "As reported by the frame" (`config.panel_follow`); a
named panel is an override `adopt_panel` leaves alone until the owner switches
frames. The firmware side of a port is `-DFF_GENERIC_PANEL` (W-819, the
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
./.venv/bin/python -m featherframe.preview --gray 1 --dither stucki  # exercise modes
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

**`service.py` is the hub.** `FeatherframeService` holds the *single current
frame* (bytes + ETag), persisted to `data/frames/current.fff` so a restart never
blanks the device. A background thread runs `tick()` on the poll interval;
`tick()` is the whole decision tree — quiet hours (+ optional day-in-review
sheet), mode (single/collage; "auto" was removed and migrates to single),
confidence, debounce and same-species skip (only when `single_show_latest` is
False — the default is True), blocklist — and renders *at most one* frame per
decision. Every web handler just reads the current frame. The default
path is to do nothing (priority: few panel refreshes).

**Ingest (`birdnet.py`) is strictly read-only.** Opens `?mode=ro`, never writes
or locks BirdNET's DB. The cursor is `WHERE rowid > :last`. Every method
soft-fails to a safe default (None/[]/0) so a missing or odd DB keeps the current
frame instead of crashing. Fixture schema in `tests/_fixtures.py` is verbatim
from the Nachtzuster fork.

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
(generated PNGs go through the same `plate.extract` as real scans): the
heaviest ink band, extended through faint contiguous ink (hanging straw) up
to a real paper gap, then mirrored about the plate centre so Audubon's own
placement survives. The art is full-bleed to the mat opening (W-707):
`compose.py` cover-fits a plate whose edges are inked (Snowy Owl) only if
that crops ≤ 25 % of it, else contain-fits it centred; the date and № marks
share one footer baseline with the gone-quiet note;
`typography.py` sets the caption (W-708): a copperplate script title
(Pinyon Script, OFL, bundled in `featherframe/fonts/`; Garamond italic is the
stand-in if it is ever absent), the engraved
Latin name, and the plate's own legend lines from
`scripts/legends.yaml` (Audubon's printed figure key and plant, transcribed
per Havell plate; `featherframe/legends.py` reduces a composite sheet to the
detected species' line);
the date · time and "No. NN" sit in the bottom
corners in the same script. `theme.py` holds all geometry/tone constants.

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
- **Dithering:** `config.dither` defaults to `"auto"`, the panel's own default
  (`panels.py`). Gray: blue-noise (vectorized, Pi-friendly); Stucki there is a
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

**The wordmark is the plate title, everywhere.** The dashboard serves the
bundled script at `/fonts/script.ttf`, the favicon is its F
(`server/scripts/make_favicon.py`), the baked boot screens draw it through
`typography.draw_script` (`firmware/tools/screens/bake_screens.py`; the
limb and wren above it come from `boot_art.py`, three draws through the
same `OpenAIImageModel` + Havell-plate references as the AI plates, laid out as
a plate and cut so the setting stays pixel-identical across the four boot
screens), and the
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
value; `X-Poll-Seconds` sets the awake poll gap the same way (3 s default). `X-FF-Rotation`
(the page's panel rotation) rides along too and is kept in NVS like dark mode:
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
tiles live in `ff_screens_ee02.h`, from the same bake, and a 180 s floor sits
between resident repaints. One server serves one frame, and it knows its frames apart
(`service.admit_frame`): each frame names itself with `X-Device-Id` (its MAC),
the first to check in becomes the active frame, and any other gets a 403 and
waits as "pending" until the owner answers on the page — switch to it, or
ignore it (ignored frames are listed on the Frame card; the frame switched
away from is asked about again when it next checks in). Only the active frame
is served, moves the device card, or decides the panel (`adopt_panel`, from
its `X-Panel`); a switch to a frame with another panel raises the "New panel
connected" notice offering that panel's defaults (`panels.PANEL_SETTINGS`;
nothing is reset unasked). Firmware without the header is one frame called
"legacy", which becomes its real ID in place after an update. A parked frame
shows "Add this frame on the Featherframe page" (gray: error pill 3; EE02:
`FF_SCR_PENDING`) and keeps asking. Discovery prefers a server whose mDNS TXT
`panel` matches and otherwise takes any, and `X-Board` on the OTA request
keeps one board's image off the other. The
Display section's Advanced has "Reset to defaults" (client-side fill from
`Config.defaults_for(panel)`, applied only on Save). Low battery (`FF_LOW_BATT_V`: gray < 3.45 V, EE02 < 3.55 V) skips Wi-Fi and
sleeps 4 h at a time, saying "Battery low, charge me" on the glass once at
the crossing (`markLowBattery`: gray paints the baked `FF_TOAST_LOW_BATTERY`
pill over the plate, the EE02 the baked `FF_SCR_LOW_BATT` full screen, which
is why its hold starts 0.1 V earlier — a 30 s refresh needs the headroom;
"once" lives in NVS `lowmark`, written before the paint so a brownout can't
loop it; the always-awake loop enters the same hold after `FF_LOW_BATT_POLLS`
low polls, W-736), and the page shows a red banner at
`frame_card.battery_critical` (≤ 10 % or ≤ the panel's hold,
`Panel.low_battery_volts`, which a test keeps equal to `FF_LOW_BATT_V`); OTA is refused under 3.70 V and a bad image rolls back.
