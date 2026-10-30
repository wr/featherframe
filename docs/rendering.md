# Rendering

How a detection becomes a picture: the provider chain, the plate crop,
the caption, the collage, AI generation and its spend gate, the plate
library, and how a sheet is finished for each panel. `AGENTS.md` has the
rules; the code wins when the two disagree.

## Never a wrong bird

- **Never a wrong bird.** No match / no-plate species → provider returns `None` →
  `compose.render_fallback` (typographic plate). Composite plates are shown
  *whole*, never cropped. The fuzzy name resolver in `names.py` is **build-time
  only** (used by `fetch_plates`); live matching uses the curated index exactly,
  because token overlap mismatches (e.g. "European Starling" → a Blackbird plate).

## Pipeline

**Render pipeline (`render/`).** `pipeline.py` orchestrates:
`compose.render_single` (or `collage.render_collage`) → `finish.to_levels`
(dither) → `framebuffer.pack`. The provider seam is `provider.py`: `ArtProvider`
returns bird artwork or `None`. The live chain is
`ChainedProvider([PlateProvider, GeneratedArtProvider])` → typographic
fallback. `genart.py` is the AI side: `ImageModel` is the vendor seam
(`OpenAIImageModel` first, plain `requests`, default `gpt-image-2.5-sunburst`
via `/v1/images/edits` with real plates as style references — chosen over
`-flare` in a side-by-side test, which pads a lone figure into a pair and so
desyncs the printed legend; the 2.5 pair are the only models that accept the
`xhigh` and `max` qualities — `OpenAIImageModel.effective_quality` clamps those to
`high` on anything older, since the stored config outlives a model switch);
generated plates are
cached forever in `data/generated/` (PNG + JSON sidecar) and only a manual
regenerate from the config page replaces one; failures soft-fail to the
fallback with a per-species cooldown. **Every paid call goes through the spend
gate (`spend.py`).** `Gate.purchase(kind, subject, …)` checks (the
master switch `Config.imagegen_enabled`, the runaway pause, the monthly limit
`ai_monthly_limit_usd`, the subject rule), records the purchase durably, lets
the one vendor call happen, then settles it; a record left open (a timeout, a
5xx, a process that died) counts at its estimate and holds its subject for a
day. The rule is `spend.decide`, held to `tests/fixtures/spend-cases.json`.
Paid model methods are fenced: called outside a purchase they raise
`spend.Unguarded`. `LocalStore` keeps the records in our SQLite (`spend`
table); the old `spend.jsonl` is imported once and no longer written. More than
`RUNAWAY_PER_HOUR` (6) automatic collages and re-bought illustrations in an
hour pauses AI until the owner presses Resume. The Illustrations switch is
`illustrations_generated`. The user's API key lives only in our DB and is
masked in `status()` and the UI. `plate.py` does the content-aware crop of a
scan (a generated PNG is `plate.extract_generated`: paper-normalised, never
cropped — it is composed to fill the sheet, and the scan crop once decapitated
a tern): the
heaviest ink band, extended through faint contiguous ink (hanging straw) up
to a real paper gap, then mirrored about the plate centre so Audubon's own
placement survives. Lettering the fixed trim leaves is lifted: the "N°/PLATE"
line in a top corner, and a caption set beside art that runs down past the
caption band — an isolated, sparse block of type, found from the bottom
edge. What touches the art is a folio's per-plate `mask` (boxes of the
sheet; only ink wholly inside one is painted, so a stem through it stays;
`all` paints a sliver whole), beside per-plate `margins`. The library keys
a crop whose caption was lifted apart (`plate.lifts_caption`). The art is full-bleed to the mat opening:
`compose.py` cover-fits a plate whose edges are inked (Snowy Owl) only if
that crops ≤ 25 % of it, else contain-fits it centred; the date and plate marks
share one footer baseline with the gone-quiet note;
`typography.py` sets the caption: a copperplate script title
(Pinyon Script, OFL, bundled in `featherframe/fonts/`; Garamond italic is the
stand-in if it is ever absent), the engraved
Latin name, and the plate's own legend lines from
`scripts/legends.yaml` (Audubon's printed figure key and plant, transcribed
per Havell plate; `featherframe/legends.py` reduces a composite sheet to the
detected species' line);
the artist and "Plate CLIX" sit in the bottom corners. The left names who
made the illustration, never when the species was heard: who drew
that plate, by its own credit line as the open dataset reads it
(`featherframe/credits.py`, `plate_credits.json` written from each folio's
`credits.csv` by `export_dataset.py credits`, which `check` holds to the
dataset), each artist as the plates engrave them, one form a person
(`credits.CORNER_NAMES`, since a plate's own line can carry a slip, "J. J.
Aububon"; as `Artwork.artist`: "J. J. Audubon", "Lucy Audubon" on Havell 64,
"E. Lear", "J. & E. Gould", "J. Gould & H. C. Richter", every one at full
size),
nothing on a plate whose credit line was not read, or on a
generated sheet "✦ AI GENERATED", in the engraved capitals of "Plate CLIX"
(`theme.GENERATED_MARK`; the face sets lowercase as small caps).
So a repeat of the species shown is the same sheet, and the gone-quiet
footnote is what says a frame has gone stale. The right is the folio's own
plate number (`Artwork.plate`; Havell's for
an Audubon plate), "Plate" in
the same script and the roman numeral in the engraved capitals, since a run
of script capitals is unreadable. A generated sheet has no number there, and
the bough of a species with no plate carries nothing in either corner. `theme.py` holds all geometry/tone constants.

## The collage

**The collage is one sheet set two ways (`render/collage.py`).** The free grid
(`render_collage`, as many plates as `collage_species_max`) and the generated composite
(`render_generated_collage`, one painted scene) share `_bottom_block`: no
header at all, the art from the top margin down, then the date in the engraved
capitals, spaced wide, over a numbered key ("1. BLUE JAY") in prominence order
packed into columns along the bottom; the generated sheet puts a single
plate's ✦ at the date line's right end, under the art's right edge. The grid's cells carry the matching
figure numerals; there are no script names and no "×count" under them. AI
collages are all or nothing (`config.collage_generated`): with the toggle on
and image generation to hand, EVERY collage is the generated sheet — a daytime
rebuild, the nightly one, the button, a settings re-render. `genart.day_composite`
is what bounds the cost: the day's sheet is reused for every redraw and bought
again only at a redraw where the day's *species list* has changed under it (a
count moving is not a change, `collage.same_species`) — so up to one per
collage interval on a busy day, plus the nightly one and the button — with
the per-key cooldown and the soft-fail to the grid intact.
The generated sheet's bough is set in the season of its date (`render/season.py`): meteorological months with an early/mid/late stage,
six months on in the south, each stage stated as ONE state of the tree
(`TREE_STATE`: "still dormant, a heavy late snow lying along its limbs, its buds just swelling"), never a
list of things to paint; a season named alone came back as dead oak leaves.
The hemisphere is the source's `latitude()` (BirdNET-Pi's `Lat`, BirdWeather's
station coords, BirdNET-Go's `bg_latitude`), else the Region (Australia is
south). The sidecar records `season` and `COLLAGE_PROMPT_VERSION` (single
plates keep `PROMPT_VERSION`). The branch is the owner's choice (`Config.collage_branch`, the
Collage section's *Branch*): Seasonal, Daily weather, or Bare (the bough of
before). Daily weather is asked of the household's own text model with web
search (`OpenAITextModel.search_json`, the Responses API's `web_search`,
`max_tool_calls` 2; the owner's key, so no weather service of ours to
license), at the source's `location()` rounded to 0.01°, only when a sheet is
bought and at most once per `weather.REASK_S` (6 h) a day
(`collages/weather.json`), bought through the spend gate as `weather`. An answer
counts only when it names a source: `heavy_snow` (≥ 15 cm fell) / `snowing` / `snow` (lying) / `rain` / "",
recorded as the sidecar's `weather`; once it is known, snow comes from it
alone, so a dry winter day is bare wood; unknown (no location, not OpenAI, a
failed ask) keeps the season's own snow. Wind was tried and does not read. A
sheet painted with another branch is bought again (a sheet from before the
choice is kept); weather alone never repaints one.
Every collage's branch, whatever it carries, is drawn by one clause
(`_P_COMPOSITE_HAND`: sparse sprays, each mark placed by hand and no two alike,
bark in clean engraved line), and the season is told by the state of what
grows, not its amount: a full canopy, a bead on every leaf and even rosettes
of moss read as AI. The composite style references are Havell 354 and 399
(one sparse, clean branch each) then 424; 353's moss nest and 416's lichened
snag came back as moss and lichen on every sheet.

