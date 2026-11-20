# AGENTS.md

Guidance for coding agents working in this repository. Claude Code reads it
through `CLAUDE.md`; Codex reads it directly.

## Source of truth
- GitHub: github.com/wr/featherframe
- Linear project: Featherframe (id: cb3f0377-9778-49b2-a57f-e9f39cfb3de5), Personal team (W)
- Branch prefix: wells/
- PR mode: ready

## Writing

Read `docs/STYLE.md` before writing anything a person reads: webapp copy,
the frame's screens, the README, the wiki, the marketing page, commits, PRs,
Linear, reports. Use its lexicon. Write every user-facing string yourself,
verbatim, in any subagent brief. `server/scripts/check_copy.py` checks the
mechanical rules (`make test` runs it on the webapp; pass `--wiki DIR` before
pushing the wiki).
Link owners to help through `featherframe.app/help/<topic>`
(`site/src/help.json`), never to the wiki directly.

## What this is

Featherframe shows the birds a BirdNET station hears as illustrations from
Audubon's and Gould's books, on an e-paper frame. A detector (BirdNET-Pi,
BirdNET-Go, or a BirdWeather station) names a species; the server finds its
plate, composes a sheet, and finishes it for every frame that shows it; each
frame fetches its own bytes.

- `server/`: Python/Starlette. Owns all logic and all image processing. Runs
  self-hosted (on the BirdNET device as a systemd service in its own venv, or
  in Docker on a NAS) and, unchanged, as Featherframe Cloud's per-household
  Container.
- `firmware/`: ESP32-S3 (PlatformIO). A deliberately simple client that
  fetches a pre-packed framebuffer and pushes it to the panel.
- `hosted/`: Featherframe Cloud on Cloudflare: a Worker, a Durable Object per
  household (its "front door"), the server in a Container, D1 and R2.
- `site/`: featherframe.app, the marketing page and the `/help/<topic>` links.
- `flasher/`: the USB web flasher, published to GitHub Pages on a release.

Two kits run the firmware: the EE03 (Seeed XIAO ePaper Kit, 10.3", 1404×1872,
16-level gray, IT8951) and the EE02 (13.3" E Ink Spectra 6, 1200×1600, six
inks, ~15 s full refresh, no partial refresh). A TRMNL, a Kobo or Kindle, and
a tablet's browser can be frames too. The wiki
(github.com/wr/featherframe/wiki) has the hardware spec, wiring, battery
numbers and the guide to porting another panel.

