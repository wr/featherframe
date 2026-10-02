# The Cloud webapp, drawn ahead (W-946, W-947)

Revised 2 Oct 2026 after an adversarial review (Fable); the changes are
listed at the end.

## Why

Wells, 2 Oct 2026: on Featherframe Cloud the webapp "takes forever to load"
after a while away; opening Firefox's tab switcher on the phone "sends a
verification email"; and an open or background tab should not hold anything
open. The goal: the webapp feels instant, and the server is used as little as
possible.

What happens today:

- **Every view of the webapp starts the server.** `GET /` goes Worker →
  front door (`Household.proxy`) → the household's server Container. After
  30 s idle it is stopped, so a visit after a while waits for a cold start:
  the Container boots, `hosted.HostedLink.pull` fetches the data dir from R2,
  Python imports and opens the database, the service starts its tick, and
  `index()` runs `status()` (two uncached calls to the detection source for
  BirdWeather, 5 s timeout each), four listings and a 295 KB template. Only
  then is there anything on screen. The preview image, the on-load
  `/api/tasks` ask and every status poll go to the server as well. `W-917`
  saw three starts in 25 minutes one night, all from page loads.
- **An open tab keeps the server running.** The page stops asking once it is
  hidden or untouched for 10 minutes (`W-915`), and holds no socket. But while
  it is visible and touched in the last 10 minutes, the status poll (every
  30 s) and the preview refresh (every 15 s) are server requests, so the
  Container never reaches its 30 s idle stop.
- **The stray email is a sign-in link.** Wells's account is verified, so no
  verification email can be sent. D1 holds two sign-in links never used:
  1 Oct 2026 10:22 PM and 2 Oct 2026 7:46 AM (Mac local time). `POST /login`
  answers with the "Check your email" page itself, so that tab's history
  entry is a POST. Most likely Firefox reloads a tab it had unloaded when the
  tab switcher shows it, sends the form again, and `login()` sends another
  link: it never checks whether the browser is already signed in. The setup
  page's "Check your email" (`setupLinkSentPage`) has the same shape and
  sends an "Add a frame" link each time. Post/Redirect/Get fixes it whatever
  the browser does.

## Goals

- `GET /` answers in under 300 ms (p50) on a warm Worker, whether the server
  is running or not, with the household's real content, not placeholders.
- Looking at the webapp, or leaving it open, never starts or holds the server
  (two bounded exceptions below: queued detections in quiet hours, and
  check-ins older than an hour).
- The server runs for detections, schedules, and an owner's edits.
- No email is ever sent without a person asking for it in that moment.
- Signing in on an iPhone or Mac can use the code Mail offers in the keyboard.

Not goals: a client-rendered app, a new design for the webapp, faster cold
starts (still worth doing; a separate ticket), any change to self-hosted
behaviour beyond what one shared code path needs.

## Part 1: sign-in (W-947, ships first)

Worker only, plus one D1 migration (`0010_signin_requests.sql`).

### A sign-in is a request, kept by the browser that made it

A new D1 table, one row per "email me" from a browser:

```sql
CREATE TABLE signin_requests (
  id_hash    TEXT PRIMARY KEY,   -- sha256 of the ff_signin cookie
  email      TEXT NOT NULL,
  kind       TEXT NOT NULL,      -- 'login' | 'setup'
  link_hash  TEXT,               -- login_links.token_hash of its link, or NULL when nothing was sent
  code_hash  TEXT,               -- sha256(id_hash || code), or NULL when nothing was sent
  attempts   INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL    -- created_at + 15 min, as the link
);
CREATE INDEX signin_requests_link ON signin_requests (link_hash);
```

- `POST /login` normalises the email and, unless the rate limit stops it
  (`LOGIN_LINKS_PER_HOUR`, unchanged), makes the link as today and a 6-digit
  code (`crypto.getRandomValues`, uniform 000000–999999). It writes the row,
  sets `ff_signin=<random 32 bytes hex>; Path=/login; Max-Age=900; HttpOnly;
  Secure; SameSite=Lax`, and answers **303 → `/login/code`**. The email goes
  out in `ctx.waitUntil`, after the answer, as `setup.ts` already sends its
  own.
