# Featherframe

A wall-mounted e-paper frame that shows the birds heard in your backyard, drawn
as Audubon lithograph plates. It listens to your BirdNET-Pi, renders the most
recent detection as a museum plate, and otherwise disappears into the wall. Set
it and forget it.

<p align="center"><em>Northern Cardinal · Cardinalis cardinalis · rendered to the panel's 16 grays</em></p>

Two parts:

- **`server/`** — a small Python (FastAPI) service that runs *on the BirdNET-Pi
  itself*, next to BirdNET. It reads detections, renders plates, and serves a
  packed framebuffer plus a LAN config page. It does all the image work.
- **`firmware/`** — a deliberately dumb ESP32-S3 client. It wakes, asks the
  server for a frame, pushes it to the panel, and goes back to sleep.

---

## How it works

```
 BirdNET-Pi  ──reads──▶  Featherframe server  ──HTTP /api/frame──▶  ESP32-S3 ──▶ 10.3" e-paper
 (birds.db, read-only)   (FastAPI, systemd)     (packed framebuffer)  (deep sleep)
```

1. The server polls BirdNET's SQLite DB read-only, using a rowid cursor, every
   ~20 s. It never writes to or locks BirdNET's database.
2. A new detection above the confidence threshold triggers one render: crop the
   bird from its Audubon plate, lay it on an off-white field, set a museum
   caption in EB Garamond, and dither to the panel's 16 grays.
3. The frame gets a content hash (ETag). The device wakes on a timer (default
   15 min) or a button press, sends `GET /api/frame` with `If-None-Match`, and
   gets either `304 Not Modified` (sleep) or the new framebuffer (paint, then
   sleep).

Everything the frame needs is Wi-Fi + the server URL. Everything else is
configured on the server's web page.

---

## Hardware

- **Seeed XIAO ePaper DIY Kit EE03** — a XIAO ESP32-S3 Plus on the EE03 driver
  board, driving a **10.3" monochrome panel, 1404 × 1872, 16-level grayscale**
  (E-Ink ED103TC2, IT8951 controller).
- A 1S LiPo with a JST-PH connector for the battery build (or run it on USB-C).
- A frame/mat. The panel is portrait; matting it like a print sells the effect.

The kit ships pre-flashed with SenseCraft HMI — we replace that firmware.

### Assembly

1. Seat the XIAO ESP32-S3 Plus on the EE03 driver board (USB-C facing out).
2. Connect the panel's flat cable to the driver board's FPC connector (mind the
   contact orientation and latch).
3. Battery: plug the LiPo into the JST connector. Charging happens over the
   XIAO's USB-C.
4. Mount in the frame with the panel portrait. The three user buttons stay
   reachable — one triggers an immediate refresh; holding one during boot
   re-opens Wi-Fi setup (see below).

The firmware sets rotation so the image is portrait as it hangs; you don't wire
anything for orientation.

---

## Install the server (on the BirdNET-Pi)

BirdNET-Pi installs bare-metal, so Featherframe does too — no Docker.

```bash
git clone <this repo> ~/featherframe
cd ~/featherframe/server
./install.sh
```

`install.sh` creates a venv, installs deps, downloads the Audubon plates
(~220 MB, one time), and installs + starts a `featherframe.service` systemd
unit that runs as you (not root), niced and idle-IO so it stays out of BirdNET's
way. When it finishes it prints a URL:

```
http://<your-pi>.local:8080/
```

Open it. That's the whole setup on the Pi side.

Options: `./install.sh --skip-plates` (fetch plates later),
`--port 9000`, `--no-service` (venv only). If your BirdNET DB isn't at
`~/BirdNET-Pi/scripts/birds.db`, set the path on the config page.

> **No auth.** v1 is LAN-only with no login. Keep it on your home network.

---

## Flash the firmware

