# Featherframe Cloud

How `hosted/` runs the same Python server for many households on
Cloudflare. Owners read "Featherframe Cloud"; anything else is
"self-hosted". The code wins when this page and it disagree.

## Cost

**Featherframe Cloud is free to owners, so it is built to carry at least 500
households at the least cost.** Weigh every hosted change at that scale: a
household's month of container time, Durable Object requests and duration, R2
operations, D1 rows and Worker requests, times 500, against the Workers Paid
allowances (`hosted/src/usage.ts` prices each). Between two designs, take the
one that starts the server less often, keeps it up for less time and makes
fewer requests. Measure a change with the account's GraphQL Analytics, as
`usage.ts` reads it, before calling it cheap.

## Architecture

One host, `cloud.featherframe.app` (the
apex is the marketing page, not routed; `plates.` is the plate library). The
Worker (`hosted/src/index.ts`) routes the page by session (a magic link from
Resend, or the six-digit code emailed beside it, typed in the browser that
asked, `accounts.ts`, `signin.ts`; every form that emails answers
with a redirect to `/login/code`, so a reloaded tab sends nothing;
invite-only; one login per household; D1 `featherframe`
holds households, users, invites, sessions, the frame registry and pairing
codes) and a frame by the registry: its `X-Device-Id` plus `X-FF-Key`, a key
the firmware makes once at first boot (NVS `ffkey`) — a MAC alone gets
nothing. A frame no one has claimed is shown a pairing code (letters only),
drawn for its own panel by the `Lobby` Container (`featherframe/lobby.py`,
same image) as the kit's boot screen: the bough over the wordmark, the code
where the splash sets its version, and under it when the code expires in
the frame's own time zone (Cloudflare's, by its IP: `pairing.expiryText`), so
a frame found unpowered in a drawer says whether its code is still good
(`welcome.render_pairing`), and cached in
R2 (bump `LOBBY_DRAWING` in `pairing.ts` when that drawing changes, or old
codes keep the old one); the owner types it under the Frames card's
*Pair a frame* (hosted only; one large field that keeps six letters as
`ABC-DEF`, typed or pasted), and the household's server adds it at once.
Removing a frame on the page (Remove, or Forget on an ignored kit) also drops
its registry row (`Household.proxy` → `unpair`), so it is shown a new code.
An unclaimed kit's pairing screen also carries a QR of its setup page
(`hosted/src/setup.ts`; the same page from its six letters typed on
the sign-in page). That page makes the account, picks the detection
source and claims the frame. A new account needs an invitation: the kit
registered at fulfillment (`firmware/tools/register_kit.py` → D1 `kits`), or
the email invited from the waitlist. There are no setup codes. Viewers pair the same way (`viewers.ts`): a TRMNL client is known by
its `ID` plus its access token (which `/api/setup` hands out here), the kiosk
page by its id plus a key it keeps in localStorage (`&key=`). Unclaimed, a
TRMNL is sent its code drawn for its own screen (the Lobby's `/render-view`,
cached in R2) and the page shows it in its HTML (`code` in `/api/view/state`).
Claimed, every ask goes to the household's front door, which answers from its
`viewers` table — each on viewer's image drawn ahead by the server
(`service.ensure_view`, in `hosted_state()["viewers"]`, pushed with
`frames/drawn/`) — and keeps the ask for the server (`apply_viewer_checkin`),
so a tablet asking every 20 s never wakes it. An image URL carries `t`, the
first 32 hex of the hash of the device's key. `/view`, its manifest and icons
and `/fonts/script.ttf` are bundled into the Worker (`rules` in
wrangler.jsonc): opening the page wakes nothing.
Per household, `Household` (`household.ts`) is the front door: it answers
`/api/frame` from its table + R2, holds push sockets, keeps check-ins and
detectors' pushes (routed by token; a BirdNET-Go body that is not a detection
is dropped) in its `ingest` queue until the server takes them (below), and
wakes the server only for news (a BirdWeather look every 2 min, a push it
cannot answer itself, the server's own `next_wake_epoch`; ≥ 5 min apart; none
in quiet hours; none for a detection the server named in
`hosted_state()["unchanged"]`: the species whose sheet is shown and the
blocked ones, or `"*"` while no frame shows detections, read from a push by
`util.pushedNames`), stopping it right
after — but only once it is idle: the Containers library's activity
timeout (`sleepAfter`, 30 s) runs from a request's start and knows nothing of
the server's own work, so `HouseholdServer.onActivityExpired` and `wake()`
ask `GET /api/hosted/busy` (`service.busy_for`: a tick and the push after it,
a task, a repaint, a firmware fetch) and stop only when it says no, or once
the work passes `MAX_WORK_S` (10 min); a tick that outlived the timeout was
destroyed before its push, and the nightly collage was lost with it. A stop
that comes anyway waits `HOSTED_STOP_WAIT_S` for the tick under way. genart's
per-key failure cooldown is kept in the DB (`imagegen_cooldowns`, cleared
with `imagegen_error` on a new key), so a fresh Container does not ask a
failed paid generation again early.