Kits sold as Featherframe ship on USB power, without a battery. Battery power
(the Power setting's *Battery*, deep sleep, the low-battery hold) is supported
for makers who fit a cell themselves: keep it working, but never describe a
sold kit as battery-powered (site, setup, kit copy).

## Docs

Read the doc for a subsystem before changing it, and update it in the same PR
when you change what it describes.

- `docs/architecture.md`: the server's model in depth: panels, the tick,
  frames, pictures and outputs, push, viewers, the drawn store, adding frames,
  firmware updates, sources, the webapp.
- `docs/rendering.md`: the render pipeline, the caption, the collage, AI
  generation and the spend gate, the plate library, the colour panel,
  dithering, the framebuffer format.
- `docs/folios.md`: the plate crosswalks and how each book's scans are cut;
  `docs/gould-*/` hold each Gould folio's working record,
  `docs/plate-sources.md` the survey of sources.
- `docs/hosted.md`: Featherframe Cloud.
- `docs/firmware.md`: the firmware client on both kits.
- `docs/STYLE.md`: how Featherframe writes.

## Layout

- `server/featherframe/`: `service.py` (the hub), `frames.py`, `pictures.py`,
  `viewers.py`, `drawn.py`, `push.py`, `app.py` (routes), `config.py`,
  `panels.py`, `spend.py`, `switching.py`, `keys.py`, `hosted.py`,
  `birdnet.py` (BirdNET-Pi's database), `sources/` (BirdWeather, pushed
  detections), `render/` (compose, collage, genart, finish, framebuffer,
  spectra), `fonts/`, `art/`.
- `server/templates/`, `server/static/`: the webapp and the `/view` page.
- `server/scripts/`: `fetch_plates.py`, `folios/` (one crosswalk per book),
  `legends.yaml`, `export_dataset.py`, `check_copy.py`.
- `server/tests/`: pytest. `tests/fixtures/*-cases.json` hold the cases a
  Python rule and its port (TypeScript in `hosted/`, JavaScript in the page)
  must both pass.
- `firmware/src/`: `main.cpp`, Improv, the status LED, the setup portal, the
  baked screens. `firmware/tools/`: the screen bake, release assets, portal
  font and preview, kit registration.
- `hosted/src/`: the Worker and the Durable Objects. `hosted/migrations/`: D1
  schema. `hosted/Dockerfile`: the Container image.
- `.github/workflows/`: `ci.yml` (tests), `release.yml` (a `v*` tag).

## Commands

All Make targets run from the repo root; they drive `server/.venv`.

```bash
make venv              # create server venv + install requirements
make plates            # download the folios' plates (scripts/fetch_plates.py)
make plates-all        # cache every Havell plate (~2.9 GB, idempotent, retries)
make preview           # END-TO-END render of a fake Cardinal -> test_output/*.png + *.fff
make preview-all       # one PNG per curated species
make preview-collage   # a daily collage
make preview-fallback  # the typographic (no-plate) fallback
make preview-ee02      # the Cardinal for the EE02 colour panel (six-ink dither)
make preview-views     # the Cardinal as viewers get it (TRMNL X, Kobo, Kindle, TRMNL OG, tablet)
make serve             # run the server on :8181 (advertises mDNS; see Rules)
make test              # pytest
make site-test         # featherframe.app build, unit and Playwright tests
```

`make preview` is the headline verification path: the whole art pipeline
with no hardware and no detections. Run it after any render change and look
at `test_output/`.

More granular:

```bash
cd server
./.venv/bin/python -m pytest tests/test_names.py -q                  # one test file
./.venv/bin/python -m pytest -k cursor -q                            # by keyword
./.venv/bin/python -m featherframe.preview --species "Blue Jay"      # any species
./.venv/bin/python -m featherframe.preview --dither stucki           # bench override, never persisted
./.venv/bin/python scripts/fetch_plates.py --dry-run                 # resolve plates, no download
FEATHERFRAME_NO_MDNS=1 ./.venv/bin/python -m featherframe --port 8181  # run the server directly
./.venv/bin/python -m featherframe --clear-password                  # turn off the webapp's password

# Firmware
cd firmware && pio run -e xiao_ee03 -t upload && pio device monitor  # build/flash the EE03 + serial (115200)
cd firmware && pio run -e ee02                          # the EE02 colour build
cd firmware && pio run -e ee02_bench                    # Spectra 6 refresh-speed bench (serial-driven; test_bench_ee02/)
cd firmware && pio run -e release                       # the binary an EE03 kit ships with (release_ee02: the EE02)
cd firmware && pio run -e generic_bench                 # a panel the server has never heard of; bakes its own screens
python3 firmware/tools/portal_preview.py                 # the setup portal's pages (ff_portal.h) on :8199, no kit needed

# Featherframe Cloud
cd hosted && npm test                                   # vitest, Workers pool
```

Deploy:

- Self-hosted on a BirdNET device: `cd server && ./install.sh` (venv, plates,
  systemd unit).
- On a NAS: `docker-compose.yml` at the root runs `ghcr.io/wr/featherframe`
  (`server/Dockerfile`): host networking for mDNS, `/data` a volume, plates
  from the shared library (`FEATHERFRAME_PLATE_LIBRARY`, set in the
  Dockerfile), `TZ` set because quiet hours run on local time.
- A release: push a `v*` tag. `release.yml` publishes the firmware images and
  `firmware-manifest.json`, the Pages flasher, and the server image.
- Featherframe Cloud: `cd hosted && npx wrangler d1 migrations apply
  featherframe --remote` when there is a new migration, then
  `npx wrangler deploy` from an up-to-date `main`. Docker must be running
  (wrangler builds the Container image); retry if the registry push drops. A
  new image serves only once the rollout ends and the Container restarts.
- featherframe.app: `make site-deploy`.

## Architecture in brief

`docs/architecture.md` has each of these in depth.

**Data flow.** Detector → the server's ingest cursor → a composed sheet per
picture → a finished output per frame (packed framebuffer or PNG, with an
ETag) → `GET /api/frame` (`If-None-Match`) → the panel.

**`service.py` is the hub.** A background thread runs `tick()`, which is two
passes: `_tick_pictures()` decides what each picture is of and composes at
most one sheet per picture; `_tick_frames()` finishes each picture into one
output per frame that shows it. Web handlers only read bytes; nothing is
rendered in a request. The default is to do nothing: few panel refreshes.
Quiet hours are asked of the service (`quiet_window` / `in_quiet_hours`),
never of `Config` alone: Sunset → Sunrise at the source's location.

**Frame, household, picture, output.** A *frame* is any screen the server
draws for (a kit, a TRMNL, an e-reader, a tablet page): one registry,
`frames.py`, no primary frame, each row `asking`, `on` or `ignored`, with what
the device reported kept apart from what the owner set. Every frame of every
kind is approved by the owner on the webapp. The *household* is `Config`:
everything shared by every frame, and the whole `/settings` form.
`frames.frame_config(row, household)` is the one place a frame's effective
config comes from; `frames.capabilities(row)` decides what can be asked of a
frame from its transport and its report, never from a list of models. There
are exactly two *pictures*, `plates` and `collage` (`pictures.py`), each
composed once as a sheet whatever the panels are; `picture_for(shows, now)`
is the one place that answers which picture a frame gets. An *output* is one
frame's picture fitted, matted, dithered and packed for that frame, redrawn
only when its picture or settings change.

**Push.** A kit on USB holds a WebSocket to `/api/frame/push` and is told
when its next `/api/frame` answer would change; it answers with its usual GET,
so `/api/frame` stays the one contract. Battery frames never open one.

**Viewers** (TRMNL, e-readers, the `/view` page) are frames fed over plain
HTTP. They redraw the kept sheet at their own size and depth
(`GET /api/view.png`, TRMNL's `/api/setup` and `/api/display`) and never touch
a kit's output.

**The drawn store** (`drawn.py`, `data/frames/drawn/`) keeps everything drawn
once, named by what it was drawn from, and reuses it by name.

**Rendering** (`render/`, `docs/rendering.md`): `pipeline.py` runs
compose → finish (dither) → framebuffer pack. Art comes from a provider chain
(the plate library or the scans, then AI generation) and falls back to a
typographic plate. Every paid AI call goes through the spend gate
(`spend.py`).

**Featherframe Cloud** (`hosted/`, `docs/hosted.md`) runs this same Python
server in a per-household Container behind a Durable Object front door that
answers frames, keeps detections, and wakes the server only for news. The
only TypeScript copies of server rules are the spend gate's `decide` and the
front door's switch rule `choose`, each held to its Python side's fixtures.
Cloud is free to owners and is built to carry at least 500 households at the
least cost: weigh every hosted change at that scale (container time, Durable
Object requests and duration, R2 operations, D1 rows, Worker requests;
`hosted/src/usage.ts` prices each), prefer the design that starts the server
less often, keeps it up for less time and makes fewer requests, and measure
with the account's GraphQL Analytics before calling a change cheap.

## Rules

Do not break one side of these without the other.

- **Never a wrong bird.** No match or no plate means the provider returns
  `None` and `compose.render_fallback` draws the typographic plate. Composite
  plates are shown whole, never cropped. The fuzzy name resolver in
  `names.py` is build-time only (`fetch_plates`); live matching uses the
  curated index exactly.
- **BirdNET's database is read-only.** `birdnet.py` opens it `?mode=ro`,
  never writes or locks it, and soft-fails to a safe default so a missing or
  odd database keeps the current picture. The fixture schema in
  `tests/_fixtures.py` is verbatim from the Nachtzuster fork.
- **Nothing renders in a request.** Handlers read bytes the tick drew.
- **The framebuffer format (FFF) is a contract with the firmware.**
  `server/featherframe/render/framebuffer.py` and `displayFrame()` in
  `firmware/src/main.cpp` change together. Gray: 16-byte header, 4bpp, high
  nibble = left pixel, 0 = black, 15 = white, native landscape 1872×1404,
  rotation 90 or 270 only. Colour (`FLAG_INKS`): nibbles are ink codes,
  native portrait 1200×1600, rotation 0 or 180. Never send a frame a
  wrong-size image.
- **Bump the drawing versions when pixels move.** `compose.SHEET_VERSION`
  for a gray single sheet (layout, type, fonts, the bough),
  `compose.COLOR_VERSION` for the colour path, `pipeline.FINISH_VERSION` for
  finishing (fit, mat, dither, pack, views), and `LOBBY_DRAWING` in
  `hosted/src/pairing.ts` when Cloud's pairing screen changes. Kept drawings
  are reused by name;
  `tests/test_drawn_versions.py` fails when pixels move without a bump
  (record with `FF_RECORD_DRAWN=1`).
- **Every paid AI call goes through the spend gate.** `Gate.purchase(...)`
  wraps the one vendor call; paid model methods raise `spend.Unguarded`
  outside a purchase. The user's API key is never shown unmasked.
- **Ported rules move together.** `spend.decide` and `spend.ts` are held to
  `tests/fixtures/spend-cases.json`; `switching.py` and `switch.ts` to
  `switch-cases.json`; `test_switch_e2e.py`'s `FrontDoor` mirrors the front
  door's `decideAlone` and `takeState`. Change both sides and the fixtures.
- **A dev server never advertises mDNS.** A frame whose own server is
  unreachable adopts any server advertising its panel. Run every dev, bench or
  preview server on a LAN with frames on it with `FEATHERFRAME_NO_MDNS=1`
  (put it in each `.claude/launch.json` entry, which is untracked). `make
  serve` advertises, because owners run the server that way. The tests turn it
  off themselves.
- **What an owner never tuned is a constant, not a setting.** The poll
  interval, confidence floor, alarm thresholds and corroboration numbers are
  named constants at the top of `service.py`. Think twice before turning one
  back into a field.
- **The render stays single-threaded and memory-frugal** (target: Pi Zero 2W,
  512 MB). Gray dithers with blue noise; Stucki is a slow per-pixel loop there,
  never the gray default. The only dither override is
  `pipeline.DITHER_OVERRIDE`.
- **Server code runs on Python 3.9** (BirdNET-Pi on Bullseye). CI tests 3.9
  and 3.12.
- **Folios reorder, never filter.** A species is asked of the Region's folios
  first, then the rest in published order. `plate: none` hands a species to
  the next folio, never to a guess. Pin a plate only once its engraved
  caption reads right on the scan. `test_crosswalk.py` guards the tricky
  numbers.
- **The AI's style references are Havell plates only**
  (`genart._havell_species`), because its prompts name the Havell edition.
  Image prompts state principles, never examples (`docs/STYLE.md`).
- **Hosted: nothing orders files by time.** A Container start writes every
  file fresh.
- **The wordmark is the plate title's script everywhere.** After any change to
  the script face or its theme sizes, run the favicon, screen bake and portal
  font tools (`docs/rendering.md`). A change to baked text means re-baking the
  screens for every panel.
- **Paths are env-overridable**: `FEATHERFRAME_DATA_DIR`,
  `FEATHERFRAME_PLATES_DIR`, `FEATHERFRAME_DB`, `FEATHERFRAME_PORT`
  (`paths.py`).
- **Never commit** runtime state (`server/data/`), downloaded plates
  (`server/plates/`), secrets or API keys, or a third-party file without a
  licence that allows it.

## Licensing

Code, the folio crosswalks (`server/scripts/folios/`) and the legend
transcriptions (`server/scripts/legends.yaml`) are Apache-2.0 (`LICENSE`,
`NOTICE`). The crosswalks are also published CC0 as
github.com/wr/historical-bird-plates. Bundled fonts are SIL OFL 1.1. Audubon's and
Gould's plates are public domain and are not in the git tree; `THIRD_PARTY.md` lists everything that belongs to
someone else and its terms. Add any new third-party file there.
