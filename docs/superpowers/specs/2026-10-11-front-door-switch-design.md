# The front door switches to a picture already drawn (W-1025)

## Why

Since W-1012 and W-1019 a Featherframe Cloud wake is cheap, about 1 CPU-s,
but nearly every wake does the same small thing. On 10 Oct 2026 Wells's
household started its server 68 times: 56 of those only pointed the frames
at a picture already drawn and kept in R2 (House Finch, Eastern Chipmunk and
American Robin taking turns every five minutes), 3 drew a new picture, 8
changed nothing. Replaying 4–10 Oct's BirdNET-Go detections through the
front door's wake rule gives the same shape every day: 95–98 % of daytime
wakes end on a picture already drawn, and 1–3 a day need one composed.

A start costs the same whatever it does: container CPU and running time,
the Durable Object's time while it runs, the pull, the push. At 10 Oct's
rate Cloud costs about $0.0058 a household a day, $87 a month for 500
households, and the $5 plan carries 7–9 households before overage. Cloud
must carry at least 500 households at the least cost (AGENTS.md).

## The idea

The server answers ahead of time: every report says, for each species whose
picture it has already drawn for every screen, exactly what each screen
would be switched to if that species were heard next, and how that
detection would rank. When the front door would wake the server only
because detections arrived, and every species in the batch has such an
answer, it makes the choice itself by the server's own rule, switches the
screens, and leaves a marker in its queue where the batch ended.

The server takes the queue itself, first thing at every start and every
wake, before it can report anything: the detections and markers in order,
running its own choice at each marker at the marker's time. History, the
cursor and every count come out as if it had been awake, and nothing it
reports can be older than what the front door already showed. It is still
the truth: a marker that disagrees with its own choice is counted and the
server's choice wins.

The glass changes at the same moments as today (the same five-minute
batches), a few seconds sooner (no start). The server starts for a species
it has not drawn or that is new today, the schedule it names, the page, a
pairing, and a frame or a setting changing.

## What the server reports: the switch table

`hosted_state()` gains `switches`, built in hosted mode only, after the tick
and its prune (so every file it names is kept):

```json
"switches": {
  "valid_until": 1760241600,
  "shown": "haemorhous mexicanus",
  "shown_clean": true,
  "blocked": ["house sparrow", "passer domesticus"],
  "floor": 0.0,
  "notes": ["8812", "8813"],
  "species": {
    "turdus migratorius": {
      "common": "american robin",
      "first_today": false,
      "etag": "95a595e4bea8a584",
      "frames": {"E886D8…": {"etag": "…", "file": "frames/drawn/out/…fff",
                              "push": {"etag": "…", "rotation": 90, "power": "awake", "ota": false}}},
      "viewers": {"…": {"name": "…", "file": "frames/drawn/views/…png"}}
    }
  }
}
```

- **Keyed by scientific name**, lowercased, as the server keys a species
  (`Detection.key`).
- **Which species.** Every species with a kept sheet under the key
  `_render_single` would look up for a detection of it heard now
  (`drawn.kept_sheet`), whose detection would be shown from that sheet
  without composing or buying anything, and which is not new today
  (`_is_new_species`: such a detection ranks first-ever and may need
  corroboration, so it always wakes the server). The `SingleSpec` comes
  from one helper `_render_single` also uses, with the footnote a fresh
  detection carries (none: a detection clears the quiet and outage notes)
  and the fallback note computed once per report.
- **Every screen or none.** An entry lists every `on` kit and every `on`
  viewer that would show the plates picture after the switch
  (`picture_for(shows, now)`), each with the output it would be served:
  the kit's `out/<etag>-<okey>` for its current settings and its push
  message with the new ETag; the viewer's drawn view. A species with any
  such screen lacking its file has no entry. Frames on the collage are not
  in any entry and are never touched by a switch.
- **`first_today`.** Whether a detection of this species would be its first
  today (`_today_counts` has none of it): the server's novelty, said ahead.
- **`shown`, `shown_clean`.** The species of the plates picture now, and
  whether its sheet is exactly what a fresh detection of it would show (no
  footnote, not waiting for colour, art present or none could arrive:
  `_is_shown` of a fresh spec). Only then is choosing it "same".