## The plate library

**The plate library (`plate_library.py`)** is the scans' crops taken
once: `library.json` (index.json's species, each naming its crop by
`library` key) + `lib/<key>.gray.png` (`plate.extract`'s output) +
`lib/<key>.color.webp` (the raw colour crop, normalised at load). Both
lossless, so a plate from the library is the plate from the scan (a test
holds it); ~1.2 GB for the edition. `FEATHERFRAME_PLATE_LIBRARY` (a directory
or a URL, fetched on first use into `data/plate-library/`) puts
`LibraryProvider` in place of `PlateProvider` — for a server with no scans,
the hosted render Container first. Build: `python -m featherframe.plate_library
build OUT_DIR` on a machine with every scan; for an update, `build OUT_DIR
--against https://plates.featherframe.app` takes only the crops R2 lacks, and
`upload OUT_DIR` puts them in the bucket (wrangler), `library.json` last.
A crop's key includes a folio's own `margins`, so Havell's keys never move.

## Framebuffer format

- **Framebuffer format (FFF) is a contract with the firmware.** 16-byte header +
  packed pixels: 4bpp = 2px/byte, **high nibble = left pixel, 0=black 15=white**
  (identical to Seeed's sprite). The server emits **native landscape 1872×1404**
  because the panel's `setRotation()` is a no-op — rotation happens in
  `pipeline._finish` via `config.panel_rotation` (90 or 270 only — the
  firmware's `displayFrame()` rejects anything that isn't native 1872×1404),
  while the PNG preview stays upright portrait. `framebuffer.py` and
  `firmware/src/main.cpp displayFrame()` must agree.

## The colour panel

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

## Dithering and memory

- **Dithering** is the panel's own (`Panel.dither`, `panels.py`); the only
  override is `pipeline.DITHER_OVERRIDE` (`preview.py --dither`, and tests
  that want a cheap render). Gray: blue-noise (vectorized, Pi-friendly); Stucki there is a
  correct but slow per-pixel Python loop — don't make it the gray default on a
  Pi Zero. Colour: Stucki (`spectra._diffuse_stucki`), chosen side by side on
  the glass — with six inks, diffusion holds engraving lines and
  grains much tighter than the ordered mix, and a 15 s panel can afford the
  loop. It diffuses the gamut-mapped image and picks each pixel's ink only
  from that pixel's own ink set (its table decomposition): a free choice of
  all six turns neutral gray into green/blue/red dots.
- Keep the render single-threaded and memory-frugal (target: Pi Zero 2W, 512MB).
  Plates load downscaled; `Image.MAX_IMAGE_PIXELS` is lifted for the big scans.

## The wordmark

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
