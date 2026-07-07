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
`tick()` is the whole decision tree — quiet hours, mode (single/collage/auto),
confidence, debounce, same-species skip, blocklist — and renders *at most one*
frame per decision. Every web handler just reads the current frame. The default
path is to do nothing (priority: few panel refreshes).

**Ingest (`birdnet.py`) is strictly read-only.** Opens `?mode=ro`, never writes
or locks BirdNET's DB. The cursor is `WHERE rowid > :last`. Every method
soft-fails to a safe default (None/[]/0) so a missing or odd DB keeps the current
frame instead of crashing. Fixture schema in `tests/_fixtures.py` is verbatim
from the Nachtzuster fork.

**Render pipeline (`render/`).** `pipeline.py` orchestrates:
`compose.render_single` (or `collage.render_collage`) → `finish.to_levels`
(dither) → `framebuffer.pack`. The provider seam is `provider.py`: `ArtProvider`
returns bird artwork or `None`; `AudubonProvider` is v1 (swap here for an
AI-gen provider later). `plate.py` does the content-aware crop; `typography.py`
does EB Garamond faux small caps and the caption block; `theme.py` holds all
geometry/tone constants.

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
  `pipeline._finish` via `config.panel_rotation`, while the PNG preview stays
  upright portrait. `framebuffer.py` and `firmware/src/main.cpp displayFrame()`
  must agree.
- **Config** is one flat `Config` dataclass (`config.py`), persisted as a JSON
  blob in our own SQLite (`db.py`, a kv store, separate from BirdNET's DB).
- **Dithering:** blue-noise is the default (vectorized, Pi-friendly); Stucki is a
  correct but slow per-pixel Python loop — don't make it the default on a Pi Zero.
- Keep the render single-threaded and memory-frugal (target: Pi Zero 2W, 512MB).
  Plates load downscaled; `Image.MAX_IMAGE_PIXELS` is lifted for the big scans.
- Paths are env-overridable: `FEATHERFRAME_DATA_DIR`, `FEATHERFRAME_PLATES_DIR`,
  `FEATHERFRAME_DB`, `FEATHERFRAME_PORT` (see `paths.py`). `install.sh` sets these
  in the systemd unit.

**Firmware (`firmware/src/main.cpp`).** Deep-sleep model: all logic in `setup()`,
`loop()` empty. Wake → Wi-Fi (WiFiManager captive portal on first boot / held
button) → `GET /api/frame` with stored ETag → 304 sleeps, else `pushImage` +
`update()` (full refresh) → deep sleep (timer + button ext1 wake). Uses Seeed's
`Seeed_GFX` via `TFT_eSPI.h`; `begin(1)` is the fast re-init after a sleep wake.
Panel/board selection is `lib/driver/driver.h` (combo 511). Three on-hardware
unknowns to confirm on bring-up: `panel_rotation` direction (set on the config
page, no reflash), `VBAT_SCALE` calibration, and the EE03 button/ADC pins (the
SHT40 may share I2C) — all noted in `include/ff_config.h`.
