# Settings simplification (W-821)

Approved in chat, 20 Sep 2026. First of several simplification passes; hosting
(Pi / Cloudflare Worker / homelab) and render speed are separate problems.

## Goal

Fewer controls for the owner to think about and fewer states a server + frame
can be in. `Config` goes from 50 fields to 31; `tick()`'s main tree from 11
questions to 9. Behaviour an owner never tuned becomes a constant next to the
code that uses it.

## Kept on purpose

- **Daytime collage.** A Spectra 6 refresh takes ~30 s, so a collage every N
  hours can beat a plate per detection there.
- **Power settings** (`power_mode`, `wake_interval_minutes`,
  `device_poll_seconds`). The USB/battery inference is not reliable enough to
  own the choice.
- **Sunset → sunrise** quiet hours.
- **Image-generation options**, all of them.

## Removed fields

| Field | Becomes |
|---|---|
| `single_show_latest`, `refresh_debounce_minutes` | Always show the latest qualifying detection. The debounce and same-species skip leave `tick()`. |
| `dwell_minutes` | `DWELL_MINUTES = 90` |
| `confidence_threshold`, `birdnet_go_defer_confidence` | The source's own threshold. `CONFIDENCE_FLOOR = 0.7` applies to sources that have none, and to BirdNET-Go only when its threshold can't be read. |
| `corroborate_new_species`, `corroborate_confidence`, `corroborate_window_hours`, `corroborate_min_gap_minutes` | Always on: 0.85, 24 h, 10 min. |
| `quiet_alarm_hours`, `source_alarm_minutes` | 6 h, 60 min. |
| `poll_interval_seconds` | 5 s (BirdWeather's 60 s floor stays). |
| `panel_follow` and the Panel select | `panel` always follows the active frame. It stays in the blob as state (the server must remember the panel between check-ins) and `FEATHERFRAME_PANEL` still seeds a fresh install. |
| `gray_mode` | 16-level gray; a `mono` panel format is still 1-bit by its format. |
| `dither` | The panel's own (`Panel.dither`). `preview.py --dither` stays as an unpersisted bench override. |
| `color_saturation` | `spectra.SATURATION = 1.2` |
| `dark_mode` | Gone: no inversion anywhere. |
| `show_plate_number` | The mark is always drawn when the plate has a number. |

`collage_rebuilds_per_day` is renamed `collage_interval_hours` (1–24, default
8; an old blob migrates as `round(24 / rebuilds)`). `mode` joins
`panels.PANEL_SETTINGS`, so the Spectra 6's defaults are Collage and the gray
panel's are Single. As with every panel default, that is offered by the
existing "use this panel's defaults" notice and never applied unasked.

`Config.from_dict` already drops unknown keys, so stored blobs load unchanged.

## The plate mark

Audubon's sheets carry two marks: "No. 32" (the part, five plates each) and
"Plate CLIX". The second identifies the bird. The corner mark becomes
**"Plate CLIX"**, roman, from `Artwork.audubon_plate`. A species with no Havell
plate (generated, or the typographic fallback) prints nothing; a generated
plate keeps "Imagined after Audubon." on that line.

`species_ordinal` is deleted from every source and the base class: it was a
per-instance count, and on BirdNET-Go a network call per render. The footer's
reserved width is measured against the widest numeral, CCCLXXXVIII.

## Dark mode

Server (PR 3): `dark_mode`, `Config.dark_now`, the dark-flip and hold-flip
checks in `tick()`, the welcome plate's dark check, the `dark` frame meta and
the invert stage of both finishes all go. The server **keeps sending
`X-FF-Invert: 0`**: fielded firmware stores the last value in NVS, and a frame
that was dark would otherwise keep dark boot screens.

Firmware (PR 4): the stored flag, the header parse and the inverted handling
of baked screens and tiles go, on both panels. Once both frames run it, the
server stops sending the header.

## Pull requests

1. **Settings cull**: `config.py`, `service.py`, `app.py`, `panels.py`,
   `render/pipeline.py`, `render/spectra.py`, `preview.py`, the page, tests.
2. **Plate mark**: `render/typography.py`, `render/compose.py`, `theme.py`,
   sources, `service.py`, tests.
3. **Dark mode, server.**
4. **Dark mode, firmware**, then the header drops.

## Tests

- An old blob with every removed key loads, and saves without them.
- `collage_rebuilds_per_day: 3` migrates to `collage_interval_hours: 8`.
- A settings POST carrying removed fields changes nothing.
- Roman numerals: 1, 4, 9, 40, 159, 388, 435.
- A plate with a Havell number prints the mark; generated and fallback do not.
- BirdNET-Go falls back to `CONFIDENCE_FLOOR` when its threshold is unreadable.
- Deleted with their features: `test_dark_mode.py`, debounce and
  same-species-skip cases, `species_ordinal` cases.

## Not in this pass

Footnote re-renders, the welcome plate, the owner hold, frame admission and
the image-generation matrix are unchanged.