- **`blocked`, `floor`, `notes`.** The blocklist (lowercased names); the
  confidence floor for this source (0 for BirdNET-Go, whose own threshold
  wins; `CONFIDENCE_FLOOR` for BirdNET-Pi's Apprise); the BirdNET-Go
  `note_id`s of the last hour the server took, so a push that repeats one
  (BirdNET-Go sends a detection again as a new species) is not counted
  twice across a take.
- **`valid_until`.** The sooner of the next local midnight and the next
  start of quiet hours, as an epoch. After it the front door decides
  nothing until a report.
- **Empty (`{}`)** whenever the front door must not decide: not hosted, no
  frame shows the plates picture, a source that is not a push feed
  (BirdWeather is left for later), or quiet hours. An older server image
  reports no `switches` at all, so the front door never decides for it.

At most 25 pictures are kept (`drawn.KEEP_ETAGS`, collages and footnote
variants included), so the table is a few kilobytes.

## The choice, in one place twice

The server's rule for a batch is `_pick_showable(by_novelty=True)`: newest
first, skip blocked species, skip a new species not yet corroborated, take
the highest novelty (first-ever 2, first-today 1, repeat 0), the newest
within a class. The part the front door needs moves into
`featherframe/switching.py`, a pure module:

- `parse(kind, body)`: a push body to `{scientific, common, confidence,
  note}`, the same fields `_parse_apprise` and `_parse_birdnet_go` read
  (which now call it), or nothing for a body that is not a detection, the
  BirdNET-Go test species, or names neither name.
- `best(ranked)`: the highest rank, newest within it. `_pick_showable`
  calls it with `_novelty`'s ranks.
- `choose(table, queued)` → `"wake"`, `"same"` or a species key.

`choose` over the detections queued since the server last took the queue
(parsed, in arrival order):

1. Drop a push below `floor`, one whose `note` repeats an earlier push's
   or is in `notes`, and a blocked species (by either name).
2. Resolve each push to an entry by its scientific name. A push with no
   scientific name wakes the server: it would draw its sheet with no Latin
   name, which is another sheet than any entry names. If any push resolves
   to no entry, or BirdNET-Go says `days_since_first_seen == 0`: **wake**.
3. Each push ranks 1 if its entry is `first_today` and that species appears
   exactly once in the queued detections, else 0: `_novelty`'s
   `count <= 1`, counted over what the server has not yet taken.
4. `best` of the batch since the last marker. The species shown with
   `shown_clean`: **same**. The species shown without it, or any other
   species: that species.

`hosted/src/switch.ts` is its port, held to
`server/tests/fixtures/switch-cases.json` as `spend.decide` is to
`spend-cases.json`. Its cases are raw push bodies of both kinds (the
Apprise message with its `{…}` inside, an out-of-range confidence, a
non-string or over-long name, `note_id` as number and string, a body that
is not a detection, the test species) with what `parse` reads, and batches
with a table and the answer. A test also holds that an entry's
`first_today` is what `_novelty` says of a fresh detection.

## What the front door does

It decides only when detections are the only reason for a wake:

- `ingest()` sets `push_news`, apart from `news`, which pairing
  (`adopt`) and BirdWeather's look keep setting as today.
- At the alarm, decide only if `push_news` is set and nothing else is due:
  no `news`, no `look`, no named wake, no daily wake, no queued check-in
  or seed row, the server neither running nor starting, and a table
  within `valid_until`.
- The five-minute gap is counted from the later of `wake_ms` and a new
  `switch_ms`; `wake_ms` stays the server's last wake (the daily wake and
  the admin's "last wake" read it).

Otherwise it wakes the server as today. When it decides, in one synchronous
block (no `await` between reading the queue and writing the marker):

- run `choose`. **wake**: wake as today.
- **same** or a species: append the marker, `INSERT INTO ingest (path,
  body)` with path `marker` and body `{at, choice, etag, upto}` (`upto`,
  the last queued `seq` it decided over). For a species, also update each
  listed frame's row (`etag`, `file`, `push`) and send the new push message
  to its sockets, update each listed viewer's `name` and `file`, and set
  `unchanged` to `blocked` plus the species' names. Set `switch_ms`; clear
  `push_news`.

Then, outside the block, age the page's changing copies (a new
`cache.age()`: `filled_at` to 0, bodies kept), so the next look serves the
stale copy and asks for a wake, as after any change today. `clear()` would
delete them and start the server on the next poll.

`takeState()` keeps a frame's row, a viewer's and `unchanged` as the front
door last switched them while a marker the server has not taken is queued
(a report from a run that took the queue before that marker was written).

## The queue, taken by the server

Today `wake()` POSTs each queued detection to the server, one request each,
deleting it whether or not it landed. Instead the server takes the queue:

- `POST {base}/ingest/take` returns every queued row with its `seq` (pushes
  and markers, in order). `POST {base}/ingest/ack {upto}` deletes them, and
  is sent only after the settle that put them in the server's database in
  R2. The server keeps the last `seq` it applied and skips any at or below
  it, so a take repeated after a crash applies nothing twice.
- The server takes it at start, right after the pull (`hosted.take`,
  beside the check-ins), and again at the top of every `/api/hosted/run`.
  At start it runs beside the server, not in front of it: the container
  library gives a start 20 s to answer on its port, and a day's queue can
  take seconds. The take holds the tick lock and counts as work
  (`/api/hosted/busy`, W-917), and every report waits for it to finish (a
  stop, a save's settle, the run's): nothing the server reports can be
  older than what the front door has already shown.
- `wake()` no longer sends detections; it starts the server and runs it.

## What the server does with a marker

