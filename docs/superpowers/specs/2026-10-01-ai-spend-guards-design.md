# AI spend guards (W-938)

## Why

From 26 to 28 Sep 2026 OpenAI billed about 89 Max-quality collages for one
household ($18.88), and the spend ledger recorded 5. Each server start found
the day's collage missing, bought it again, and was stopped (W-917) before it
saved the sheet or logged the purchase. The account ran dry late on 28 Sep.

Three things let it happen:

- **The record came after the purchase.** `record_spend` runs only once the
  vendor answers, and on Cloud the ledger reaches R2 only at the end of a
  tick. A process that dies mid-call leaves no trace, so the next one buys
  again.
- **Nothing capped the total.** The only limits were a 15-minute cooldown
  after an *error* and `REGEN_PER_HOUR` on manual repaints. A killed process
  raises no error.
- **Nobody was told.** The webapp's month-to-date line reads the same ledger
  that undercounted.

Owners give us their API keys. A bug must never spend their money unchecked,
and an owner must always be able to see and stop what is spent.

## What the owner sees

All in the **AI image generation** section of Settings.

### The switch

The section's first row is a switch labeled **AI image generation**. Off,
nothing is bought of any kind: no illustrations, no collages, no species
briefs, no weather. The provider, key, model, quality and limit are all kept,
and illustrations and collages already generated keep showing. The two
feature switches (**Generate images for missing species** in Illustrations,
**Generate collages** in Collage) keep their values but are locked, with the
hint "AI image generation is off", the way they are locked with "Needs an API
key" today. With no key stored, the switch itself is locked with "Needs an API
key". It saves the moment it is flipped, like every switch.

### Monthly limit

A row **Monthly limit**: a dollar field, whole dollars, at least $1, no hint.
It always holds a number. Default **$10**. (With AI collages at Medium a busy
month runs about $5–7; at $5 some owners would stop near month's end.) The
month is the calendar month in the server's time zone. Existing households
get the default, and what the ledger already holds for this month counts.

The section's existing "This month: …" line becomes "This month: $1.20 of
$10.00".

### The section's summary and states

The collapsed row's summary is one of these, first match wins:

| State | Summary | Dot |
|---|---|---|
| Switch off | Off | grey |
| No key | No API key | grey |
| Key refused | Key rejected | red |
| Account empty | Out of credits | red |
| Paused | Paused | red |
| At the limit | Limit reached | red |
| Otherwise | OpenAI · $1.20 of $10.00 | green |

Open, a paused or capped section shows its notice where the out-of-credits
notice sits today:

- **Paused:** "AI generation is paused: 7 purchases in the last hour, more
  than usual. Nothing more is bought until you resume." Button: **Resume**.
- **Limit reached:** "This month's AI spend reached your $10.00 limit. New AI
  illustrations and collages resume on 1 Nov, or raise the limit."

### What the month will cost

Under the **Quality** menu, a line that follows the menu as it changes:
"About $4 a month at this station's pace." It is this household's automatic
purchases of each kind over the last 30 days (or since the first one, scaled
to 30 days), each at the chosen quality's price. With no purchases yet, no
line.

### On the frame

Where the frame shows the grid in place of an AI collage, the footnote pill
says why, as "Out of OpenAI credits" does today: **AI limit reached** or
**AI paused**. With the switch off, no footnote: the owner chose it.

### Repaint

A repaint the gate refuses answers with the reason: "This month's AI limit is
reached.", "AI generation is paused." or "AI image generation is off."

### The wiki

The AI illustrations page gains a step: set a monthly budget on your own
account with the provider as well, so a limit holds even outside
Featherframe. The wording is checked against OpenAI's current settings page
when written.

## The gate

A new module, `featherframe/spend.py`, is the only way to a paid call.

```python
with spend.purchase(kind, subject, model=..., quality=..., auto=True) as p:
    png = image_model.generate(prompt, size, refs)
    p.settle(usage)          # or p.release() on a vendor refusal
```

`kind` is `plate`, `collage`, `describe` or `weather`. `auto` is False only
for an owner's own action (Repaint, the collage's repaint).