A push that only points the frames at a picture already drawn does not start
the server. A pushed detection sets `push_news`, apart from `news`,
which pairing and a BirdWeather look keep; when detections are the only reason
a wake is due (`wakes.onlyDetections`) and the server is not running,
`decideAlone` runs `choose` over the detections it has not yet taken. Every
report carries `switches`, the switch table (`service.switch_table`): for each
species with a kept sheet drawn for every screen on plates, and not new today,
what each frame and viewer would be switched to, with a `valid_until` (the
sooner of midnight and the next quiet hours) after which nothing is decided;
`{}` whenever the front door must not decide (not a push source, quiet hours,
no frame on plates, or detections the server holds that no tick has decided
over: a page's start takes the queue and stops with no tick, and the replay of
the next marker would choose over them), and an older image reports none. It also carries
`applied_seq` and `switch_mismatches`, the server's running count of markers it
disagreed with, which the admin page shows. The rule is pure:
`featherframe/switching.py` (`parse`, `best`, which the server's own
`_pick_showable` ranks with, and `choose`) is ported to `hosted/src/switch.ts`,
both held to `server/tests/fixtures/switch-cases.json` as `spend.decide` is to
`spend-cases.json`. `server/tests/test_switch_e2e.py` holds the two halves
together: a Python `FrontDoor` (`decideAlone`, the queue routes and `takeState`'s
rule) drives a real service through scripted batches with page starts between,
and the server's replay must agree. When `decideAlone` or `takeState` changes,
so does that class. On a species it writes the frame and viewer rows, tells the
sockets last, sets `unchanged`, counts the switch (`usage.switches`) and ages the
page's copies (`PageCache.age`: dated back and kept, since `clear()` leaves
nothing to serve and the next poll starts the server); on that or `same` (what
the frames already show) it writes a `marker` row into the queue after the
detections it decided over. A species with no entry (one the server has not
drawn, or new today), a push with no scientific name (the server would draw
another sheet than any entry names), a first-seen date of today, a seed row in
the queue, a row the server took and has not acknowledged (it counts that row
and `decideAlone` would not), an expired table or an error is a wake, as
before. A push of the other detector's kind than the household's `source_kind`
(queued before it changed its source) is dropped from the choice, and
`apply_queue` skips it, as the route's 409 did. The five-minute
gap for detections counts from the later of `wake_ms` (the server's last wake,
which the daily wake and the admin page read) and `switch_ms`. `nextAlarm`
alarms for a named time whenever there is one, even one that came due while
`decideAlone` awaited the server; one a wake has since failed to move is
retried after `MIN_GAP_MS`, not every second. `takeState`
keeps the rows the front door switched, and its table, while a marker is past
the report's `applied_seq` (the report is older than the switch), and asks for
a wake.

