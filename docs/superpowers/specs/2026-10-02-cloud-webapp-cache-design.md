# The Cloud webapp from a cache, and sign-in with a code (W-946, W-947)

Revised 2 Oct 2026: part 1 after an adversarial review (Fable); part 2
replaced the same day by a plain cache, in place of a snapshot the server
wrote. Both changes are listed at the end.

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
- Leaving the webapp open never holds the server up, and looking at it
  starts the server at most once every 15 minutes, only when nothing else
  has woken it in that time.
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


## Part 2: the webapp from a cache (W-946)

### The idea

A standard stale-while-revalidate cache, kept by the front door. It keeps the
last answer the server gave to each read the webapp makes (the page, its JSON,
the pictures on it) and answers the next ask for it at once, without the
server. A copy older than 15 minutes is still answered, and the front door
asks for a wake in the background; the page takes the new copy on a later
30-second status check. While the server is up anyway (a wake, a save, its
own ticks), the front door refreshes the copies someone is reading, so they
are rarely old. A save clears the copies it can change, so the owner always
sees their own change.

The trade: when nothing has woken the server for 15 minutes (at night,
mostly), the page opens on a copy up to 15 minutes old. It is replaced on the
next status check after the wake that look asks for, which is under a minute
unless a wake ran in the last 5 minutes (`MIN_GAP_MS`), then up to 5. In
return, looking at the webapp never starts the server just to look.

### What is cached

Only GETs from the signed-in webapp, on these paths, answered 200, under
5 MB:

