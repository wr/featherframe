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

The server answers ahead of time: in every report it says, for each species
whose picture it has already drawn for every screen, exactly what each
screen would be switched to if that species were heard next, and how that
detection would rank. When the front door would wake the server for news,
and every species in the batch has such an answer, it makes the choice
itself by the server's own rule, switches the screens, and leaves a marker
in the queue where the batch ended. The server, at its next real wake,
replays the queue in order and runs its own choice at each marker, at the
marker's time: History, the cursor and every count come out as if it had
been awake. It is still the truth; a marker that disagrees with its own
choice is logged and the server's choice wins.

The glass changes at the same moments as today (the same five-minute
batches), a few seconds sooner (no start). The server starts only for a
species it has not drawn, a species new today, the schedule it names, the
page, and a frame or a setting changing.

## What the server reports: the switch table

`hosted_state()` gains `switches`, built in hosted mode only, after the tick
and its prune (so every file it names is kept):

```json
"switches": {
  "valid_until": 1760241600,
  "blocked": ["house sparrow", "passer domesticus"],
  "floor": 0.0,
  "species": {
    "haemorhous mexicanus": {
      "names": ["house finch", "haemorhous mexicanus"],
      "first_today": false,
      "etag": "b94cd8f752f6dd77",
      "frames": {"E886D8…": {"etag": "6969c3fb…", "file": "frames/drawn/out/…fff",
                              "push": {"etag": "6969c3fb…", "rotation": 90, "power": "awake", "ota": false}}},
      "viewers": {"…": {"name": "…", "file": "frames/drawn/views/…png"}}
    }
  }
}
```

- **Which species.** Every species with a kept sheet (`drawn.kept_sheet`
  under the key `_render_single` would look up for a detection of it heard
  now), whose detection would be shown from that sheet without composing or
  buying anything, and which is not new today (`_is_new_species`: such a
  detection ranks first-ever and may need corroboration, so it always wakes
  the server). The sheet is the one `_render_single` would choose for a
  fresh detection: the same `SingleSpec`, built by one helper both use, with
  the footnote a fresh detection carries (none: a detection clears the
  quiet and outage notes).
- **Every screen or none.** An entry lists every `on` kit and every `on`
  viewer that would show the plates picture after the switch
  (`picture_for(shows, now)`), each with the output it would be served:
  the kit's `out/<etag>-<okey>` for its current settings and its push
  message with the new ETag; the viewer's drawn view. A species with any
  such screen lacking its file has no entry. Frames on the collage are not
  in any entry and are never touched by a switch.
- **`first_today`.** Whether a detection of this species would be its first
  today (`_today_counts` has none of it): the server's novelty, said ahead.
- **`blocked`, `floor`.** The blocklist (lowercased names), and the
  confidence floor for this source (0 for BirdNET-Go, whose own threshold
  wins; `CONFIDENCE_FLOOR` for BirdNET-Pi's Apprise).
- **`valid_until`.** The next local midnight, as an epoch: after it
  `first_today` and a sheet's date-bearing key are stale, so the front door
  decides nothing more until a report. Quiet hours span midnight in most
  households, so this rarely matters.
- Empty (`{}`) whenever the front door must not decide: not hosted, no
  frame shows the plates picture, a source that is not a push feed
  (BirdWeather is left for later; see Not in this change), or quiet hours.

About 25 species at most (`drawn.KEEP_ETAGS`), a few hundred bytes each.

## The choice, in one place twice

The server's rule for a batch is `_pick_showable(by_novelty=True)`: newest
first, skip blocked species, skip a new species not yet corroborated, take
the highest novelty (first-ever 2, first-today 1, repeat 0), the newest
within a class. Its pure core moves into `featherframe/switching.py`:

```python
def choose(batch, table) -> Choice   # "wake" | "same" | species key
```

Given the batch (each push's names, confidence and BirdNET-Go `note_id`, in
arrival order), the table and how many times each species has been heard
since the report:

1. Drop a push below `floor`, a repeat of an earlier push's `note_id`
   (BirdNET-Go sends a detection again as a new species), a blocked
   species, and a push that names no species.
2. If any remaining push names a species with no entry, or BirdNET-Go says
   `days_since_first_seen == 0`: **wake**.
3. Each push ranks 1 if its species' entry is `first_today` and the species
   has been heard exactly once since the report (counting this batch), else
   0. This is `_novelty`'s `count <= 1` with the count kept by the front
   door.
4. Highest rank, newest within a rank. If that is the species shown:
   **same**. Otherwise: that species.

`hosted/src/switch.ts` is its port. Both are held to
`server/tests/fixtures/switch-cases.json`, as `spend.decide` is to
`spend-cases.json`: each case is a table, the species shown, the counts so
far, a batch, and the answer. Step 4's order (highest rank, newest within
it) is one function, `switching.best`, which `_pick_showable` also calls
with `_novelty`'s ranks, so the server's rule and the fixture cannot drift
apart; a test also holds that an entry's `first_today` is what `_novelty`
says of a fresh detection.

