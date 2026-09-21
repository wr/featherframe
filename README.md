**NOTE: this project is still a work in progress :)**

<h1 align="center">Featherframe</h1>

<p align="center">
  <strong>A wall-mounted e-paper frame that shows the birds in your backyard as Audubon lithograph plates.</strong>
</p>

<p align="center">
  <a href="https://github.com/wr/featherframe/actions/workflows/ci.yml"><img src="https://github.com/wr/featherframe/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
</p>

<p align="center">
  <a href="#what-is-it">What is it?</a> ⬪
  <a href="#shopping-list">Shopping list</a> ⬪
  <a href="#install">Install</a> ⬪
  <a href="#configure-it">Configure</a> ⬪
  <a href="#species--plates">Species & plates</a> ⬪
  <a href="#license">License</a>
</p>

<center><img width="600" alt="featherframe" src="https://github.com/user-attachments/assets/22e61eee-6bd7-49b2-96bb-d9fbfed88f1a" /></center>

---

## What is it?

BirdNET is a free local AI model that identifies bird calls in your backyard. Featherframe is an eink display that pulls live bird detection data from BirdNET and shows it as a beautiful [Audubon lithograph print](https://www.audubon.org/art/birds-of-america)... in color or grayscale.

Featherframe also has a custom `gpt-image` prompt that can automatically generate high-quality (read: not AI slop) lithograph plates *in the Audubon style* for birds not found in the original 435 prints from the 1800's. It can also generate a "day in review" that shows all of the creatures your BirdNET setup detected that day.

<center><img width="600" alt="IMG_1899" src="https://github.com/user-attachments/assets/95d46050-47f6-4af5-8e1a-6dfe1475b7b2" /></center>


- **`server/`** — a small Python (FastAPI) service that runs *on the BirdNET-Pi
  itself*, or anywhere on the LAN beside BirdNET-Go. It reads detections,
  renders plates, and serves a packed framebuffer plus a LAN config page.
- **`firmware/`** — a deliberately dumb ESP32-S3 client. It wakes, asks the
  server for a frame, pushes it to the panel, and goes back to sleep.

```
 BirdNET-Pi  ──reads──▶  Featherframe server  ──HTTP /api/frame──▶  ESP32-S3 ──▶ 10.3" e-paper
 (birds.db, read-only)   (FastAPI, systemd)     (packed framebuffer)  (deep sleep)
 or BirdNET-Go (its API)
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
- A BirdNET-Pi or BirdNET-Go you already have running.

Seat the XIAO on the driver board, latch the panel's flat cable, plug in the
battery, and mount portrait with the buttons reachable: **KEY0** fetches now,
**KEY1** shows today's collage, **KEY2** shows status (hold 3 s to redo Wi-Fi).

## Install

### 1. Server

**Which BirdNET do you have?**

- **BirdNET-Pi.** Install on the Pi itself; the server reads BirdNET-Pi's
  `birds.db` read-only.

  ```bash
  git clone https://github.com/wr/featherframe ~/featherframe
  cd ~/featherframe/server
  ./install.sh
  ```

- **BirdNET-Go.** The server talks to BirdNET-Go's API, so it can run on any
  Linux machine on your network: the same box, a NAS, a container.

  ```bash
  git clone https://github.com/wr/featherframe ~/featherframe
  cd ~/featherframe/server
  ./install.sh --source birdnet-go --url http://<birdnet-go-host>:8080
  ```

BirdWeather stations and Apprise notifications are also sources; pick those on
the config page afterwards. The page's source card shows which source is live
and when it last produced a detection
([troubleshooting](docs/troubleshooting-sources.md)).

That creates a venv, downloads the Audubon plates (~2.9 GB, as checksummed
tarballs from this repo's `plates-v1` release, with the public mirror as the
fallback), and installs a
`featherframe.service` systemd unit, niced to stay out of BirdNET's way. It
prints the config page URL when done: `http://<hostname>.local:8080/`.

Options: `--skip-plates`, `--all-plates` (every Havell plate, not only the
ones `species.yaml` uses),
`--port 9000`, `--no-service`, `--check` (report what a run would change),
`--source birdnet-pi|birdnet-go` with `--url` (written once, only when given).

**Upgrading** is the same command again: `git pull && ./install.sh`. It reuses
the venv, fetches only plates a new species needs, keeps the port and data
directory of the existing install, and restarts the service. Your config,
frames, and generated plates in `data/` are never touched.

**Before you reinstall or move the server**, open the page's *Generated plates
on file* card and **Download a backup**: a zip of every generated plate. Each
one cost an image, and they live only in `data/generated/`. **Restore from a
backup…** on the new install puts them back, and never replaces a newer plate.

> **No auth.** It's LAN-only with no login. Keep it on your home network.

### 2. Firmware

With [PlatformIO](https://platformio.org/) installed:

```bash
cd firmware
pio run -t upload        # build + flash over USB-C
pio device monitor       # serial log, 115200
pio run -e release       # the binary a kit ships with (release_ee02 for the colour kit)
```

Nothing per-unit or per-network is compiled in: the frame finds the server by
mDNS, and the battery read is untrimmed (within about 2 %; see `VBAT_TRIM` in
`firmware/include/ff_config.h`, where the button pins also live).
One build serves both power models: the config page's **Power** setting
(always awake on USB, deep sleep on battery) and the wake interval reach the
frame on its next check-in and are stored on the device.

#### Another panel

The server draws for whatever panel a frame describes (its size, format and
the rotations it accepts ride along on every check-in), so a port is a
firmware job only. Copy the `generic_bench` env in `firmware/platformio.ini`,
and in the copy:

1. Set the build flags for your glass: `PANEL_W` / `PANEL_H` as it hangs,
   `FF_PANEL_ID`, `FF_BOARD_ID`, `FF_PANEL_ROTATIONS`, `FF_BAKED_ROTATION`
   (they are listed in `firmware/include/ff_config.h`, under Panel).
2. Set `custom_bake` to your size, and `FF_SCREENS_HEADER` to its `--out`: the
   boot, setup and error screens are baked for your panel when you build.
3. Pick your panel's Seeed_GFX setup in `firmware/lib/driver/driver.h`. With
   another driver library, `fullPaint()` in `firmware/src/main.cpp` is the one
   call to replace.

Every change on the glass is a full refresh on this path, as on the colour
kit. The panel must take 16-level gray (or Spectra 6 inks, with
`-DFF_GENERIC_INKS`), and a panel that is not 3:4 shows the same plate,
centred on paper. To see what the server will send before any hardware exists:

```bash
cd server && ./.venv/bin/python -m featherframe.preview --panel custom:800x480:gray16:90,270
```

### 3. First boot

The frame starts a hotspot named **`Featherframe-Setup`**. Join it from your
phone and pick your Wi-Fi. Leave the server URL blank: the frame finds the
server by mDNS (the server advertises `_featherframe._tcp`), and finds it
again on its own if the box ever changes address. Type the URL from step 1
(include `http://` and the port) only on a network that blocks multicast.
To redo it later, hold **KEY2 for 3 s**; hold KEY2 while powering on to wipe
everything.

If the image hangs sideways, change **Panel rotation** on the config page — no
reflash.

## Configure it

The page at `http://<your-pi>:8080/` is the whole UI:

- **Live preview** of the current frame, plus a **Test detection** button that
  injects a fake Cardinal so you can exercise everything with no birds.
- **Mode** — *single* (latest detection) or *collage* (the day's top species),
  plus an optional overnight "day in review" sheet.
- **Quiet hours** (22:00–06:00, or sunset to sunrise). The confidence
  threshold is your detector's own: set it in BirdNET-Go, not here.
- **Power** — *always awake* (Wi-Fi up, asks for a new plate every few seconds,
  instant buttons; for USB) or *deep sleep* (wakes on the **wake interval**,
  15 min by default, or a button; for battery). *Check every* sets the awake
  poll (3 s by default). The frame picks up a change on its next check-in.
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

Quiet hours push these further; the always-awake model lasts 4–5 days. Below
3.45 V (3.55 V on the colour panel, whose warning is a full 30 s refresh) the
frame says "Battery low, charge me" on the glass, once, and stops using Wi-Fi
until it's charged; the page warns first, with a red banner
under 10 %.

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
Add an OpenAI API key on the config page and it prompts `gpt-image-2.5-sunburst`
(or `-flare`, or the older `gpt-image-2`) with real
plates from your set as style references. One image per species (~$0.07),
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
- **Frame never updates** — quiet hours, a new species waiting for a second
  detection, or a first-of-the-day species holding the frame are all
  intentional. Hit *Test detection* to force one.
- **Device never checks in** — `/api/status` shows `mdns.advertised`; if it is
  false the box has no LAN route or `zeroconf` is missing (re-run
  `install.sh`). On a network that blocks multicast, hold KEY2 for 3 s and type
  the server URL (scheme and port) into the portal.
- **The frame shows another server's plate** — a second Featherframe server on
  the LAN (a dev copy on a laptop) advertises itself too, and a frame whose own
  server stops answering adopts it. Run every server that is not the real one
  with `FEATHERFRAME_NO_MDNS=1`; stop the stray and the frame goes home.
- **Plates missing** — re-run `python scripts/fetch_plates.py`; it's idempotent.

## Credits

- Plates: John James Audubon, *The Birds of America* — public domain, via
  [nathanbuchar/audubon-bird-plates](https://github.com/nathanbuchar/audubon-bird-plates).
  *Courtesy of the John James Audubon Center at Mill Grove, Montgomery County
  Audubon Collection, and Zebra Publishing.*
- Type, all SIL OFL and bundled with their licence texts:
  [Pinyon Script](https://fonts.google.com/specimen/Pinyon+Script),
  [IM Fell Double Pica SC](https://iginomarini.com/fell/) (Igino Marini's Fell Types),
  [EB Garamond](https://github.com/octaviopardo/EBGaramond12),
  [Inter](https://rsms.me/inter/) ·
  [BirdNET-Pi](https://github.com/Nachtzuster/BirdNET-Pi) (Nachtzuster fork) ·
  [Seeed_GFX](https://github.com/Seeed-Studio/Seeed_GFX)

## Donate

While Featherframe is free and open source, donations are deeply appreciated,
and make ongoing development and support possible.
[Donate now](https://www.buymeacoffee.com/wellsworkshop)

## License

Featherframe's own code is licensed under the [Apache License 2.0](LICENSE).
The bundled fonts are SIL OFL, Audubon's plates are in the public domain, and
the libraries keep their own terms: see [THIRD_PARTY.md](THIRD_PARTY.md).
