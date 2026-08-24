# Boot / first-run screens — handoff

State of the illustrated boot + onboarding panel screens on the EE03 (10.3" ED103TC2,
IT8951, 1404×1872 portrait / 1872×1404 native landscape). Branch
`wells/w-574-frame-buttons-check-now-collage-status-page`.

## Current status — working, flashed
Boot sequence on the panel:
`splash → "Connecting to Wi‑Fi…" (wren flies in) → "Connecting to BirdNET…" (empty house)
→ "Downloading image…" (wren in the hole) → server bird plate`.
- Splash + each connecting screen render; the birdhouse + wordmark hold steady and only
  the changed box (bird / wren / pill) repaints.
- The plate (e.g. American Goldfinch from `10.0.2.15:8090`) loads ~10–12 s after
  "Downloading…" (server render + transfer time).
- Onboarding (Wi‑Fi setup) flow: `setup steps → 3‑step checklist`, same darker birdhouse.

## How it's built
**Bake pipeline** — `bake_screens.py` (run with the server venv):
`server/.venv/bin/python firmware/tools/screens/bake_screens.py [--preview]`
- The 4 **boot** screens come from `boot_v2.svg` (the designer's file; text is vector
  paths, so the swash italic "Featherframe" wordmark + old‑style figures render exactly).
  rsvg-convert renders it; each 1404×1872 panel is cropped.
- To keep the birdhouse **byte‑identical across screens** (so partials stay tiny), the
  splash panel is the shared base; the bird / wren / pill are transferred onto it per
  screen. Bird & pill use an ink‑only transfer (`_paste_box`); the **wren‑in‑hole is a
  FULL COPY** of the hole box (its feathers are lighter than the empty hole, so ink‑only
  transfer turns it into a black blob — do not change this back).
- The 4 **onboarding** screens are composed in PIL (`screen_setup`, `screen_check`) on
  `art/house.png` (the darker v2 birdhouse).
- Gray output: fixed white‑point + gamma LUT `apply_curve()` (`WHITE_PT=246`,
  `GRAY_GAMMA=1.4`). MUST stay content‑independent — a mean‑based contrast maps the same
  birdhouse to different values per screen and makes the whole thing diff/flash.
- Output: 4bpp gray in native 1872×1404 (rot90 like the server), PackBits‑packed into
  `firmware/src/ff_screens.h` (~1.4 MB), + `contact_sheet.png` preview.

**Firmware** — `src/main.cpp`:
- `showScreen(idx)`: entry screens (SPLASH/SETUP) do a full `displayFrame` (gray). Every
  other screen diffs vs the previous baked body, bands the changed native COLUMNS, and
  does a **windowed IT8951 update** per box (`tconLoadImage` + `tconDisplayArea`). All
  windows use **GC16** (mode 2) — DU (1‑bit) renders the pencil line art too faint and
  ghosts on erase.
- Coordinates are **mirrored** (`mx = FF_NATIVE_W - nx - nw`): the panel flips X, which is
  invisible at full width but not for a window.
- The 3 screen buffers (`g_scrBuf/g_scrPrev/g_scrWin`, ~1.3 MB each) are file‑scope and
  **freed in `fetchAndRender()`** before the plate loads — see the critical gotcha below.

## Critical gotchas (do not relearn these)
1. **1‑bit path can't render the panel's full width** — the bottom quarter comes up black.
   Everything full‑panel goes through the 16‑gray path (`displayFrame` / bird plates). See
   memory `panel-1bit-cant-render-full-width`.
2. **Plate silently fails if PSRAM is fragmented.** `tconHostAreaPackedPixelWrite` mallocs
   a ~1.31 MB mirror buffer for the full write; if the largest free block is under that it
   `return`s without loading and the panel keeps the old screen. Keep boot buffers freed
   before any full plate load.
3. **Panel needs pioarduino Arduino‑ESP32 v3** to cold‑init, and a **real power‑on**
   (soft/RTS resets don't cold‑init). See memory `panel-needs-pioarduino-v3`.
4. **"Panel dead" is usually the USB cable** (power‑only / flaky) — no amber boot LED,
   flapping port. See memory `panel-dead-check-usb-cable`.
5. Native‑USB flashing: if the port vanishes, hold BOOT, tap RESET, release BOOT for a
   stable download port. Reading serial with DTR/RTS toggling can bounce it to download.

## Remaining work
- **Rectangular pills (issue #2, NOT done).** The pills are rounded, so the GC16 refresh
  box shows a white halo larger than the pill. Wanted: pills as filled rectangles that
  fill their refresh window, so only the pill area flashes. This means overlaying
  rectangular pills in the bake (the SVG pills are rounded) and keeping the firmware
  window tight to the pill.
- **Spinner spin** — panel does ~3–6 fps small partials, but the connect/download steps
  are blocking single‑threaded, so spinning *during* them needs non‑blocking networking.
- **Onboarding v2** — only boot got the v2 redesign; onboarding reuses the older layout on
  the new darker birdhouse.
- **`FF_NO_SLEEP=1`** is still set (dev mode, Wi‑Fi always on → ~4–5 days battery). Flip to
  0 in `include/ff_config.h` for the deep‑sleep model (~8+ months on 10 000 mAh).

## Build / flash / test
```
server/.venv/bin/python firmware/tools/screens/bake_screens.py --preview   # re-bake + preview
cd firmware && pio run -e xiao_ee03 -t upload --upload-port /dev/cu.usbmodem*  # build + flash
```
Read serial without bouncing to download mode: open the port with dtr/rts held inactive,
pulse RTS once to reset. `screen N` / `win …` / `frame …` / `panel updated` trace the boot.

## Files
- `bake_screens.py` — the whole bake (SVG boot panels + PIL onboarding → ff_screens.h).
- `boot_v2.svg` — designer's boot screens (source of truth for boot art + type).
- `art/` — `birdhouse.png` (v2 dark house, = `house.png`), `birdfly.png`, `birdpeek.png`
  (v2 wren); `fly.png`/`wren.png`/`bird.png`/`wren_hole.png` are the older v1 cut‑outs.
- `src/ff_screens.h` — generated; don't hand‑edit.
- `src/main.cpp` — `showScreen`, `freeScreenBuffers`, `displayFrame`, boot flow in `setup()`.