"Heard since the report" counts the pushes queued after the server last
took the queue: those are the detections the server's own counts do not
yet include.

## What the front door does

At the moment it would wake the server for news today (`due()` with `news`
set, the alarm):

1. If the server is already running (a page is open), wake it as today: a
   running server costs no start.
2. If the table is empty or past `valid_until`, wake as today.
3. Run `choose` over the pushes queued since the last marker or wake.
   **wake**: wake as today. **same** or a species:
   - for a species, update each listed frame's row (`etag`, `file`, `push`)
     and send the new push message to its sockets; update each listed
     viewer's `name` and `file`; set `unchanged` to `blocked` plus the
     species' names;
   - add the species' heard counts;
   - append the marker to the queue: `INSERT INTO ingest (path, body)` with
     path `/api/hosted/decided` and body `{at, choice, etag}`, so it sits
     after exactly the pushes it decided;
   - mark the page's changing copies stale (`cache.clear("changing")`), so
     the next look at the page wakes the server as a stale copy does today;
   - count it as a wake for the five-minute gap (`wake_ms`), and clear
     `news`.

A marker is written for **same** too: the server's next batch must be the
pushes after it, or it would choose over a different batch than the front
door did.

The queue is replayed in one request instead of one per push: `POST
/api/hosted/ingest` with every row in order (`[{path, body}]`), at most
1 MB a request, then `/api/hosted/run`. On 10 Oct that is 68 requests a day
instead of 464, and under this change one wake may carry a day's pushes.

## What the server does with a marker

`/api/hosted/decided` (hosted only, like every `/api/hosted/` route), met
in the replay after the pushes it covers:

- run the plates part of a tick at the marker's time (`_single_tick(at)`),
  which reads exactly the pushes the front door saw, ranks them by the same
  counts, and commits the kept sheet: History gets the switch at its time,
  the drawn store's recency moves, the cursor passes the batch;
- compare: the picture it now shows against the marker's `choice` and
  `etag`. A mismatch is logged as a warning with both; the server's picture
  stands and its report at the end of the wake puts it on the screens.

The frames' outputs follow at `/api/hosted/run`'s tick, which finds every
one already drawn and points the frames at the last picture chosen. The
report then refreshes the table.

## What the owner sees

- The frames change when they do today, a few seconds sooner.
- History lists every switch at the time it was made, once the server next
  wakes. Until then the webapp's preview and History are the last ones the
  server drew; opening the page wakes it (the cache's copies are stale), as
  it does after any change today.
- Nothing else: no setting, no copy.

## Cost

Replaying 4–10 Oct, wakes for news fall from 64–90 a day to 1–4; scheduled
wakes (the end of quiet hours, the nightly collage), page visits and
settings saves stay. Estimated at 10 Oct's prices: about $0.0058 →
$0.0015–0.002 a household a day; 500 households about $87 → $25–30 a month;
the $5 plan carries about 25–35 households. Container CPU per remaining
wake rises a little (the replay runs a plates tick per marker, ~0.05–0.1
CPU-s each), far less than a start. Measure it as W-1013 was: whole days,
`cost.py` before and after, and the VM's own CPU lines.

## Not in this change

- **BirdWeather.** The front door already reads a station's detections; the
  same table would serve it, but its marker has to carry the station's
  detection id instead of a queue position. Next, if households use it.
- **Serving the preview from the front door.** The page's
  `/api/frames/<id>/preview.png` could follow the switch at once (the
  output's PNG sits beside its `.fff`). Not needed for cost.
- **Self-hosted** is unchanged: there is no front door.

## Risks

- **A wrong choice.** The port is held to a shared fixture and every marker
  is checked by the server; a mismatch costs at most one switch the server
  undoes at its next wake, and never shows a wrong species: an entry is
  that species' own picture.
- **A stale table.** It is rebuilt at every report and expires at local
  midnight. A setting or frame change wakes the server (a POST does today),
  which reports a new one. A kit that reports new facts at check-in gets
  its outputs redrawn on the next wake, as today.
- **A long replay.** A day of pushes and markers in one wake: a few seconds
  of the server's own work, inside `MAX_WORK_S`.

## Tests

- `switch-cases.json`, run by `tests/test_switching.py` and
  `hosted/test/switch.test.ts`: blocked, below the floor, a repeated
  `note_id`, an unknown species, a new species, first-today beats a newer
  repeat, a species heard twice in the batch ranks as a repeat, the shown
  species chosen (same), an empty batch.
- Server: the table lists exactly the species a detection would show from a
  kept sheet, with every on screen's output; none for a missing output, a
  new species, or a frame on the collage; `first_today` equals `_novelty`.
- Server: a replay of pushes and markers gives the same History, cursor and
  picture as ticks at those times.
- Front door (`household`): a batch inside the table switches the rows,
  sends the push, writes the marker and does not start the server; a batch
  outside it wakes; a running server is woken; an expired table wakes.
- After deploy: a day's wakes and cost against 10 Oct.
