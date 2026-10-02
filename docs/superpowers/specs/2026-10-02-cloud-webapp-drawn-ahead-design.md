# The Cloud webapp, drawn ahead (W-946, W-947)

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
  `index()` runs `status()` (which asks the detection source, a 5 s-timeout
  HTTP call for BirdWeather), four listings and a 295 KB template. Only then
  is there anything on screen. The preview image (`/api/frames/<id>/preview.png`)
  and every status poll go to the server as well. `W-917` found that page
  loads caused most overnight starts.
- **An open tab keeps the server running.** The page stops asking once it is
  hidden or untouched for 10 minutes (`W-915`), and holds no socket. But while
  it is visible and touched in the last 10 minutes, the status poll (every
  30 s) and the preview refresh (every 15 s) are server requests, so the
  Container never reaches its 30 s idle stop.
- **The stray email is a sign-in link.** Wells's account is verified, so no
  verification email can be sent. D1 holds two sign-in links never used:
  1 Oct 2026 10:22 PM and 2 Oct 2026 7:46 AM (Mac local time). `POST /login`
  answers with the "Check your email" page itself, so that tab's history
  entry is a POST. When Firefox reloads a tab it had unloaded (opening the tab
  switcher does this), it sends the form again, and `login()` sends another
  link. It never checks whether the browser is already signed in. The
  setup page's "Check your email" (`setupLinkSentPage`) has the same flaw and
  sends an "Add a frame" link each time.

## Goals

- `GET /` answers in under 300 ms (p50) on a warm Worker, whether the server
  is running or not, with the household's real content, not placeholders.