The webapp's reads come from a cache at the front door (`hosted/src/pagecache.ts`): the server's last answer to `/`, `/api/status`,
`/api/history`, `/api/tasks`, the battery, the previews, the history and
generated images and `/static/*`, kept in R2 and keyed by the server's
`page_build` (and, for `/`, the account). Fresh for 15 min; a stale copy is
still served while a `look` asks the alarm for a wake (`wakes.ts`, never
before `MIN_GAP_MS`, in quiet hours only when something is queued). The
server's reports (`takeState`) refresh the copies read in the last 2 h when
the page would change or after a POST; a POST clears the changing ones (the
front door's own switch only ages them); `/`
with nothing kept and the server asleep is the bundled loading page
(`loading.ts`). Reads that must be live ask `live=1`; every other read the
page makes as it loads must be in `routeOf`'s table, or each visit starts the
server. An edit warms the server (`POST /api/warm`).

The household's AI spend is kept at its front door (`hosted/src/spend.ts`): `spend.FrontDoorStore` reserves every paid call at
`/_internal/<household>/spend/reserve` before the server makes it, and settles
it after. A front door it cannot reach buys nothing, and the webapp shows AI
image generation as *Unavailable* until it can. The front door runs
`spend.decide`'s port (held to `server/tests/fixtures/spend-cases.json`) with
the insert, plus one rule of its own: a $10 backstop per UTC day by its own
clock. It emails `ADMIN_EMAILS` once a reason a day when a household pauses,
passes $3 in a UTC day, or hits the backstop, and the admin households table
shows each one's AI spend this month. The server's own rows (and the older
`spend.jsonl`), and a pause from before, reach the front door at each start,
before its first read or reservation there; it keeps each row once. Its server
image rolls forward only: an older image would not see the front door's
records.

The owner's AI keys are the front door's too (`server/featherframe/keys.py`,
`hosted/src/gate.ts`). On Cloud the image key and the text key live only in
the household's front door (`ai_keys`, AES-GCM under the `AI_KEY_SECRET`
Worker secret, bound to the household and the slot, `vault.ts`); the DB keeps
a stand-in, `held:<mask>:<fingerprint>` (`keys.stand_in`), and `keys.key_id`
makes a key and its stand-in the same key, so moving one clears no cooldown
and buys no collage again. A model with a held key sends its request to the
vendor's own path on `<vendor>.vendor.internal` (`keys.Vendor`), naming the
key in `X-FF-Key` and, inside a purchase, the purchase in `X-FF-Purchase`.
`HouseholdServer` intercepts `*.vendor.internal` on its Container
(`ctx.container.interceptOutboundHttp`, set at `configure` before a start and
again `onStart`) into `VendorGate` (`vendorgate.ts`), a Worker entrypoint with
the household in its props, never read from the request. It asks the front
door for the sealed key (`Household.vendorKey`: a POST needs a spend record
that is open), opens it, adds it, and forwards only the paths `gate.ts` lists;
its own refusals carry `X-FF-Gate`. A Worker, not the front door: a Durable
Object is billed while it waits on a fetch, and an image takes minutes. A key
the owner types passes through the server once, in the settings POST, and
goes straight to `PUT /_internal/<hid>/keys/<slot>` (`service._held_keys`; a
save the front door does not take saves nothing). A key from before moves at
a start (`service.hold_keys`, from `HostedLink.take`, after the queue) only
once `keys.gate_agrees`, the vendor answering it through the gate as it does
directly; then the DB is VACUUMed, because a SQLite backup copies free pages.
Cloud offers no Self-hosted image provider and no Local text provider: a
Container cannot reach the owner's machine. Self-hosted is unchanged: the key
is the owner's, in the owner's DB, sent by the server itself.