### Order

1. **Check.** Refused if the switch is off; if AI is paused; if this month's
   spend (settled at cost, open at estimate) plus this estimate passes the
   limit; if the subject rule says no (auto only); or if the runaway threshold
   is reached (auto only; this trips the pause).
2. **Reserve.** A record (id, kind, subject, auto, model, quality, estimate,
   time, state `open`) is written durably before the call. If it cannot be
   written, nothing is bought.
3. **Call** the vendor.
4. **Settle.**
   - The vendor answered with the result: state `settled`, real usage and
     cost, even if the image then fails to decode (it was billed).
   - The vendor refused before doing the work (HTTP 400, 401, 402, 403, 404,
     429): state `released`, $0. `failure_reason` still decides what the
     page says.
   - Anything else (a timeout, a dropped connection, a 5xx, the process
     dying): the record stays `open` and counts at its estimate, as if billed.
     The subject cools off for 24 hours.

A refusal at step 1 is not an error: the caller falls back as it does today
(the grid, the typographic illustration, no brief, the season's own snow).

### The fence

Every paid model method (`ImageModel.generate` on each provider,
`TextModel.complete_json`, `OpenAITextModel.search_json`) raises
`spend.Unguarded` when called outside `spend.purchase` (a thread-local the
gate sets). A new call site that forgets the gate fails in tests, never in an
owner's account. Listing models and *Test key* cost nothing and stay outside.

### Estimates

The gate needs a price before the call. `spend.ESTIMATE_USD` holds the
measured W-859 prices for `gpt-image-2.5-*`: low $0.034, medium $0.039, high
$0.070, xhigh $0.103, max $0.194; a collage sheet is 10% more. A brief is
$0.002; a weather lookup $0.012. Any other paid model is priced at the
highest image price in the table ($0.21), so the limit still holds for a model
we have not measured. A local model (`a1111`, `local`) is $0.

### Subject rules (automatic purchases)

The counts come from the durable records, so a restart cannot reset them.

| Kind | Rule |
|---|---|
| plate | Once per species. An `open` record for the species blocks another for 24 hours. |
| collage | Subject is the date. One per collage interval for a date (no second within the interval less 10 minutes), plus one nightly sheet per date: the quiet-hours collage passes `nightly=True` through `day_composite`, and its subject is `<date>/nightly`. |
| describe | Once per species (as the brief cache already does). |
| weather | Once per `weather.REASK_S` per date (as today). |

Owner actions skip the subject rule, never the switch, the pause or the limit.
`REGEN_PER_HOUR` stays.

### Runaway pause

More than `RUNAWAY_PER_HOUR` = 6 automatic image purchases in a rolling hour trips
the pause: the purchase that would be the 7th is refused, and the pause is
stored with its time and count. It holds until the owner presses **Resume**;
after a resume only purchases after it count. With the subject rules in place
a household never comes near 6 an hour, even on hourly collages. It counts
automatic collages, and an automatic illustration only when it buys a species
again within a day of buying it; a first illustration of a new species is
bounded by the monthly limit, not the pause. Briefs and weather cost cents, and
a day's collage can need a dozen briefs.

## Where the records live

One interface, `spend.Store`, with two implementations.

### Self-hosted: `LocalStore`

A `spend` table in our SQLite (`db.py`): `id, at, month, day, kind, subject,
auto, model, quality, est_usd, cost_usd, usage, state, settled_at`, and a kv
row `ai_pause`. The reserve is committed before the call. On first start this
month's `spend.jsonl` lines are imported as `settled`; the file is then no
longer written. `spend_for_month` and the page read the table.

### Cloud: `FrontDoorStore`

The household's front door (`Household` Durable Object) holds the records in
its own SQLite (`spend` table, the same columns) and the pause. The server
has no count of its own on Cloud. Routes under `/_internal/<hid>/spend/`, on
the server's existing key:

- `POST reserve` `{id, month, day, kind, subject, auto, est_usd, limit_usd,
  subject_rule, runaway_per_hour}` → `{ok: true}` or `{ok: false, reason}`,
  `reason` one of `paused`, `limit`, `subject`, `runaway`, `backstop`. One
  DO transaction: check, then insert. The numbers come in the request, so the
  rules stay in Python; the front door counts.
- `POST settle` `{id, state, cost_usd, usage}`.
- `GET summary?month=` → month totals (settled, open at estimate), purchase
  counts by kind for the last 30 days, the pause. The page's status reads it
  (cached 30 s).
- `POST resume`.

The front door adds one rule of its own, a platform backstop: at most
`BACKSTOP_USD_PER_DAY` = $10 of reservations per household per UTC day,
whatever the request says. Any `backstop` refusal means the server's own
checks failed, and it alerts (below). A front door that cannot be reached
refuses: nothing is bought.

## Admin alerts (Cloud)

- The admin page's households table gains **AI this month**, read from each
  front door's summary without waking its server.
- The front door emails `ADMIN_EMAILS` (through `sendMail`) when a household
  pauses, passes `ALERT_USD_PER_DAY` = $3 in a UTC day, or hits the backstop.
  At most one email per household per reason per day. Subject: "Featherframe
  Cloud: {household} AI {paused | passed $3 today | hit the $10 backstop}".
  Body: the reason, the last hour's and today's purchases, and this month's
  spend against the limit, with a link to the household's admin row.
- Owners get the webapp notice and the footnote only (decided 1 Oct 2026).

## Config and migration

- `imagegen_enabled` becomes the master switch. It already turns off every
  model (`make_image_model`, `make_text_model`).
- New `illustrations_generated: bool = True` is the **Generate images for
  missing species** switch, which until now was `imagegen_enabled` itself.
  `GeneratedArtProvider.artwork` buys only when it is on.
- Migration: `illustrations_generated` takes the stored `imagegen_enabled`;
  `imagegen_enabled` keeps its value. A household that had the missing-species
  switch off had, without knowing it, AI collages off too; it stays fully off
  and sees the master switch off. No migration starts a purchase that was not
  happening.
- New `ai_monthly_limit_usd: int = 10`, sanitized to 1–1000.
- The pause is state, not config: the kv row (self-hosted) or the front door.

## Tests

- **The gate:** each refusal (off, paused, limit, subject, runaway); the
  record exists when the fake model is called; settled, released and open
  outcomes; open records count at their estimate; an owner action skips the
  subject rule only.
- **The fence:** every paid method of every model class raises outside the
  gate.
- **The 27 Sep replay:** a fake model that bills every call, and 100 server
  starts that each lose the data dir mid-tick, against a durable fake front
  door. At most one collage per interval per date is bought; with the subject
  rule switched off, the pause trips at the 7th purchase in an hour.
- **Migration:** `illustrations_generated` from `imagegen_enabled`; off stays
  off; this month's `spend.jsonl` lands in the table.
- **Worker (vitest):** `reserve` is atomic under concurrent requests; the
  backstop; pause and resume; the summary; an unreachable front door refuses
  on the server side.
- **The webapp:** the switch locks the two feature switches; the limit field
  saves; each summary state renders; the projection line follows the menu;
  `check_copy.py` passes.

## Order of work

1. **PR 1, the gate:** `spend.py`, the fence, `LocalStore`, the subject rules,
   the pause, the master switch and its migration, the limit, the page, the
   footnotes, the wiki step. It runs on Cloud with `LocalStore` until PR 2;
   W-917 already keeps a tick alive until it saves.
2. **PR 2, the front door:** `FrontDoorStore`, the DO table and routes, the
   backstop, the admin column and emails.

## Out of scope

- The key leaving the server's data: W-937.
- Checking against the vendor's own usage: OpenAI's usage API needs an admin
  key, which owners do not have.
- Limits for our own scripts. They spend Wells's key, not an owner's. Two
  call the models and would hit the fence: `firmware/tools/screens/boot_art.py`
  and `featherframe/preview.py`'s generated path. PR 1 wraps each of their
  calls in `spend.purchase` against a `LocalStore` on a scratch DB with the
  limit set high, so they keep working and their purchases are recorded.
