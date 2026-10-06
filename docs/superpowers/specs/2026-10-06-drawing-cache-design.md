# A picture drawn once is never drawn again (W-999)

## Why

Drawing pictures is now the largest part of Featherframe Cloud's container
cost: about 60 % of container CPU on a busy day and about half its running
time. Most of that drawing repeats itself. On 4 Oct 2026 Wells's household
drew 77 pictures of 18 species. Since W-984 a species' sheet is the same
every time it is drawn (no time on it), but the server finishes it again
from scratch whenever it comes back after another species: on 5 Oct the
Black-capped Chickadee was drawn four times with one ETag. Measured in the
hosted image at a quarter CPU, a returning species costs 1.65 CPU-s, the
same as a new one.

Each draw also uploads its sheets and frames again, about 6 MB, and
container egress is billed beyond 1 TB a month. And because `frames/out`,
`frames/views` and `frames/pictures` are files of their own, every fresh
Container downloads all of them (over 10 MB a wake for a colour household).

Cloud must carry at least 500 households at the least cost (AGENTS.md).

## The idea

Everything drawn is kept once, under a name that says what it is, and
pictures, frames and viewers point at it instead of holding a copy. A
returning species still composes its gray sheet (cheap and deterministic, so
it lands on the same ETag), and from there everything is found, not made:
the colour twin, each frame's finished output, each viewer's image.

Reviewed twice before building (6 Oct 2026). The first draft also memoised which
sheet a detection would draw, to skip the gray compose; the review showed
that predicting the art without loading it turns passing failures (a crop
fetch that times out, a colour load that fails) into wrong pictures that
persist. That is left out: this design never names anything by a guess. The
second review moved Refresh off deleting, pinned the pruner per file, put
the finishing version in views' names, checked a twin's art against its gray
sheet's, and added a tripwire for the versions.

## Names

Under `frames/drawn/`:

- `sheets/<etag>.png`: a picture's gray sheet. Its ETag is the hash of its
  own pixels (`pictures.etag_for`), so the name is the content.
- `sheets/<etag>-c<COLOR_VERSION>.png`: its colour twin, kept only when the
  colour art truly loaded (a failed `color_pair()` gives no twin, and colour
  screens finish from the gray sheet, as today when there is none) and is the
  same art as the gray sheet's: each sheet carries `ff_art` (folio, plate,
  volume, generated, artist; per cell on a collage grid), and a recompose
  that asks the providers again later must agree with it.
  `COLOR_VERSION` (compose.py) is bumped whenever the colour path changes
  its pixels (colour crop, colour compose), so an old twin is never reused.
- `out/<etag>-<okey>.fff` and `.png`: one frame's finished output. `okey`
  hashes `_output_src` (which names the source sheet file, so the twin's
  version, plus the panel, rotation, mat, mat guide and bit depth) and
  `fkey`: `FINISH_VERSION` (pipeline.py, bumped when finishing changes
  pixels), a bench dither override, and the Pillow, numpy and numba versions.
- `views/<sheet>-<view>-<fkey>.png`: a viewer's image, named by the sheet
  file it was drawn from (`<etag>` or `<etag>-c<n>`), its view key and fkey.
  The name is the ETag the viewer is served.

`tests/test_drawn_versions.py` holds the pixels each versioned path draws
(the library's colour pair, paper normalising, a twin, the bough's twin, one
finish per panel, one view per format) for the libraries it was recorded
with, and fails when they move without their version.

Waiting plates stay in `frames/views/` as today.

## Flows

**A commit (`_commit`).** Compose the gray sheet as today and take its ETag.
Write `sheets/<etag>.png` only if it is not there (an identical file is never
rewritten, so never uploaded again). The colour twin: if
`sheets/<etag>.c<n>.png` is there, keep it; else compose it when it is wanted,
as today, and keep it only if its colour loaded. Files are written before the
picture's ETag changes, under unique temporary names.

A sheet already there is checked whole before it is trusted (a Pi that lost
power can tear one); a torn one is written again.

**A frame (`_draw_frame`).** Read the picture's ETag once, under `_lock`;
the source is that ETag's immutable sheet file. If the frame already points
at that output and the file is there, nothing. Else, if
`out/<etag>-<okey>.fff` is kept, point the frame at it:
`_out[fid] = {etag, src, file}`. A file here is read whole and its ETag taken
from its bytes; one still at the front door takes the ETag recorded in the kv
row `drawn` when it was made (no record: draw it). Else finish it as today
into that file.
`_out_paths(fid)` reads `_out[fid]["file"]`; `/api/frame`, the preview and
`hosted_state()` all name that file, so the front door serves the object
already in R2.

**A viewer (`view_png`, `ensure_view`, `_draw_view`).** The same: read the
ETag once, pick the source sheet, name the image after it, draw it only if it
is not there. No longer deleted when the picture changes.