| Path | Query in the key | Kind |
|---|---|---|
| `/` | none (the rest is the page's own flash messages) | changing |
| `/api/status`, `/api/history`, `/api/tasks` | none | changing |
| `/api/battery` | `frame`, `hours` | changing |
| `/api/frames/<id>/preview.png`, `/api/preview.png` | none | changing |
| `/api/collages/<today>.png` | `thumb` | changing (the tick redraws today's) |
| `/api/collages/<earlier day>.png` | `thumb` | fixed |
| `/api/generated/<slug>.png` | `thumb`, `v` (its `created_at`: a repaint is a new key) | fixed |
| `/api/history/<etag>.png`, `.jpg` | none | fixed (the name is its content's hash) |
| `/static/*` | none | fixed |

Any other query parameter (the page's cache-busters `t` and `w` among them)
is left out of the key. A request with `live=1` skips the cache. Every POST,
and every GET not listed (`/api/imagegen/models`, `/api/generated/export`, the
USB dialog's `/api/flash/*`: things an owner does, not looks at), goes to the
server as today. The time function the page needs
(`server/static/js/page-time.js`) is inlined into the page, so loading it is
no request at all.

The key also carries:

- for `/`, the account headers the Worker already sends (the email, the
  address waiting for confirmation, whether it is confirmed), so the account
  row and the "Confirm your email address" banner are right for whoever is
  signed in, with nothing rewritten;
- the server's page build: the first 12 hex of the sha256 of `templates/`
  and `static/`, computed once at start and reported in `hosted_state()`
  (`page_build`). A new server image empties the cache when it first reports,
  so its copies are never from an older page. A Worker-only deploy empties
  nothing.

Storage: the bodies in R2 at `households/<hid>/cache/<random id>` (deleted
with the household, like everything under that prefix), indexed in the front
door's SQLite:

```sql
page_cache (key PRIMARY KEY, kind, request, object, type, headers,
            generation, filled_at, read_at)
```

`request` is what to ask the server again: path, the query in the key, and
for `/` the account headers. `object` is NULL until a copy is stored. The
browser gets the server's own headers. Rows unread for 30 days, and their
objects, go at the next refresh; a build change deletes them all.

### Reads

- **Fresh** (filled under 15 minutes ago): answered from R2. The server is not
  asked.
- **Stale**: answered from R2 just the same, and the front door asks for a
  wake (below).
- **Missing, server running**: asked of the server (straight, never through
  `proxy()`, so it does not count as page activity), stored, answered.
  "Running" is a new `HouseholdServer.running()`: `ctx.container.running` and
  not on its way out (`!this.stopping`, containers.ts), so a server stopping
  counts as asleep.
- **Missing, server asleep**: the row is written with `object` NULL so the
  next refresh fills it. `/` answers the loading page (below) and asks for a
  wake; any other path is asked of the server as today.

Every hit sets `read_at`.

### Asking for a wake

A new meta flag, `look`. A stale read, or a miss on `/`, sets it and sets the
alarm to `max(now + 500 ms, last wake + MIN_GAP_MS)`. `alarm()`'s `due` and
`schedule()` both read it: due when it is set and `MIN_GAP_MS` has passed
since the last wake. `wake()` clears it. It never sets `news` or zeroes
`wake_ms`, so it cannot jump the gap. `suspended` stops it as it stops every
wake.

In quiet hours (`poll = 0`) a look asks for a wake only when the front door
holds check-ins or detections the server has not taken: in quiet hours
nothing else can have changed.

A wake asked for by a look is an ordinary wake: it hands the server the
queued detections and check-ins first, so the refreshed copies have them.

### Refreshing while the server is up

The server already tells the front door each time it settles
(`report()` → `takeState`: after a wake's tick, a POST, and its own ticks
while it is up). That report decides the refresh:

- **When:** a report that changed what the page shows (a frame's or viewer's
  picture, status or settings, the set of frames, the page build), every
  report that follows a proxied POST, and the end of every wake if the copies
  are more than 15 minutes old. Nothing else: the copies are not redrawn for
  a tick that changed nothing.
- **Which:** every changing row read in the last 2 hours, plus rows with
  `object` NULL. When `/` qualifies, `/api/status`, `/api/tasks`,
  `/api/history` and each frame's preview are refreshed with it, read or not,
  so a page and its parts always come from one refresh.
- **How:** asked of the server straight (not `proxy()`, so not page
  activity), in `ctx.waitUntil` from `takeState`, one at a time, into new
  objects. The rows then all move to the new `generation` in one SQLite
  transaction and the old objects are deleted, so a page never meets a status
  from another refresh.

A household nobody has looked at in 2 hours gets no refreshes. One whose
owner looked an hour ago gets at most one every 15 minutes, plus one per
picture change.

### What clears it

- A POST the front door proxies, answered under 400, clears the household's
  changing copies before the answer leaves. The redirect after a save, and the
  reads after it, are then misses while the server is up: asked of it, and
  stored. Fixed copies stay. `/api/warm` and the other routes the front door
  answers itself are not proxied POSTs.
- `adopt()` (a frame paired by code or over USB) clears the changing copies,
  so the reload after pairing shows the new frame once its wake has drawn it.
- A new page build clears everything.

### The page

- **Times in the browser.** Every relative time on the page ("just now",
  "7 min ago", "yesterday", "24 Sep") is rendered by one JS function from the
  ISO time the server puts beside it (most are there; the rest are added):
  last detection, pending, quiet and outage "since", history, a frame's last
  check-in. A copy 15 minutes old then still says the right age. Countdowns
  read absolute times (`queued_until`, a pending species' `expires_at`), so
  they don't jump back when a copy is applied again. The function follows
  `_ago` and STYLE.md; a shared fixture
  (`server/tests/fixtures/page-time-cases.json`) holds both to the same
  answers (pytest on `_ago`, a vitest in `hosted/test` on
  `server/static/js/page-time.js`).
- **Status applied at load.** The page asks `/api/status` once as it loads (a
  cache hit, tens of milliseconds).
- **The set of frames** that `applyStatus` compares (`knownFrames`) starts
  from the rows the page rendered, not the first status. A status whose
  frames differ reloads the page, which then comes from the same refresh as
  that status.
- **Live polls.** The task and regenerate polls, and the 2 s poll while a
  firmware update is moving, ask `live=1`. The on-load `/api/tasks` ask is a
  cached read.
- **The preview** reloads when the status says that frame's picture changed
  (`fr.etag`), not every 15 s.
- The status poll stays at 30 s, and still stops when the tab is hidden or
  untouched for 10 minutes.

### Edits

A save still needs the server, which is now usually asleep when the owner
starts one.

- **Warm on intent.** On hosted, the first `input`, `change` or `focusin` on a
  settings or frame field, or opening a dialog (Pair a frame, USB firmware
  update), sends `POST /api/warm`; more edits send it again at most every 20 s.
  The front door answers 204 at once and, unless the household is suspended,
  starts the server in the background (`ctx.waitUntil`, asking
  `/api/hosted/busy`), counted as page activity. The server stops on its own
  30 s after the last warm or save. Opening a section to read it warms
  nothing.
- **Switches don't wait.** A switch flips at once and stays flipped; its tick
  reads "Saving…" until "Saved" or "Not saved" (then it flips back, as
  today). The request is sent with `keepalive`.
- **Save buttons** read "Saving…" and are disabled until the answer.

### The loading page

`/` with nothing cached and the server asleep: a new household (setup lands
on `/?welcome=1` while its first wake runs), or the first visit after a new
server image. A page bundled in the Worker shows the webapp's header and the
two columns' cards as gray blocks, with no text. It asks
`GET /api/page/ready` every 2 s. The front door answers that itself: ready
when `/` is cached; and when it is not but the server is running, it asks the
server for `/` first and stores it. The loading page then reloads with its
query and hash kept. The preview area shows the same gray block until its
image loads, on every page.

### What runs the server, before and after

| Event | Today | After |
|---|---|---|
| Open the webapp | starts it; held 30 s+ | nothing if the copies are under 15 min old; else one wake, at most every 5 min (in quiet hours only if check-ins or detections are waiting) |
| Tab open, visible, untouched under 10 min | held up the whole time | nothing |
| Hover a battery cell | starts it | nothing once cached |
| Start editing | already up from the visit | starts it; held until 30 s after the last edit |
| Save | proxied | proxied (usually warm by then) |
| A wake or tick while someone looked in the last 2 h | — | a refresh of the page's copies when the picture changed, or once every 15 min: one page render on a server already running |

### Self-hosted

No cache. The page's own changes (times in the browser, status applied at
load, `knownFrames` from the rendered rows, the preview on its etag, live
polls) run the same on the box.

## Testing

- **pytest.** An ISO time beside every relative time the page shows;
  `queued_until` and `expires_at`; `_ago` against the shared fixture;
  `hosted_state()` carries `page_build`, which changes with the template.
- **vitest (hosted).**
  - A fresh hit is answered without a Container fetch (the stub throws if
    called).
  - A stale hit is answered and sets `look`; `alarm()` wakes for it only
    after `MIN_GAP_MS`, never when suspended, and in quiet hours only with
    check-ins or detections queued; `wake()` clears it. Written against the
    real `alarm()` and `schedule()`.
  - A miss while running is asked of the server, stored, and does not move
    `page_ms`; `running()` is false while stopping. A miss while asleep writes
    a NULL row; on `/` it answers the loading page and sets `look`.
  - The key keeps `thumb`/`frame`/`hours`/`v`, drops `t`/`w`, carries the
    account headers for `/` and the page build. Only a 200 under 5 MB is
    stored. `live=1` skips the cache. Today's collage is changing, earlier
    days fixed.
  - A proxied POST under 400 clears changing copies before answering and
    keeps fixed ones; `/api/warm` clears nothing; `adopt()` clears; a new
    page build clears all.
  - `takeState` refreshes on a change, after a POST, and at a wake's end when
    the copies are over 15 min old; not for a tick that changed nothing;
    only rows read in 2 h (plus NULL rows, plus `/`'s companions); the rows
    move to the new generation together.
  - `/api/page/ready` fills `/` when the server is running; `/api/warm`, and
    not for a suspended household; the page time function against the
    shared fixture.
- **vitest (sign-in).** As in part 1.
- **On Cloud.** A test household signed in with the admin API's link:
  `curl -w` time to first byte for `/` with the server stopped and a fresh
  copy cached (target under 300 ms); Container time and starts for that
  household (W-915's diagnostics) over a day with the page looked at hourly,
  before and after; a tab left open an hour starts nothing. Wells on the
  phone: the Firefox tab switcher sends no email; whether an iPhone offers
  the code from Mail.

## Rollout

1. W-947 (sign-in): migration `0010`, then `wrangler deploy`. Worker only.
2. W-946 (cache): one `wrangler deploy` (the server image for the page's
   changes and `page_build`, the Worker for the cache). Every household's
   first visit after its new server reports shows the loading page while its
   first fill runs.

## Risks

- **Up to 15 minutes old** for under a minute after opening (up to 5 minutes
  right after another wake), when nothing has woken the server in that time.
  The accepted trade.
- **Refreshes cost Container time.** Each is a page render (`status()` asks
  BirdWeather up to three times) on a server already running, at most once
  every 15 minutes plus once per picture change, and only for 2 hours after
  someone looked. Measured on Cloud before and after (Testing).
- **What is cached** is what the page shows (the masked API key, frames' IP
  addresses), under the household's own R2 prefix, served only to its
  signed-in owner, as the page itself is.
- An admin looking at a suspended household (`ff_as`) can still start its
  server through a miss, as today.

## Changes after review

Fable reviewed the first draft adversarially on 2 Oct 2026, and part 2 again
after it became a cache; each finding was checked against the code.

**Part 1** took these:

- Enumeration: a NULL row answers as a wrong code; the email is sent in
  `waitUntil`, so timing doesn't depend on the address; Origin checks.
- `limiterFor` changes, not cited.
- Apple Mail's autofill is tested on the phone, not claimed.

**Part 2** was replaced the same day: the snapshot the server wrote on every
settle became this cache, a standard pattern with far fewer parts, trading up
to 15 minutes of staleness when nothing has woken the server. From the first
review it keeps: the on-load `/api/tasks` ask is cached; cache-busters are
outside the key; task and firmware polls ask `live=1`; wakes go by alarm and
check `suspended`; the loading page keeps its query; switches don't wait;
times and countdowns read absolute times.

The second review, of the cache, changed:

- **A cleared or new household could never fill `/`:** a miss writes a NULL
  row the next refresh fills, `/` brings its companions, and
  `/api/page/ready` fills `/` itself when the server is running.
- **A stale look's wake didn't fit `alarm()`:** a `look` flag that `due` and
  `schedule()` read, cleared by `wake()`, never jumping `MIN_GAP_MS`.
- **Copies refreshed one by one could disagree:** one generation, swapped in
  one transaction; `knownFrames` starts from the rendered rows.
- **Reads that would still start the server:** `page-time.js` inlined;
  `/static/*` cached; fixed copies survive a POST.
- **Changes made by the server's own ticks:** refreshes now follow
  `takeState`, which the server already calls on every settle.
- **Generated and collage images changed under their key:** `v` is in the
  key; today's collage is changing.
- **Cost:** refresh only on a change or every 15 minutes, only for rows read
  in 2 hours, never as page activity; measured on Cloud.
- **`version_metadata` was not bound, and a Worker deploy is the wrong
  signal:** the key carries the server's own page build instead.
- **`running()` raced a stop:** a stopping server counts as asleep.
- **Quiet hours:** a look wakes the server only when something is waiting.
- **The index could not replay a request:** it keeps `request`.
- **The wait after a look** is up to 5 minutes right after another wake, and
  says so.