- **The same answer for every address.** An uninvited address, and an
  address over the rate limit, get the same cookie, a row with `link_hash`
  and `code_hash` NULL, the same redirect, and nothing sent (an uninvited
  one joins the waitlist as today). Because the send is never awaited, the
  timing is the same too. A NULL row answers every code exactly as a wrong
  one: the attempt is counted, the same words, "Too many tries" at 5, the
  same expiry.
- `POST /setup/<code>/<token>` for an address that already has an account
  does the same with `kind = 'setup'`; the link keeps its `pair_code`, so the
  code adds the frame too. An address with no invitation gets the uniform row
  and redirect. Its own errors ("Enter an email address.") still answer with
  the form, as they send nothing.
- `POST /login` with a live session sends nothing and answers **303 → `/`**.
  (The setup form, signed in, keeps its existing branch: it adds the frame to
  that account and sends nothing.)
- `GET /login` and `GET /login/code` with a live session: **303 → `/`**.
- `POST /login`, `POST /login/code` and `POST /login/resend` refuse a foreign
  `Origin` (403), as `pair`, `typedCode` and `resendVerification` do.

### The code page: `GET /login/code`

Found by the cookie; no address in the URL. With no cookie, or an expired
row, it is `/login`. Copy (kind `login`):

> **Check your email**
>
> If {email} has an invitation or an account, we sent it a code.
>
> Code `[      ]` **Sign in**
>
> The code and the link in the email work once, for 15 minutes.
>
> Send a new code · Use a different email · Help

Kind `setup` reads "If {email} has a Featherframe Cloud account, we sent it
a code that adds this frame.", the button is **Add this frame**, and the
existing waitlist paragraph follows ("Built this frame yourself? …").

The field: `inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}"
maxlength="6"`, autofocused. Six digits typed or pasted submit the form at
once. "Use a different email" is `/login` (kind `setup`: the setup page).
"Help" is `featherframe.app/help/account`.

While the page is open it asks `GET /login/state` (Worker only: `{signedIn}`
from the session cookie, one D1 read) when the tab is shown again or gains
focus, never on a timer. Signed in, it goes to `/`. So a "Check your email"
tab left behind when the link opened in a new tab becomes the webapp, rather
than a page a reload could resend.

### Checking a code: `POST /login/code`

- Needs the cookie and a live row. Wrong (or a NULL code): `attempts + 1`,
  **303 → `/login/code?e=wrong`** ("That code doesn't match."). At 5 tries
  the row is dead: "Too many tries. Send a new code." Expired: "That code has
  expired. Send a new code."