With [PlatformIO](https://platformio.org/) installed:

```bash
cd firmware
pio run -t upload        # build + flash over USB-C
pio device monitor       # watch the serial log (115200)
```

The panel/driver selection lives in `firmware/lib/driver/driver.h` (Seeed_GFX
combo 511 for the 10.3" ED103TC2). Battery calibration and button pins are in
`firmware/include/ff_config.h`.

> On-hardware note: the framebuffer format matches Seeed_GFX's sprite exactly
> (4bpp, high-nibble-left, 0=black; pushed with `pushImage` + `update`), so the
> only thing to check on first flash is orientation — the panel can't rotate
> itself, so the server sends it pre-rotated. If the image is sideways or
> upside-down as it hangs, change **Panel rotation** on the config page (no
> reflash). Everything upstream of the panel push is hardware-independent and
> already verified via `make preview`.

### First boot — Wi-Fi

On first boot (or after holding the reset button), the frame starts a Wi-Fi
hotspot named **`Featherframe-Setup`**. Join it from your phone, pick your
network, and enter the server URL from the install step
(`http://<your-pi>.local:8080`). Credentials and URL are saved to NVS; you only
do this once.

---

## Configure it

The page at `http://<your-pi>:8080/` is the whole UI:

- **Live preview** — the current frame, exactly as the panel shows it.
- **Mode** — *single* (most recent detection), *collage* (the day's top
  species in a grid), or *auto* (single by day, a "day in review" collage
  overnight).
- **Confidence threshold** (default 0.7), **refresh debounce** (default 15 min —
  the panel never repaints more often, and won't repaint for the same species
  it's already showing), **wake interval**, **quiet hours** (default 22:00–06:00,
  hold the image overnight).
- **Rendering** — 16-level grayscale or the 1-bit fallback; blue-noise dither
  (fast, Pi-friendly) or Stucki (slower, richer).
- **Species blocklist** — one name per line, common or scientific. Ban the house
  sparrows if you like.
- **Status** — last detection, last device check-in, battery voltage.
- **Test detection** — injects a fake Northern Cardinal so you can exercise the
  whole pipeline with no birds and no hardware.

Settings persist to a small SQLite DB in the data dir (separate from BirdNET's).

---

## Preview without hardware

The whole art pipeline runs on your laptop:

```bash
make venv
make plates              # downloads the Audubon plates
make preview             # renders a fake Northern Cardinal
make preview-all         # one PNG per curated species
make preview-collage     # a daily collage
make preview-fallback    # the typographic fallback plate
```

Output lands in `test_output/` as PNGs (exactly what the panel shows) plus the
packed `.fff` framebuffer. `make test` runs the unit tests.

---

## Battery life

E-paper holds its image with zero power, so between wakes the frame draws almost
nothing. The cost is per wake — mostly the Wi-Fi association, plus the panel
refresh when the image actually changes.

Rough model (2000 mAh 1S LiPo, ~1700 mAh usable; ~100 µA deep sleep; ~8 s of
Wi-Fi + HTTP per wake at ~90 mA; ~20 panel refreshes/day at ~0.5 mAh each):

| Wake interval | Wakes/day | Est. daily draw | Runtime on 2000 mAh |
|--------------:|----------:|----------------:|--------------------:|
| 15 min        | 96        | ~32 mAh         | ~7–8 weeks          |
| 30 min        | 48        | ~22 mAh         | ~11 weeks           |
| 60 min        | 24        | ~17 mAh         | ~14 weeks           |

Quiet hours (no wakes overnight) push these further. The biggest lever is the
wake interval — Wi-Fi assoc dominates each wake, so fewer wakes ≈ proportionally
longer life. These are estimates; the config page shows the real battery voltage
the firmware reports, so watch it on your unit and adjust.

Longest life: run *auto* mode with a 30–60 min interval and quiet hours on. Best
freshness: 15 min. On USB power, none of this matters — set 15 min and forget it.

---

## Species & plates

`server/scripts/species.yaml` is the editable crosswalk: modern species →
Audubon Havell plate. It ships with ~40 common Eastern US backyard birds, each
plate number verified by hand (Audubon's 1830s titles are archaic — the
Northern Cardinal is his "Cardinal Grosbeak", the junco his "Snow Bird").

- **Add a species**: add an entry. Omit `plate:` and `fetch_plates.py` will
  suggest one by title match for you to pin.
- **Composites**: some plates show several species (the chickadees share one).
  Those are flagged `composite: true` and shown whole rather than cropped — a
  crop might land on the wrong bird, and rule #2 is *never a wrong bird*.
- **No plate**: a couple of common birds postdate Audubon (European Starling
  introduced 1890, House Sparrow 1851). They're pinned `plate: none` and render
  a clean typographic plate — the name set large, "First recorded <date>"
  beneath. Any species with no match falls back the same way.

Matching keys on scientific name first (stable), then common name, with a few
old-binomial synonyms for taxonomic renames. If it's not confident, it falls
back rather than guess.

---

## Project layout

```
server/
  featherframe/            the package
    birdnet.py             read-only ingest + rowid cursor
    names.py               species -> plate matching (+ synonyms, fallback)
    config.py  db.py       settings + our own small SQLite
    service.py             the scheduler: poll, debounce, quiet hours, render
    app.py                 FastAPI: /api/frame (ETag), config page, status
    render/
      plate.py             plate load, content-aware crop, paper normalise
      typography.py        EB Garamond, faux small caps, caption block
      compose.py           single-detection composition + fallback plate
      collage.py           daily grid
      finish.py            contrast + blue-noise / Stucki dithering
      framebuffer.py       pack to the panel's native FFF wire format + ETag
      pipeline.py          orchestration (used by scheduler + `make preview`)
    fonts/                 EB Garamond (OFL)
  scripts/
    fetch_plates.py        one-time plate downloader
    species.yaml           the species -> plate crosswalk (editable)
  tests/                   cursor logic + name matching + framebuffer
  install.sh               venv + deps + systemd on the Pi
firmware/
  src/main.cpp             wake -> fetch -> paint -> sleep
  lib/driver/driver.h      Seeed_GFX panel selection (combo 511)
  include/ff_config.h      pins, battery calibration, defaults
  platformio.ini
```

---

## Troubleshooting

- **Config page shows "BirdNET: not found"** — check the DB path on the page.
  Default is `~/BirdNET-Pi/scripts/birds.db`; confirm with
  `sqlite3 ~/BirdNET-Pi/scripts/birds.db '.schema detections'`.
- **Frame never updates** — quiet hours, the debounce window, or "same species
  already showing" can all be intentional. Hit *Test detection* to force one.
- **Device never checks in** — confirm it joined Wi-Fi (serial monitor) and that
  the server URL it has matches the Pi. Hold a button during boot to redo setup.
- **Plates missing** — re-run `python scripts/fetch_plates.py`; the mirror is
  occasionally flaky and the script is idempotent.
- **Slow renders on a Pi Zero** — use blue-noise dither (the default), not
  Stucki.

---

## Credits & license

- Plate images: John James Audubon, *The Birds of America* — public domain,
  via the [nathanbuchar/audubon-bird-plates](https://github.com/nathanbuchar/audubon-bird-plates)
  mirror of audubon.org. Credit line: *Courtesy of the John James Audubon Center
  at Mill Grove, Montgomery County Audubon Collection, and Zebra Publishing.*
- Typeface: [EB Garamond](https://github.com/octaviopardo/EBGaramond12) by Georg
  Duffner / Octavio Pardo (SIL Open Font License).
- Detections: [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi)
  (Nachtzuster fork).
- Display library: [Seeed_GFX](https://github.com/Seeed-Studio/Seeed_GFX).

Featherframe's own code: do what you like with it.