`HouseholdServer` is THIS Python server in a Container: there is no TS copy of
any rule but the spend gate's `decide` and the front door's switch rule
`choose` (above), each held to its Python side's cases. Hosted
mode (`featherframe/hosted.py`, on with
`FEATHERFRAME_HOSTED_URL`/`_KEY`) pulls the data dir from
`/_internal/<household>/` before the DB opens, pushes changes after every
tick and POST, applies check-ins through `app.parse_checkin` →
`service.apply_checkin` (`add` = paired), and reports `hosted_state()`; a wake
is `POST /api/hosted/run`, its one tick and its one sync (a hosted
server never ticks on its own clock, `service.ticks_itself`, and the run
request does not sync again after its tick). The server takes the front door's
queue itself; the front door sends it nothing at a wake.
`POST {base}/ingest/take` returns every queued row with its `seq`, and
`ingest/ack {upto}` deletes them only after the settle that carried the
database holding them to R2. It is taken at start, beside the server on a
thread (the container library gives a start 20 s to answer on its port) and at
the top of every `/api/hosted/run`. A report waits for the start's take, up to
`QUEUE_WAIT_S` (`HostedLink.queue_ready` is set even when the take fails), and
carries `applied_seq`, so the front door can tell a report that is behind a
switch it made (`takeState`, above). `service.apply_queue` applies pushes,
seeds and markers in `seq` order under the tick lock, skipping any at or below
`queue_seq` (a take repeated after a crash applies nothing twice); a marker is
the plates part of a tick at its own time (`_replay_marker`), so History, the
cursor and the counts come out as if the server had been awake, and its choice
is checked against the server's own: a disagreement, or a replay that raises,
is counted in `switch_mismatches` and the server's picture stands. The take
applies the check-ins and the queue each on its own, so one failing costs the
other nothing, and `settle` builds its state and sends it under
`HostedLink._report_lock`, so two reports reach the front door in the order
their states were built (not `_lock`: building the state asks for the tick
lock, which a tick holds while it asks for `_lock`). `applied_seq` is read
before the report's outputs, so they are at least as new as it says. The small
files a start reads travel as archives, `bundles/files.tar` and
`bundles/history.tar` (`hosted.bundle_of`): one request each, not one per file;
what the front door serves itself, the pictures, the DB and anything over
256 KB stay files of their own, and an archive wins over a file of the same
path. A start
writes every file fresh, so nothing may order files by their times:
history keeps what the render log shows last, waiting plates the last drawn
(kv `waiting_views`), and whatever replaces an image drops its thumbnail. The image
compiles ahead what every start used to (`hosted/Dockerfile`): the
Stucki kernel's numba cache (`NUMBA_CPU_NAME=generic`, so it holds on any
x86-64 host), Python's library and the server as bytecode, the page's
templates into `FEATHERFRAME_TEMPLATE_CACHE` (Jinja loads with the first page,
`app.page_templates()`, so a wake never imports it), and one BLAS thread;
`tests/test_start_cost.py` holds them. The container library's readiness check,
`GET http://ping/`, is answered with a 204 before any route
(`app._ReadinessPing`), so a wake never builds the page to say it is
up. Admin: `/admin` (`admin.ts`) for a signed-in user whose email is in
the `ADMIN_EMAILS` secret, 404 to anyone else — the waitlist (D1 `waitlist`:
the marketing page's form posts `POST /api/waitlist`, form or JSON with CORS
for the apex; double opt-in: an address is pending until it follows the emailed
link, `GET /api/waitlist/confirm?t=` (token hashed, 7 days, re-sent at most
every 10 min, `signUpWaitlist`), with the same answer for every address and 5
sign-ups an hour per IP and per address (D1 `rate_hits`); an uninvited email
trying to sign in joins it pending, and is not emailed; the admin counts only
confirmed ones), invitations, and every household
(frames and when each was last seen, from the front door's `summary()`, and
rough server time by UTC day: its wakes, apart from the time a page kept it
up; and the last 7 days' switches the front door made without a wake,
summed from the same `usage` days, with a running count of the ones the server
disagreed with). The page also shows this month's Cloudflare usage against the Workers Paid allowances
and the bill so far (`usage.ts`: GraphQL Analytics with the `CF_API_TOKEN`
secret, one query per dataset so a missing field costs one meter; Containers
are not in that API, so theirs is the front doors' own count), *Log in as* (an
`ff_as` cookie naming a household, honoured only beside an admin's own
session, `sessionUser` vs `realSessionUser`; a bar over the page leads back),
changing a login's email outright, suspending a household
(`households.suspended_at`: its page closed to the owner, its front door stops
waking the server, its frames keep their last picture), deleting one, and
revoking or resending an unused invitation. A delete is complete (`deleteHousehold`): every D1 row that names the household or its login
(sessions, links, email changes and checks, its invitation and waitlist row,
its rate counts, its frames' registry rows; a kit set up into it may be set up
again), its front door's storage, its server Container's storage and every R2
object under `households/<id>/` — the front door is handed the id
(`Household.destroy(hid)`), never reads its own — and the toast says how many
frames now show a pairing code and how many files went; run again, it
finishes one that stopped part way. The admin's own household has no Delete.
Each row's actions sit in its last column; a household's are *Log in as* and
a ⋯ menu (Suspend/Resume, Change email…, Delete…, the last two dialogs, Delete
asking for the email or id typed back). Every action, the page's and the
bearer API's, is one entry in `admin.ts`'s `ACTIONS` (a page path
`/admin/household/delete` is `household.delete`). What an action came to is
a toast on the next load (the Featherframe page's own flash, carried by a
one-time `ff_admin_toast` cookie, never the URL), and every action is kept in
D1 `admin_log` (migration 0005), the last 100 shown at the foot of the
page. The API: `Authorization: Bearer <admin token>`, `POST /_admin/invite|link {email}`,
`adopt {email, household}`, `kit {device_id, key_hash, kit, note}`; it answers
`{ok, result}` or `{ok: false, error}`.
Plain http to any hosted host is a 301 to https before anything else
(`util.httpsRedirect`). Deploy: `cd hosted && npx wrangler deploy` (Docker running; retry on a
registry push drop; a new image serves only once the rollout ends and the
Container restarts); D1 schema in `hosted/migrations/`.