Met in the taken queue after the pushes it covers, under the tick lock:

- run the plates part of a tick at the marker's time: refresh the source's
  quiet and outage state as `_tick_pictures` does, then `_single_tick(at)`,
  which reads exactly the pushes the front door saw, ranks them by the same
  counts, and commits the kept sheet. History gets the switch at its time
  (`log_render`, `touch` and `commit` take `now`), the drawn store's
  recency moves, the cursor passes the batch.
- compare the picture it now shows against the marker's `choice` and
  `etag`. A mismatch is logged and counted in the kv store; the count rides
  in `hosted_state` and shows in the admin page's households table. The
  server's picture stands, and the report at the end of the run puts it on
  the screens.

Frames' outputs follow at the run's own tick, which finds each one already
drawn and points the frames at the last picture chosen. The report then
rebuilds the table.

## What the owner sees

- The frames change when they do today, a few seconds sooner.
- History lists every switch at the time it was made, once the server next
  starts. Until then the webapp's preview and History show the last
  picture the server drew; opening the page serves that copy and wakes the
  server, as it does after any change today.
- Nothing else: no setting, no copy.

## Cost

Replaying 4–10 Oct's BirdNET-Go detections, wakes for news fall from 64–90
a day to 1–4; scheduled wakes (the end of quiet hours, the nightly collage),
page visits and settings saves stay. Estimated at 10 Oct's prices: about
$0.0058 → $0.0015–0.002 a household a day; 500 households about $87 →
$25–30 a month; the $5 plan carries about 25–35 households. Container CPU
per remaining wake rises a little (a plates tick per marker, ~0.05–0.1
CPU-s each, and a History thumbnail read from the drawn store when one has
been let go), far less than a start.

BirdNET-Pi households save less: its pushes carry no first-heard date, so a
sheet's key includes the day (`_drawn_from`'s `first`) and each species'
first detection of the day still composes and wakes. After that the table
serves it as it does BirdNET-Go.

Measure as W-1013 was: whole days, `cost.py` before and after, and the
VM's own CPU lines.

## Not in this change

- **BirdWeather.** The front door already reads a station's detections; the
  same table would serve it, but its marker has to carry the station's
  detection id instead of a queue position. Next, if households use it.
- **Serving the preview from the front door.** The page's
  `/api/frames/<id>/preview.png` could follow the switch at once (the
  output's PNG sits beside its `.fff`). Not needed for cost.
- **Self-hosted** is unchanged: there is no front door.

## Risks

- **A wrong choice.** The port is held to a shared fixture, raw bodies
  included, and every marker is checked and counted by the server; a
  mismatch costs one switch the server undoes, and never shows a wrong
  species: an entry is that species' own picture.
- **A report behind the front door.** Closed by the server taking the queue
  before its first report, and by `takeState` keeping switched rows while
  a marker is untaken.
- **A stale table.** Rebuilt at every report; expires at midnight or quiet
  hours; a setting, a frame or a pairing starts the server, which reports
  a new one. A `FINISH_VERSION` bump or a new viewer empties entries until
  each picture is drawn again.
- **A long take.** A day of pushes and markers at one start: a few seconds
  of the server's own work, inside `MAX_WORK_S`, counted as busy.

## Tests

- `switch-cases.json`, run by `tests/test_switching.py` and
  `hosted/test/switch.test.ts`: the raw-body parse cases above; blocked,
  below the floor, a repeated `note_id` within the queue and from `notes`,
  a species with no entry, a new species, a push with only a common name
  (it wakes), first-today beats a newer repeat, a species heard twice ranks
  as a repeat, the shown species clean (same) and not clean (switch), an
  empty batch.
- Server: the table lists exactly the species a detection would show from a
  kept sheet, with every on screen's output; none for a missing output, a
  new species, or a frame on the collage; `first_today` equals `_novelty`;
  `valid_until` is the sooner of midnight and quiet hours.
- Server: taking a queue of pushes and markers gives the same History,
  cursor and picture as ticks at those times; a repeated take applies
  nothing twice; a report waits for the take; a mismatch is counted.
- Front door: a batch inside the table switches the rows, sends the push,
  writes the marker in order and does not start the server; a batch outside
  it wakes; a pairing, a named wake, a look or a running server wakes;
  `takeState` keeps switched rows while a marker is untaken; `age()` keeps
  bodies.
- After deploy: a day's wakes and cost against 10 Oct.

## Reviewed

11 Oct 2026, against the code, before planning. The first draft replayed
the queue as POSTs at the next wake and let reports land in between; the
review found that a save, a stop or a page's start would report the old
picture and repaint every frame twice. The server now takes the queue
before it can report. It also found that clearing the page cache starts the
server, that `news` carries pairing too, that "same" was undefined while a
footnote is shown, that counts and `note_id` dedupe must come from the
queue, that pushes must resolve by scientific name, and that BirdNET-Pi's
sheets are keyed by day.