**Refresh** (the owner's button) deletes nothing. It records a new token for
the ETag it draws; the gray sheet and the twin are written again in place,
and an output or view drawn under another token is drawn again under the
same name, by an atomic replace. `refresh_now` finishes every frame before it
returns, so what the front door is then told names whole, new files.

**Nothing but the pruner deletes under `drawn/`.** `Picture.commit` and
`drop()`, `_drop_output` and `_rename_output` only drop or move pointers.

## Hosted

`frames/drawn/` is lazy (`hosted._LAZY`, `.png` and `.fff`): a start never
downloads it; a file comes down only when something reads it. Every read
goes through `hosted.local()`, every existence check through
`hosted.exists()`, listing through `hosted.glob()`, deletion through
`hosted.remove()`: `Picture.sheets()` and `has_color()`, `_output_src`,
`_draw_frame`, `_output_bytes`, the preview, `view_png`, `_draw_view`,
`ensure_view`, `_keep_collage_day`, `_save_history_thumb` (whose `full.exists()`
on a lazy JPEG today re-encodes and re-uploads it on every wake), and
`_load_outputs` (which checks only outputs that are local; a lazy one is
trusted, R2 objects are whole).

A sync uploads, then reports the state, then deletes, so the front door is
never pointed at a file that is already gone.

## Pruning

The kv row `drawn` holds `used: {etag: iso}` (touched on every commit,
before any file is written), `outs: {okey: {pic, etag, at, f}}`,
`views: {name: {at, f}}` and `fresh: {etag: token}`. After a commit, list
`drawn/` (`hosted.glob`) and keep every file a picture, a frame or an `on`
viewer points at (pinned per file, so five kits on one picture keep five
outputs), every ETag they point at, then the 40 most recently used ETags; of
each kept ETag at most 4 outputs and 8 views besides the pinned ones, newest
first by the record's time (a lazy file has no mtime). A file no record names
goes too (a lost write), unless it is here and younger than 10 minutes:
another thread may be drawing it. Temporary names are never touched. About 6 MB an ETag for a gray + colour
household (240 MB at the cap), about 2.5 MB for gray only.

## Migration (forward only)

On the first start of this build:

- A picture's `pictures/<kind>/sheet.png` moves to `drawn/sheets/<etag>.png`
  once its pixels hash to the picture's ETag; its `sheet_color.png` moves as
  the `-c1` twin (version 1 is what drew it).
- Each frame's `frames/out/<fid>.fff` and `.png` move to
  `drawn/out/<etag>-<okey>.*`, `okey` from the frame's stored `src` with its
  sheet renamed (`sheet.png` → `<etag>.png`, `sheet_color.png` →
  `<etag>-c1.png`), never from its current settings (a save not yet drawn
  must still be drawn), and only when the file is whole and its ETag the
  stored one; no frame repaints.
- `frames/views/` keeps its waiting plates; viewer images are drawn again
  under their new names (a TRMNL repaints once).

On Cloud the moved files are already local at that start, so this is one
upload and nothing drawn. Forward only, like the spend records: an older
image finds no sheet and no output, so its frames are answered 503 until the
next new picture.

## Expected effect

A return composes the gray sheet (about 0.35 CPU-s of a colour household's
1.5, bench) and finds everything else: no colour work, no dither, no PNG
writes, no uploads. On 4 Oct about three draws in four were returns. For a
colour household like Wells's that is roughly −50 % of drawing CPU and
running time, and most of the ~6 MB a draw uploads; a gray-only household
saves less CPU (its draw is 0.37 CPU-s) but the same uploads. Every start
also stops downloading the pictures, frames and views (over 10 MB a wake
for a colour household). A return on Cloud still fetches its gray crop and
`library.json` (about 0.8 MB) in each new process: `plate-library/` is never
pushed.

Measured on a Mac with real plates (no numba, so main's colour dither runs
in Python and its figure is high): a returning species with a gray and a
colour kit, 5.7 CPU-s on main, 0.16 on this build, the outputs byte for byte
the same.

Later, if the numbers say so: remember which sheet a detection drew, keyed
by the art actually loaded (never a prediction), to skip the gray compose
too.

## Tests

- A returning species (after another) composes, lands on the same ETag, and
  re-uses the twin, every frame's output and every view: no finish, no PNG
  write, no new file.
- A miss whenever a frame's settings, `FINISH_VERSION` or `COLOR_VERSION`
  change, or the colour art fails to load, or a twin would be other art.
- Refresh draws again in place, deletes nothing, and the frames are finished
  before it returns.
- Nothing but the pruner deletes; pruning keeps what is pointed at and the
  40 most recent, caps unpinned variants, sweeps unknown files but not young
  ones or temporary names.
- Two threads drawing the same ETag never mix pictures in one file.
- A torn output or sheet on self-hosted is drawn again, not served.
- The version tripwire.
- Hosted: a start downloads nothing under `frames/drawn/`; a frame's output
  is served by the front door from its file; a view, a preview and a sheet
  come down when asked; delete only after report.
- Migration from today's layout without a repaint of any kit; a sheet that
  is not its picture's is not moved.

## Not in this change

Remembering which sheet a detection draws; sharing drawings between
households; lighter starts; the server Durable Object's time per wake.
