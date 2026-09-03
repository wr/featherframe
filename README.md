<h1 align="center">Featherframe</h1>

<p align="center">
  <strong>A wall-mounted e-paper frame that shows the birds in your backyard as Audubon lithograph plates.</strong>
</p>

<p align="center">
  <a href="#what-is-it">What is it?</a> ⬪
  <a href="#shopping-list">Shopping list</a> ⬪
  <a href="#install">Install</a> ⬪
  <a href="#configure-it">Configure</a> ⬪
  <a href="#species--plates">Species & plates</a> ⬪
  <a href="#license">License</a>
</p>

<p align="center"><em>Northern Cardinal · Cardinalis cardinalis · rendered to the panel's 16 grays</em></p>

---

## What is it?

Your BirdNET-Pi hears a bird; a few minutes later it's hanging on the wall as a
museum plate — cropped from its Audubon lithograph, captioned in EB Garamond,
dithered to the panel's 16 grays. Then the frame goes back to sleep. Set it and
forget it.

- **`server/`** — a small Python (FastAPI) service that runs *on the BirdNET-Pi
  itself*. It reads detections, renders plates, and serves a packed framebuffer
  plus a LAN config page.
- **`firmware/`** — a deliberately dumb ESP32-S3 client. It wakes, asks the
  server for a frame, pushes it to the panel, and goes back to sleep.

```
 BirdNET-Pi  ──reads──▶  Featherframe server  ──HTTP /api/frame──▶  ESP32-S3 ──▶ 10.3" e-paper
 (birds.db, read-only)   (FastAPI, systemd)     (packed framebuffer)  (deep sleep)
```

The server polls BirdNET's database read-only and renders once per qualifying
detection. The frame wakes on a timer or a button and either sleeps (`304`) or
paints.

## Shopping list

- **Seeed XIAO ePaper DIY Kit EE03** — XIAO ESP32-S3 Plus, EE03 driver board,
  and a 10.3" 1404 × 1872 16-gray panel (E-Ink ED103TC2, IT8951). We replace
  the SenseCraft firmware it ships with.
- A protected 1S LiPo with a JST-PH lead — or just run it on USB-C.
- A frame and mat. Matting it like a print sells the effect.
- A BirdNET-Pi you already have running.

Seat the XIAO on the driver board, latch the panel's flat cable, plug in the
battery, and mount portrait with the buttons reachable: **KEY0** fetches now,
**KEY1** shows today's collage, **KEY2** shows status (hold 3 s to redo Wi-Fi).

## Install

### 1. Server (on the BirdNET-Pi)

```bash
git clone https://github.com/wr/featherframe ~/featherframe
cd ~/featherframe/server
./install.sh
```

That creates a venv, downloads the Audubon plates (~220 MB), and installs a
`featherframe.service` systemd unit, niced to stay out of BirdNET's way. It
prints the config page URL when done: `http://<your-pi>.local:8080/`.

Options: `--skip-plates`, `--all-plates` (the whole Havell edition, ~2.9 GB),
`--port 9000`, `--no-service`.

> **No auth.** It's LAN-only with no login. Keep it on your home network.

### 2. Firmware

With [PlatformIO](https://platformio.org/) installed:

```bash
cd firmware
pio run -t upload        # build + flash over USB-C
pio device monitor       # serial log, 115200
```

Battery calibration and button pins live in `firmware/include/ff_config.h`.
`FF_NO_SLEEP 1` keeps the frame awake and polling every 15 s (fine on USB);
`0` is the deep-sleep battery build.

### 3. First boot

The frame starts a hotspot named **`Featherframe-Setup`**. Join it from your
phone, pick your Wi-Fi, and enter the server URL from step 1 (include `http://`
and the port). To redo it later, hold **KEY2 for 3 s**; hold KEY2 while
powering on to wipe everything.

If the image hangs sideways, change **Panel rotation** on the config page — no
reflash.

## Configure it

The page at `http://<your-pi>:8080/` is the whole UI:

- **Live preview** of the current frame, plus a **Test detection** button that
  injects a fake Cardinal so you can exercise everything with no birds.
- **Mode** — *single* (latest detection) or *collage* (the day's top species),
  plus an optional overnight "day in review" sheet.
- **Confidence threshold** (0.7), **wake interval** (15 min), **quiet hours**
  (22:00–06:00), and an optional debounce between repaints for a calmer frame.
- **Species blocklist** — one name per line. Ban the house sparrows if you like.
- **Detection source** — BirdNET-Pi DB (default), BirdNET-Go, BirdWeather, or
  an Apprise webhook, with a *Test connection* button.
- **Frame card** — last check-in, battery, Wi-Fi signal, overdue warning.

## Battery life

E-paper holds its image with zero power; the cost is per wake, mostly Wi-Fi.
Rough model for a 2000 mAh cell and ~20 refreshes a day:

| Wake interval | Runtime    |
|--------------:|-----------:|
| 15 min        | ~7–8 weeks |
| 30 min        | ~11 weeks  |
| 60 min        | ~14 weeks  |

Quiet hours push these further; the always-awake build lasts 4–5 days. Below
3.45 V the frame stops using Wi-Fi until it's charged.

## Species & plates

`server/scripts/species.yaml` maps modern species to Audubon Havell plate
numbers — every species he painted, each checked against the plate's own
archaic title (the Northern Cardinal is his "Cardinal Grosbeak"). The rule is
**never a wrong bird**: anything unsure falls back rather than guesses.

Birds Audubon never painted — the European Starling, the House Sparrow, your
yard's bats and cicadas — are pinned `plate: none` and get a clean typographic
plate instead: the name set large, "First recorded <date>" beneath.

### AI plates

Optionally, the server can paint those missing species in the Havell style.
Add an OpenAI API key on the config page and it prompts `gpt-image-2` with real
plates from your set as style references. One image per species (~$0.17),
cached forever, cropped like a real scan; regenerate or remove from the
gallery. No key just means the typographic fallback.

## Preview without hardware

```bash
make venv && make plates
make preview     # renders a fake Northern Cardinal to test_output/
```

## Troubleshooting

- **"BirdNET: not found"** — check the DB path on the config page. Default is
  `~/BirdNET-Pi/scripts/birds.db`.
- **Frame never updates** — quiet hours, the debounce window, or "same species
  already showing" are all intentional. Hit *Test detection* to force one.
- **Device never checks in** — check the serial monitor; the server URL needs
  a scheme and port. Hold KEY2 for 3 s to redo setup.
- **Plates missing** — re-run `python scripts/fetch_plates.py`; it's idempotent.

## Credits

- Plates: John James Audubon, *The Birds of America* — public domain, via
  [nathanbuchar/audubon-bird-plates](https://github.com/nathanbuchar/audubon-bird-plates).
  *Courtesy of the John James Audubon Center at Mill Grove, Montgomery County
  Audubon Collection, and Zebra Publishing.*
- [EB Garamond](https://github.com/octaviopardo/EBGaramond12) (SIL OFL) ·
  [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi) (Nachtzuster fork) ·
  [Seeed_GFX](https://github.com/Seeed-Studio/Seeed_GFX)

## Donate

While Featherframe is free and open source, donations are deeply appreciated,
and make ongoing development and support possible.
[Donate now](https://www.buymeacoffee.com/wellsworkshop)

## License

Featherframe's own code: do what you like with it.