- Right: the row is deleted, its link marked used, and the sign-in finishes
  exactly as the link does: one function, `finishSignIn(linkRow)`, factored
  out of `auth()` (makes the household on an invitation's first use, marks
  the address verified, claims a setup link's frame, starts the session).
  `ff_signin` is cleared.
- The link in the email keeps working; using it deletes the request
  (`DELETE FROM signin_requests WHERE link_hash = ?`), so its code dies too.
- A code is bound to the browser that asked for it: without that cookie
  there is nothing to guess against. With it, 5 tries per request, and
  `RL_AUTH`'s 10 a minute per IP.

### Send a new code: `POST /login/resend`

Same row (found by cookie): a new code and a new link, the old code dead, the
old email's link still good until it expires. Counts against the same
per-address limit; over it, nothing is sent and the page says "Already sent
a few times. Check your email, or try again in an hour." Answers 303 →
`/login/code?sent=1` ("Sent a new code.").

### Rate limits

`limiterFor` changes: `GET /login/code` and `GET /login/state` count against
`RL_PAGE`, as `GET /login` does today; the three POSTs stay `RL_AUTH`. A test
holds the table.

### The emails

Sign in (`signInEmail(link, code)`):

- Subject: `Your Featherframe sign-in code: 123456`
- Body: "Your sign-in code is **123456**. Or sign in with this link:
  {link} The code and the link work once, for 15 minutes. If you didn't ask
  to sign in, ignore this email."

Add a frame (`addFrameEmail(link, frame, code)`):

- Subject: `Your code to add a frame to Featherframe: 123456`
- Body: "Someone scanned the code on a frame ({frame}) and asked to add it to
  your account. To add it and sign in, enter the code **123456**, or use this
  link: {link} The code and the link work once, for 15 minutes. If this
  wasn't you, ignore this email."

The code sits beside the word "code" in the body's first line, and in the
subject so it can be read from a notification. Whether the keyboard offers it
is checked on Wells's iPhone and Mac (Mail, Safari) before shipping; nothing
here depends on it.

### The sign-in page

The second form goes. Under the email form, one muted line: a link,
**Set up a new frame**, to `GET /setup`, which today redirects to `/login`.
It becomes the code form that was on the sign-in page, on its own page:

> **Set up a new frame**
>
> Enter the code on your frame's screen.
>
> Code `[ABC-DEF]` **Continue**
>
> Sign in

The sign-in button becomes **Email me a code** (it sends both). `POST /setup`
is unchanged, but its errors answer with this page rather than the sign-in
page.

## Part 2: the webapp drawn ahead (W-946)

### The idea

The server already draws what frames and viewers need ahead of time and
leaves it with the front door, which answers them while the server sleeps
(`hosted_state()`, `ensure_view`). The webapp becomes one more thing drawn
ahead. When the server settles and something the page shows has changed, it
writes the page, its JSON, and the images the page shows into the data dir;
the push takes them to R2, and the state report tells the front door which
GET answers from which file. The front door then answers every read the
webapp makes from R2. Only edits and work in progress reach the server.

### What the server writes (`featherframe/page_snapshot.py`, hosted only)

`write(service, data_dir, force) -> dict`, called by `settle` after the
check-ins are applied and before `hosted_state()` and `push()`. Under
`data/page/`:

| File | What | Answers |
|---|---|---|
| `page/index.html` | `index.html` rendered with the same context `GET /` uses (one function, `app.page_context(svc)`, shared by both), the account left out (below) | `/` |
| `page/status.json` | `/api/status`'s body | `/api/status` |
| `page/history.json` | `/api/history`'s body | `/api/history` |
| `page/tasks.json` | `/api/tasks`'s body | `/api/tasks` (the on-load ask) |
| `page/battery/<id>.json` | `/api/battery?hours=24&frame=<id>` for each kit | the battery hover |

Plus routes to files already in the data dir:

| Route | File |
|---|---|
| `/api/frames/<id>/preview.png` | a kit's `frames/out/<id>.png`; a viewer's upright preview, which `write` draws ahead into `frames/views/` through `view_png` (cached as today, so drawn only when the picture changes) |
| `/api/preview.png` | the shown picture's `sheet.png` |
| `/api/history/<etag>.png`, `.jpg` | `frames/history/<etag>.png`, `.jpg` |
| `/api/generated/<slug>.png` (and `thumb=1`) | `generated/<slug>.png` (and its thumbnail) |
| `/api/collages/<day>.png` (and `thumb=1`) | `frames/collage-days/<day>.png` (and its thumbnail), with the download's file name |

A thumbnail is routed only if it exists; `write` draws at most 4 missing ones
per call (each may fetch a lazy full-size file), and a missing one is asked
of the server as today.

`write` returns the route table, which `hosted_state()` carries as
`state["page"] = {"build": …, "routes": {…}}`. Each route names a file in the
data dir and the query keys that matter to it (`thumb`; `frame` and `hours`
for the battery; none for the rest). Lazy files (`W-915`) qualify: they are
in R2 even when the Container never pulled them. `page/` is push-only: added
to `_LAZY`'s rule so a start never pulls it, and `write` removes the page
files it no longer writes (a removed frame's battery file) through
`hosted.remove`, which reaches the ones never pulled.

`_VIEWS_MAX` stops pruning any view a viewer row or a route names: a
viewer's served image and its upright preview can no longer push each other
out.

`build` is the first 12 hex of the sha256 of the template file and the page's
static JS. It changes only when the page's own code changes. The Worker
ships the same hash (`PAGE_BUILD`, computed from the same files by
`hosted/scripts/page-build.mjs` at deploy); a test holds the two to the same
answer.

### When the server writes it

The render is the cost, so it happens only when the page would change:

- `write` first builds the page's context and hashes it (status, history,
  tasks, listings, config, spend, build). If the hash matches the last
  written snapshot's, it writes nothing and returns the routes it has.
- `status()` keeps its own answers from the detection source (`latest`,
  `available`, `all_time_species_count`) for 60 s, dropped whenever a tick
  takes in a new detection, so building the context after a quiet tick asks
  BirdWeather nothing and one after a new detection shows it. The tick asks
  the source as today.
- A settle after a request that changed something (the `_hosted_settle`
  middleware, including a wake's `POST /api/hosted/run`) calls it with
  `force` (no throttle). A scheduler tick's settle calls it at most once a
  minute: while the server is up for an edit, a push source ticks every 5 s.
  The shutdown settle does not call it.
- The middleware's settle, and so `write`, runs inside `service._working()`,
  as the tick's already does, so the Container is not stopped part way
  (`W-917`).

What that leaves: one context build per settle (no source calls) and one
render, about 300 KB pushed, only when the page changed. Measure the render
at `docker --cpus=0.25` before shipping and record it in the PR.

### The front door answers reads

`Household.takeState` stores `state.page` in a new DO SQL table
`page_routes (path PRIMARY KEY, keys, file, type, cache, filename)`
(replaced whole, like `frames` and `viewers`), `page_build` in meta, and
`state_at` (now) in meta.

In `Household.fetch`, before `proxy`: a signed-in page's `GET`/`HEAD` whose
path is in `page_routes` (the route's own query keys must match; other query
keys, the page's cache-busters among them, are ignored), whose file is in
`files`, and which does not carry `live=1`, is answered from R2:

- `/` and `/api/status` (and the other `page/*.json`): `Cache-Control:
  no-store`, no ETag. They carry what the front door adds per request.
- Images: `ETag` = the file's sha from `files`, and `If-None-Match` answers
  304 without reading R2; `Cache-Control` as the server sends them today.
- Not counted as page activity (`page_ms`): it does not keep the server up.

Anything else is proxied as today and starts the server: every POST, and
every GET with no route. A read that must be live adds `live=1`.

A snapshot is not used, and `/` is answered as if there were none (below),
when its `page_build` is not the Worker's `PAGE_BUILD` (a deploy changed the
page), or after a proxied POST whose settle did not report (`state_at` older
than the request): the front door then proxies the next `/` live, once, to
the server that just answered the POST.

**The account** is added to every `/` at serve time, with `HTMLRewriter` in
the front door, from the headers the Worker already sends
(`X-FF-Account-Email`, `-Pending`, `-Unverified`). On hosted the template no
longer renders the account at all: the General section's email row and the
"Confirm your email address" banner are `data-account` elements left empty
and hidden, in the snapshot and in a proxied `/` alike, and the front door
fills the text, the `value` and `data-value` attributes, the "Unconfirmed"
badge, the hint and the banner's `hidden`. Self-hosted renders its own from
`Config.owner_email`, as today.

**`/api/status`** gets `"heard": {<frame id>: epoch ms}` from the front
door's `seen` table (ids compared upper-cased: the server upper-cases a
viewer's), with `Date.now()` for a kit whose push socket is open right now
(`ctx.getWebSockets(id)`).

### Frame health stays right between wakes

A frame checks in with the front door, not the server, so a snapshot's
"Last checked in" and its Overdue dot would age. The rule stays in Python;
the server says it as a timeline the page can read. Each frame in
`status.json` gains:

- `heard_at`: epoch seconds of the check-in the server last applied, or null
- `overdue_after_s`: seconds after a check-in at which `frame_card` calls it
  overdue (`expected * 60`, from that check-in's `told_s`), or null where it
  never is (a page)
- `live_for_s`: seconds after a check-in it still counts as live (a page:
  `_PAGE_OPEN_SECONDS`), or null for always
- `critical`: `card.battery_critical`

The page's one function `frameState(fr, heardAt, now)` returns `bad` if
critical, else `warn` if `overdue_after_s` has passed, else `good` if live,
else `off`. `heardAt` is the later of `heard_at` and the front door's
`heard[id]`. "4 min ago" and "never" come from the same `heardAt`. Python's
`frame_health` stays the one place the parameters are decided; a test holds
its `state` and the timeline evaluated at the same moment to the same answer
for every `frame_card` case. AGENTS.md's "no TS copy of any rule" line is
amended in the same PR to name these page functions and the fixtures that
hold them.

### Telemetry the front door is holding

Battery, Wi-Fi and firmware readings ride on check-ins, which the front door
queues (`checkins`) until a wake applies them. Opening `/` while `checkins`
holds anything and the snapshot is more than an hour old asks for a wake
(below). So readings on the page are at most an hour behind, at the cost of
at most one wake an hour from page views.

### Detections queued in quiet hours

In quiet hours a push waits in the front door's `ingest` queue without waking
the server, so "Last detection" would be from before. Opening `/` while
`ingest` holds anything asks for a wake. Outside quiet hours news already
wakes the server within 5 minutes.

**Asking for a wake** is always the alarm's way, never inside the request:
`news = 1` and an alarm 500 ms out, as `adopt()` does, subject to
`MIN_GAP_MS` and `suspended`. Two tabs asking at once set the same alarm.

### Relative times move to the browser

Every relative time on the page ("just now", "7 min ago", "yesterday", "24
Sep") is rendered by one JS function from an ISO time the server puts in a
`<time datetime>` (and in the JSON beside each `*_text`): last detection,
pending, quiet and outage "since", history, a frame's last check-in. It
follows `_ago` and STYLE.md exactly. Countdowns become absolute too: a
frame's "Refreshes in N min" reads `queued_until` (epoch), and a pending
species its `expires_at`, so neither jumps back when a snapshot's status is
applied again. The `*_text` and `queued_s` fields stay for the box's older
pages and are no longer read. A shared fixture file
(`server/tests/fixtures/page-time-cases.json`) holds both: pytest runs
`_ago`, and a vitest in `hosted/test` runs the page function, kept in
`server/static/js/page-time.js`, which the page loads (served by a route
like the rest, and by the box from `static/`).

### The page asks less, and only the front door

- On load it applies `/api/status` once at once (tens of milliseconds from
  the front door), so dots and times are current before anyone looks. The
  on-load `/api/tasks` ask is answered by `page/tasks.json`; once a task is
  started, its 4 s poll asks `?live=1`, as does the regenerate poll.
- The preview no longer reloads every 15 s: it reloads when the status says
  that frame's picture changed (`fr.etag`, already in the list). The
  welcome sheet's `?w=` retry is a cache-buster like any other.
- The status poll stays at 30 s, still stops when hidden or after 10 minutes
  untouched, and asks at once when the owner comes back. It no longer
  touches the server at all.
- While a firmware update is moving, its 2 s poll asks `/api/status?live=1`.
- A page whose own `build` differs from the status's reloads once, after any
  save in progress, so a tab open across a deploy takes the new page.

### Edits

A save still needs the server. Keeping the cold start out of sight:

- **Warm on intent.** On hosted, the first `input`, `change` or `focusin` on
  a settings or frame field, or opening a dialog (Pair a frame, USB firmware
  update), sends `POST /api/warm`. More edits send it again at most every
  20 s. The front door answers 204 at once and, unless the household is
  suspended, starts the server in `ctx.waitUntil` (the same `server()` stub,
  asking `/api/hosted/busy`), counting it as page activity. It hands over
  nothing and runs no tick of its own: the server's own first tick and its
  settle apply the queued check-ins. The Container stops on its own 30 s
  after the last warm or save (`sleepAfter`, `sleepWhenIdle`). Opening a
  section to read it warms nothing.
- **Switches don't wait.** A switch saves the moment it is flipped, so its
  save can meet a cold server. It flips at once and stays flipped; its tick
  reads "Saving…" until "Saved" or "Not saved" (then it flips back, as
  today). The request is sent with `keepalive`, so leaving the page does not
  drop it.
- **Pending states.** A section's **Save** and a frame row's **Save** read
  "Saving…" and are disabled until the answer.
- **After a save** the page shows the new snapshot: `settle` runs in the
  request (the middleware) before the response leaves, so the redirect after
  `POST /settings` lands on a page written after the save. If that settle
  failed, the front door serves that one `/` live (above).

### Before there is a snapshot

A household with no usable snapshot (none yet: a brand-new one, whose setup
redirects to `/?welcome=1` while its first wake runs; or one from before a
deploy that changed the page) is answered `/` by a page bundled in the
Worker: the webapp's header, the two columns and their cards as gray
skeleton blocks, no text. It asks for a wake (the alarm's way, above) and
asks `GET /api/page/ready` (front door only: `{ready}`) every 3 s, then
reloads with its own query and hash kept (`?welcome=1`, `?paired=1` and the
save flashes are read by the page). The preview area shows the same skeleton
until its image loads, on every page.

### What runs the server, before and after

| Event | Today | After |
|---|---|---|
| Open the webapp | starts it; held 30 s+ | nothing (quiet hours with detections queued, or check-ins queued and the snapshot over an hour old: one wake) |
| Tab open, visible, untouched under 10 min | held up the whole time | nothing |
| Hover a battery cell | starts it | nothing |
| Start editing | already up from the visit | starts it, held until 30 s after the last edit |
| Save | proxied | proxied (usually warm by then) |
| Open the USB dialog or an example image | proxied | proxied (static files; bundling them is a follow-up) |
| Detection, schedule, daily check | wakes as today | the same, plus a snapshot when the page changed |

### Self-hosted

No snapshot is written and nothing is routed. The page's code path is the one
above: times and health from the timeline, the preview reloading on its
etag, the status applied on load. The box's server answers every request as
today.

## Testing

- **pytest.** `page_snapshot.write`: writes the page files, routes every
  previewed frame (kit and viewer), history thumbnails and full sizes,
  generated and collage-day thumbnails, with their query keys, and nothing
  for a file that does not exist; skips the render when the context hash is
  unchanged; at most once a minute unforced; never prunes a named view. On
  hosted the account elements are empty in every render; self-hosted fills
  them from config. `hosted_state()` carries `page`. `page/` is never
  pulled. The middleware settle runs inside `_working()`. The source memo.
  Health timeline vs `frame_health` for every card case. `_ago` against the
  shared fixture. `build` against the Worker's.
- **vitest (hosted).** A routed GET is answered from R2 without a Container
  fetch (the stub throws if called); an image's 304; `/` and `/api/status`
  are `no-store` and carry the account and `heard`; a missing file, an
  unknown query key that matters, or `live=1` falls through to the server; a
  build mismatch or a POST whose settle did not report serves `/` live once;
  `/api/warm` starts the server and answers at once, and not for a suspended
  household; an open `/` with queued ingest in quiet hours, or queued
  check-ins and an hour-old snapshot, sets the alarm; no snapshot serves the
  skeleton page; `/api/page/ready`. The page time function against the
  shared fixture.
- **vitest (sign-in).** POST /login redirects and sets the cookie, and sends
  in `waitUntil`; the code page from the cookie; right code signs in
  (household made on an invitation's first use, setup kind claims the
  frame); wrong code counted, 5 kill it; a NULL row answers as wrong;
  expired; another browser's cookie fails; link and code use each other up;
  resend; uninvited and rate-limited answer the same as invited; foreign
  Origin refused; POST and GET /login while signed in go to `/`;
  `/login/state`; `GET /setup` shows the code form; `limiterFor`'s table.
- **On Cloud.** A test household signed in with the admin API's link:
  `curl -w` time to first byte for `/` with the server stopped, before and
  after (target under 300 ms); the admin page's server time for that
  household over an hour with a tab open (target: none from the tab). Wells
  on the phone: the Firefox tab switcher sends no email; whether an iPhone
  offers the code from Mail.

## Rollout

1. W-947 (sign-in): migration `0010`, then `wrangler deploy`. Worker only.
2. W-946 (page): server image and Worker in one `wrangler deploy`. Each
   household shows the skeleton page on its first visit, which asks for the
   wake that writes its snapshot. Wells's household is woken by hand right
   after deploy.

## Risks

- **A stale page for a few minutes.** A snapshot is as old as the last
  settle that changed it. The front door adds what it knows newer
  (check-ins, open sockets); queued detections in quiet hours and readings
  over an hour old ask for a wake; a save is followed by its own snapshot.
- **Old page code after a deploy.** Caught by `PAGE_BUILD`: the old snapshot
  is not served, and an open tab reloads on its next status poll.
- **Snapshot size.** `index.html` is about 300 KB uncompressed, pushed only
  when the page changed.
- **The account is filled by the front door on hosted.** A `/` that somehow
  bypassed it would show an empty email row, never someone else's.
- **A page file and its status pushed a moment apart.** `push()` writes
  files one by one before `report()`: a read in between can pair a new page
  with the previous status. The page applies the status again within 30 s.

## Changes after review

From Fable's adversarial review, 2 Oct 2026; each checked against the code.

- The on-load `/api/tasks` ask would still have started the server: now
  answered by `page/tasks.json`; task polls ask `live=1`.
- An ETag from the file's sha would have let the browser keep a cached body
  and miss everything added per request: `/` and the JSON are `no-store`.
- The snapshot's cost was underestimated (`status()` asks BirdWeather twice
  uncached; three settles per wake; a 5 s tick on push sources while up): a
  context hash skips unchanged renders, a 60 s memo in `status()`, a once-a-minute
  throttle for ticks, no render on shutdown.
- The middleware settle ran outside `_working()`: now inside.
- Viewer previews could prune a served viewer image (`_VIEWS_MAX`): named
  views are never pruned.
- Enumeration: a NULL row answers as a wrong code; the email is sent in
  `waitUntil` so timing does not depend on the address; Origin checks.
- A switch's save meets the cold start: switches no longer wait on it.
- `?w=` is a cache-buster too: routes now name the query keys that matter
  instead of dropping a list.
- Queued check-ins (battery, Wi-Fi, firmware) went unseen for up to a day:
  an hour-old snapshot with queued check-ins asks for a wake.
- A warm ignored `suspended`: now checked.
- Skeleton waits ask for a wake by alarm, never in the request, and keep the
  query on reload.
- `page_build` compared two things that always agree: now the Worker's own
  `PAGE_BUILD`.
- AGENTS.md's TS-copy invariant is amended explicitly.
- `limiterFor` changes, not cited; Apple Mail's autofill is tested, not
  claimed; W-917's evidence quoted exactly.
- A failed settle after a save would land on the old page: the next `/` is
  served live once.
- Countdowns and expiries become absolute times.

Considered and not taken: shipping only Post/Redirect/Get for sign-in (Wells
asked for the code); having the front door copy the server's live answers at
the end of a wake instead of a snapshot module (it still needs the server to
list its image routes and the account placeholders, and it misses changes
made while the server is up between wakes; the context hash gives the same
render count).