- Looking at the webapp, or leaving it open, never starts or holds the server.
- The server runs for detections, schedules, and an owner's edits only.
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
```

- `POST /login` normalises the email and, unless the rate limit stops it
  (`LOGIN_LINKS_PER_HOUR`, unchanged), makes the link as today and a 6-digit
  code (`crypto.getRandomValues`, uniform 000000–999999). It writes the row,
  sets `ff_signin=<random 32 bytes hex>; Path=/login; Max-Age=900; HttpOnly;
  Secure; SameSite=Lax`, emails the link and the code together, and answers
  **303 → `/login/code`**. An uninvited address gets the same cookie, a row
  with `link_hash` and `code_hash` NULL, the same redirect, and no email (it
  joins the waitlist as today). The page and timing look the same for both.
- `POST /setup/<code>/<token>` for an address that already has an account
  does the same with `kind = 'setup'`; the link keeps its `pair_code`, so the
  code adds the frame too. An address with no invitation gets the uniform row
  and redirect. Its own errors ("Enter an email address.") still answer with
  the form, as they send nothing.
- `POST /login` with a live session sends nothing and answers **303 → `/`**.
  (The setup form, signed in, keeps its existing branch: it adds the frame to
  that account and sends nothing.)
- `GET /login` and `GET /login/code` with a live session: **303 → `/`**.

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

- Needs the cookie and a live row with `code_hash`. Wrong: `attempts + 1`,
  **303 → `/login/code?e=wrong`** ("That code doesn't match."). At 5 wrong
  tries the row's code is dead: "Too many tries. Send a new code." Expired:
  "That code has expired. Send a new code."
- Right: the row is used (deleted), its link is marked used, and the
  sign-in finishes exactly as the link does: one function,
  `finishSignIn(linkRow)`, factored out of `auth()` (makes the household on
  an invitation's first use, marks the address verified, claims a setup
  link's frame, starts the session). `ff_signin` is cleared.
- The link in the email keeps working; using it marks the request's code
  used too (`DELETE FROM signin_requests WHERE link_hash = ?`).
- Counted against `RL_AUTH` (10 a minute per IP) like `POST /login`, plus
  the 5-tries cap per request. `GET /login/code` and `GET /login/state` count
  against `RL_PAGE`, as `GET /login` does (`limiterFor`). A code is bound to the browser that asked for it:
  without that cookie, guessing is impossible, not just slow.

### Send a new code: `POST /login/resend`

Same row (found by cookie): a new code and a new link, the old code dead, the
old email's link still good until it expires. Counts against the same
per-address limit; over it, nothing is sent and the page says "Already sent
a few times. Check your email, or try again in an hour." Answers 303 →
`/login/code?sent=1` ("Sent a new code.").

### The emails

Sign in (`signInEmail(link, code)`):

- Subject: `Your Featherframe sign-in code: 123456`
- Body: "Your sign-in code is **123456**. Or sign in with this link:
  {link} The code and the link work once, for 15 minutes. If you didn't ask
  to sign in, ignore this email."

Add a frame (`addFrameEmail(link, frame, code)`):

- Subject: `Your code to add a frame to Featherframe: 123456`
- Body: "Someone scanned the code on a frame ({frame}) and asked to add it to
  your account. To add it and sign in, enter **123456**, or use this link:
  {link} The code and the link work once, for 15 minutes. If this wasn't
  you, ignore this email."

The code is in the subject and set alone in the first line: that is what
Apple Mail's code detection reads, which the keyboard then offers in a field
marked `one-time-code` (iOS 17 and macOS 14 and later).

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
ahead. Whenever the server settles (after each tick, and after each request
that changed something, `HostedLink.settle`) it writes the page, its JSON,
and the images the page shows into the data dir. The push takes them to R2,
and the state report tells the front door which GET answers from which file.
The front door then answers every read the webapp makes from R2. Only edits
and work in progress reach the server.

### What the server writes (`featherframe/page_snapshot.py`, hosted only)

`write(service, data_dir) -> dict`, called by `settle` between
`hosted_state()` and `push()`. Under `data/page/`:

| File | What | Answers |
|---|---|---|
| `page/index.html` | `index.html` rendered with the same context `GET /` uses (one function, `app.page_context(svc)`, shared by both), the account's fields left as marked empty elements (below) | `/` |
| `page/status.json` | `/api/status`'s body | `/api/status` |
| `page/history.json` | `/api/history`'s body | `/api/history` |
| `page/battery/<id>.json` | `/api/battery?hours=24&frame=<id>` for each kit | the battery hover |

Plus routes to files already in the data dir:

| Route | File |
|---|---|
| `/api/frames/<id>/preview.png` | a kit's `frames/out/<id>.png`; a viewer's upright preview, which `write` now draws ahead into `frames/views/` through `view_png` (cached as today, so it is drawn only when the picture changes) |
| `/api/preview.png` | the shown picture's `sheet.png` |
| `/api/history/<etag>.png`, `.jpg` | `frames/history/<etag>.png`, `.jpg` |
| `/api/generated/<slug>.png` (and `?thumb=1`) | `generated/<slug>.png` (and its thumbnail, which `write` draws if missing) |
| `/api/collages/<day>.png` (and `?thumb=1`) | `frames/collage-days/<day>.png` (and its thumbnail), with the download's file name |

`write` returns the route table, which `hosted_state()` carries as
`state["page"] = {"build": …, "routes": {key: {file, type, cache, filename?}}}`.
Each route names a file in the data dir. Lazy files (`W-915`) qualify: they
are in R2 even when the Container never pulled them. `page/` itself is
push-only: added to `_LAZY`'s rule so a start never pulls it, and `write`
removes the page files it no longer writes (a removed frame's battery file)
through `hosted.remove`, which reaches the ones never pulled.

A route's key is its path, plus its query with `t` and `v` dropped (the
page's cache-busters) and the rest sorted: `/api/battery?frame=ABC&hours=24`,
`/api/generated/x.png?thumb=1`. For `/` the whole query is dropped: it only
carries the page's own flash messages (`?saved=1`).

`build` is the first 12 hex of the sha256 of the template file and the page's
static JS. It changes only when the page's own code changes.

What the snapshot costs the server: one `status()` (the tick has just made
the same calls, so the source's caches answer) and one template render per
settle. The push sends only files whose sha changed. With relative times
moved to the browser (below), the HTML changes only when its content does.
Measure the render at `--cpus=0.25` before shipping; if it costs more than
0.5 s, render only when the status JSON's hash changed or a request changed
something.

### The front door answers reads

`Household.takeState` stores `state.page` in a new DO SQL table
`page_routes (key PRIMARY KEY, file, type, cache, filename)` (replaced whole,
like `frames` and `viewers`) and `page_build` in meta.

In `Household.fetch`, before `proxy`: a signed-in page's `GET`/`HEAD` whose
key is in `page_routes`, whose file is in `files`, and which does not carry
`live=1`, is answered from R2:

- `ETag` = the file's sha from `files`; `If-None-Match` answers 304 without
  reading R2.
- `Cache-Control` from the route (`no-cache` for the page and its JSON, the
  image routes' own `max-age` as the server sends them today).
- Not counted as page activity (`page_ms`): it does not keep the server up.

Anything else is proxied as today and starts the server: every POST, and
every GET whose key is not in the table. A read that must be live adds
`live=1`, which makes its key one the table never holds.

`/` gets the account added at serve time, with `HTMLRewriter` in the front
door, from the headers the Worker already sends (`X-FF-Account-Email`,
`-Pending`, `-Unverified`). On hosted the template no longer renders the
account at all: the General section's email row and the "Confirm your email
address" banner are `data-account` elements left empty and hidden, in the
snapshot and in a proxied `/` alike, and the front door fills the text, the
`value` and `data-value` attributes, the "Unconfirmed" badge, the hint and
the banner's `hidden` on every `/` it answers. So on hosted one place renders
the account; self-hosted renders its own from `Config.owner_email`, as today.

`/api/status` gets the newest check-ins too: the front door adds
`"heard": {<frame id>: epoch ms}` from its `seen` table (ids compared
upper-cased: the server upper-cases a viewer's), with `Date.now()` for
a kit whose push socket is open right now (`ctx.getWebSockets(id)`), and
`"page_build"`.

### Frame health stays right between wakes

A frame checks in with the front door, not the server, so a snapshot's
"Last checked in" and its Overdue dot would age. The rule stays in Python;
the server says it as a timeline the page can read. Each frame in
`status.json` gains:

- `heard_at`: epoch seconds of the check-in the server last applied, or null
- `overdue_after_s`: seconds after a check-in at which `frame_card` calls it
  overdue (`expected * 60`), or null where it never is (a page)
- `live_for_s`: seconds after a check-in it still counts as live (a page:
  `_PAGE_OPEN_SECONDS`), or null for always
- `critical`: `card.battery_critical`

The page's one function `frameState(fr, heardAt, now)` returns `bad` if
critical, else `warn` if `overdue_after_s` has passed, else `good` if live,
else `off`. `heardAt` is the later of `heard_at` and the front door's
`heard[id]`. The texts "4 min ago" and "never" come from the same `heardAt`.
Python's `frame_health` stays the one place the parameters are decided. A
test holds Python's `state` and the timeline evaluated at the same moment to
the same answer for every `frame_card` case.

The battery reading stays as of the server's last wake: it moves over hours,
and the server wakes at least daily.

### Relative times move to the browser

Every relative time on the page ("just now", "7 min ago", "yesterday", "24
Sep") is rendered by one JS function from an ISO time the server puts in a
`<time datetime>` (and in the JSON beside each `*_text`): last detection,
pending, quiet and outage "since", history, a frame's last check-in. It
follows `_ago` and STYLE.md exactly; the `*_text` fields stay for the box's
older pages and are no longer read. A shared fixture file
(`server/tests/fixtures/page-time-cases.json`) holds both: pytest runs
`_ago`, and a vitest in `hosted/test` runs the page function, extracted to
`server/static/js/page-time.js`, which the page loads (a route like the rest).

### The page asks less, and only the front door

- On load it applies `/api/status` once at once (from the front door, tens
  of milliseconds), so dots and times are current before anyone looks.
- The preview no longer reloads every 15 s: it reloads when the status says
  that frame's picture changed (`fr.etag`, already in the list).
- The status poll stays at 30 s, still stops when hidden or after 10 minutes
  untouched, and starts at once when the owner comes back. It no longer
  touches the server at all.
- While a firmware update is moving, its 2 s poll asks `/api/status?live=1`
  (the server is up for it anyway). The task and regenerate pollers already
  ask routes the snapshot does not hold.
- A page whose `page_build` differs from the status's reloads once, after any
  save in progress, so a tab open across a deploy takes the new page.

### Edits: the server is warmed when the owner starts one

A save still needs the server. To keep the cold start out of sight:

- **Warm on intent.** On hosted, the first `input`, `change` or `focusin` on
  a settings or frame field, or opening a dialog (Pair a frame, USB firmware
  update), sends `POST /api/warm`. More edits send it again at most every
  20 s. The front door answers 204 at once and starts the server in the
  background (`ctx.waitUntil(stub.fetch("http://server/api/hosted/busy"))`),
  counting it as page activity. The Container stops on its own 30 s after
  the last warm or save (`sleepAfter`, `sleepWhenIdle`). Opening a section to
  read it warms nothing.
- **Pending states.** Every control that saves shows it is saving until the
  answer: a section's **Save** and a frame row's **Save** read "Saving…" and
  are disabled; a switch keeps its new position with the existing tick
  reading "Saving…" until "Saved" or "Not saved". These already exist for
  some controls; all get them.
- **After a save** the page shows the new snapshot: `settle` runs in the
  request (the `_hosted_settle` middleware) before the response leaves, so
  the redirect after `POST /settings` lands on a page written after the save.

### Detections queued in quiet hours

In quiet hours a push waits in the front door's `ingest` queue without waking
the server, so the snapshot's "Last detection" would be from before. Opening
`/` while `ingest` holds anything asks the front door for one wake, subject
to `MIN_GAP_MS` as any other. The page takes the new status on its next
poll. Outside quiet hours news already wakes the server within 5 minutes.

### Before there is a snapshot

A household whose server has not settled since this ships (or a brand-new
one: setup redirects to `/?welcome=1` while its first wake is still running)
has no `/` route. The front door then answers `/` with a page bundled in the
Worker: the webapp's header, the two columns and their cards as gray skeleton
blocks, no text. It asks for a wake and asks `GET /api/page/ready` (front
door only: `{ready}`) every 3 s, and reloads once there is a snapshot. The
preview area shows the same skeleton until its image loads, on every page.

### What runs the server, before and after

| Event | Today | After |
|---|---|---|
| Open the webapp | starts it; held 30 s+ | nothing (quiet hours with detections queued: one wake) |
| Tab open, visible, untouched under 10 min | held up the whole time | nothing |
| Hover a battery cell | starts it | nothing |
| Start editing | already up from the visit | starts it, held until 30 s after the last edit |
| Save | proxied | proxied (warm by then) |
| Detection, schedule, daily check | wakes as today | the same, plus writing the snapshot |

### Self-hosted

No snapshot is written and nothing is routed. The page's code path is the one
above: times and health from the timeline, the preview reloading on its
etag, the status applied on load. The box's server answers every request as
today.

## Testing

- **pytest.** `page_snapshot.write`: writes the four page files, routes every
  previewed frame (kit and viewer), history thumbnails and full sizes,
  generated and collage-day thumbnails, keys without `t`/`v`, and nothing for
  a file that does not exist. On hosted the account elements are empty in
  every render; self-hosted fills them from config. `hosted_state()` carries `page`. `page/` is
  never pulled. Health timeline vs `frame_health` for every card case. `_ago`
  against the shared fixture.
- **vitest (hosted).** A routed GET is answered from R2 without a Container
  fetch (the stub throws if called); 304 on a matching ETag; a missing file
  or `live=1` falls through to the server; `/` gets the account and the
  banner filled; `/api/status` gets `heard` and `page_build`; `/api/warm`
  starts the server and answers at once; an open `/` with queued ingest in
  quiet hours sets the wake; no snapshot serves the skeleton page; the
  account and banner are filled on a proxied `/` too. The page
  time function against the shared fixture.
- **vitest (sign-in).** POST /login redirects and sets the cookie; the code
  page from the cookie; right code signs in (household made on an
  invitation's first use, setup kind claims the frame); wrong code counted, 5
  kill it; expired; another browser's cookie fails; link and code use each
  other up; resend; uninvited and rate-limited answer the same as invited;
  POST and GET /login while signed in go to `/`; `/login/state`; `GET /setup`
  shows the code form.
- **On Cloud.** A test household signed in with the admin API's link:
  `curl -w` time to first byte for `/` with the server stopped, before and
  after (target under 300 ms); the admin page's server time for that
  household over an hour with a tab open (target: none from the tab). Wells
  on the phone: the Firefox tab switcher sends no email; an iPhone offers the
  code from Mail.

## Rollout

1. W-947 (sign-in): migration `0010`, then `wrangler deploy`. Worker only.
2. W-946 (page): server image and Worker in one `wrangler deploy`. Each
   household shows the skeleton page on its first visit until its next wake
   writes a snapshot; Wells's household is woken by hand right after deploy.

## Risks

- **A stale page for a few minutes.** A snapshot is as old as the last time
  the server settled. Nothing the server alone knows changes without a wake,
  and the front door adds what it knows newer (check-ins). The gaps are
  detections in quiet hours (covered above) and battery readings (hours).
- **Old page code after a deploy.** Handled by `page_build`; until a
  household's next wake its snapshot is the previous build's page, which
  talks to the new server, as any tab left open across a deploy does today.
- **Snapshot size.** `index.html` is about 300 KB uncompressed; R2 writes are
  free of egress and pushed only when changed.
- **The account is filled by the front door on hosted.** A `/` that somehow
  bypassed it would show an empty email row, never someone else's.
