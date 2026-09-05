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

Hardware is fixed: Seeed XIAO ePaper Kit EE03 (XIAO ESP32-S3 Plus + 10.3"
ED103TC2, 1404×1872, 16-level gray, IT8951). See `README.md` for the full spec,
wiring, and battery numbers.

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
(`OpenAIImageModel` first, plain `requests`, default `gpt-image-2` via
`/v1/images/edits` with real plates as style references); generated plates are
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
- **Config** is one flat `Config` dataclass (`config.py`), persisted as a JSON
  blob in our own SQLite (`db.py`, a kv store, separate from BirdNET's DB).
- **Dithering:** blue-noise is the default (vectorized, Pi-friendly); Stucki is a
  correct but slow per-pixel Python loop — don't make it the default on a Pi Zero.
- Keep the render single-threaded and memory-frugal (target: Pi Zero 2W, 512MB).
  Plates load downscaled; `Image.MAX_IMAGE_PIXELS` is lifted for the big scans.
- Paths are env-overridable: `FEATHERFRAME_DATA_DIR`, `FEATHERFRAME_PLATES_DIR`,
  `FEATHERFRAME_DB`, `FEATHERFRAME_PORT` (see `paths.py`). `install.sh` sets these
  in the systemd unit.

**The wordmark is the plate title, everywhere.** The dashboard serves the
bundled script at `/fonts/script.ttf`, the favicon is its F
(`server/scripts/make_favicon.py`), the baked boot screens draw it through
`typography.draw_script` (`firmware/tools/screens/bake_screens.py`; the
bough and wren under it come from `boot_art.py`, three draws through the
same `OpenAIImageModel` + Havell-plate references as the AI plates, cut so the
bough stays pixel-identical across the four boot screens), and the
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
near a full cell, see W-693) and the button pins are known (KEY0=2, KEY1=3, KEY2=5,
active-low); the remaining on-hardware unknown is whether ext1 button wake
works from deep sleep at all — the keys read only while the panel's T-CON is
awake (see `PIN_PANEL_PWR` in `include/ff_config.h`). `FF_NO_SLEEP 1` (always
awake, 15 s polling) is what is flashed today; `0` is the battery model and
the less-tested branch of `setup()`. Low battery (< 3.45 V) skips Wi-Fi and
sleeps 4 h at a time; OTA is refused under 3.70 V and a bad image rolls back.
