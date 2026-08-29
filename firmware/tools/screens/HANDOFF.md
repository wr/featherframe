# Boot / first-run screens — handoff

State of the illustrated boot + onboarding panel screens on the EE03 (10.3" ED103TC2,
IT8951, 1404×1872 portrait / 1872×1404 native landscape). Branch
`wells/w-574-frame-buttons-check-now-collage-status-page`.

## Current status — working, flashed
Boot sequence on the panel:
`splash → "Connecting to Wi-Fi" (wren flies in) → "Connecting to server" (empty house)
→ "Downloading image" (wren in the hole) → server bird plate`. First run shows the
setup-portal steps instead, then hands over to the SAME flow after one full repaint —
there is no separate onboarding checklist.
- Splash + each connecting screen render; the birdhouse + wordmark hold steady and only
  the changed box (bird / wren / pill) repaints.
- The plate (e.g. American Goldfinch from `10.0.2.15:8090`) loads ~10–12 s after
  "Downloading…" (server render + transfer time).
- Button toasts are baked pills too (`toast_assets`): in-progress toasts carry the
  sweeping mark, "Up to date" a check, failures the outlined+slashed language — all
  pushed as DU tiles at the toast band over the plate margin (the old GFX-font
  1-bit toast path is gone). Dark mode: the server serves inverted plates and
  announces `X-FF-Invert`; the firmware flips every baked screen/tile (byte ^ 0xFF)
  per the NVS-persisted flag. Type keeps clear of the mat (~4%/edge); art bleeds.
- Error states (W-587): a dead-ended attempt swaps the pill band in place (outlined
  pill + slashed icon; HTTP 503 keeps the solid pill as "Waiting for the first bird")
  with a "Trying again in N min" line beneath; over a painted plate only a small
  slashed glyph appears in the margin corner past ≥4 fails and ≥30 min. Backoff:
  deep-sleep 1→5→15 min capped (RTC-tracked); always-awake 15 s→60 s. The portal
  never auto-opens when credentials exist (first run / KEY2-hold only), and the
  watchdog is armed in both power models.

## How it's built
**Bake pipeline** — `bake_screens.py` (run with the server venv):
`server/.venv/bin/python firmware/tools/screens/bake_screens.py [--preview]`
- Type is set by the SERVER's own typography module (`sys.path` → `server/`): the
  wordmark IS the plate title (EB Garamond swash italic at `theme.TITLE_SIZE`, v3
  weight/tracking, via `typography.draw_title` — descenders intact), and the splash
  version line is the plates' engraved capitals (`typography.draw_engraved`, Adorn
  Engraved at `theme.SUBTITLE_SIZE`) under a hedera. A plate-typography change on the
  server re-bakes straight into the boot face. `boot_v2.svg` is now reference-only.
- Pills: Inter Medium (vendored in `./fonts`, OFL) for readability, with a
  three-diamond **loading mark** — the solid diamond sweeps left→right. The bake
  emits per-screen animation tiles + native mirrored coords (`FfLoader` in the
  header); baked screens carry frame 0, and tiles are byte-checked against them.
- The bird / wren / pill boxes are baked **binary on purpose** so their windows
  refresh with DU (no flash): the fly-in bird is thresholded line art on empty sky;
  the **wren‑in‑hole is Bayer-dithered** (its light feathers would threshold into a
  black blob — do not change this back). Everything else stays 16-level gray.
- The single **setup** screen (`screen_setup`) shares the splash birdhouse and fits
  its card to the widest line; error/retry/corner tiles come from `error_assets()`.
- Gray output: fixed white‑point + gamma LUT `apply_curve()` (`WHITE_PT=246`,
  `GRAY_GAMMA=1.4`). MUST stay content‑independent — a mean‑based contrast maps the same
  birdhouse to different values per screen and makes the whole thing diff/flash.
- Output: 4bpp gray in native 1872×1404 (rot90 like the server), PackBits‑packed into
  `firmware/src/ff_screens.h` (~1.4 MB), + `contact_sheet.png` preview.

**Firmware** — `src/main.cpp`:
- `showScreen(idx)`: entry screens (SPLASH/SETUP) do a full `displayFrame` (gray). Every
  other screen diffs vs the previous baked body, bands the changed native COLUMNS, and
  does a **windowed IT8951 update** per box (`tconLoadImage` + `tconDisplayArea`). The
  waveform is chosen per window by content: binary boxes (bird, wren, pills — baked
  that way on purpose) take **DU** (no flash); windows with real grays take **GC16**.
  With the current bake, every boot transition is DU — nothing flashes until the
  plate's own full refresh.
- `loaderTask` (FreeRTOS, core 1) sweeps the loading mark: it pushes the baked
  `ff_loader[]` tiles as ~200ms DU partials every `FF_LOADER_STEP_MS` while
  `g_loaderAnim.on` — through Wi‑Fi connect, server connect, and the download, which
  all block the main task. `g_panelMutex` (recursive) serializes every panel touch
  (`showScreen`/`displayFrame`/toasts vs the task); a full refresh disarms the mark.
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
   before any full plate load — and never grow the boot-time PSRAM budget: retaining the
   frame copy (`g_lastFrame`) at the splash paint once pushed the SETUP screen's full
   write past the cliff and the portal instructions never reached the glass. Retention is
   fetch-path-only for this reason; `displayFrame` logs `psram largest free` so the
   failure is no longer silent.
3. **Panel needs pioarduino Arduino‑ESP32 v3** to cold‑init, and a **real power‑on**
   (soft/RTS resets don't cold‑init). See memory `panel-needs-pioarduino-v3`.
4. **"Panel dead" is usually the USB cable** (power‑only / flaky) — no amber boot LED,
   flapping port. See memory `panel-dead-check-usb-cable`.
5. Native‑USB flashing: if the port vanishes, hold BOOT, tap RESET, release BOOT for a
   stable download port. Reading serial with DTR/RTS toggling can bounce it to download.

## Remaining work
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
- `bake_screens.py` — the whole bake (art + server typography → ff_screens.h).
- `boot_v2.svg` — designer's boot screens (reference only; type is drawn live now).
- `art/` — `birdhouse.png` (v2 dark house, = `house.png`), `birdfly.png`, `birdpeek.png`
  (v2 wren); `fly.png`/`wren.png`/`bird.png`/`wren_hole.png` are the older v1 cut‑outs.
- `fonts/` — vendored Inter Medium (pill text; OFL, see fonts/OFL.txt).
- `src/ff_screens.h` — generated; don't hand‑edit.
- `src/main.cpp` — `showScreen`, `loaderTask`, `freeScreenBuffers`, `displayFrame`,
  boot flow in `setup()`.
