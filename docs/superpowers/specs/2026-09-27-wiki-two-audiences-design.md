# The wiki for owners and makers (W-614)

## Why

The wiki is written for makers: its Quickstart starts with choosing a server
and flashing firmware, and Troubleshooting opens with mDNS. A person who was
given a ready-made frame has none of that to do. They plug it in, join
**Featherframe-Setup**, scan a code, and fill in one page, and nothing on the
wiki says so. When they fall off that path (a wrong Wi-Fi password, a code
that expired, a sign-in email that never came), nothing speaks to them.

Two audiences, two ways in:

1. **Makers**, from GitHub: what do I need to know and buy, and how do I start?
2. **Owners**, from the card in the box and from Featherframe Cloud: "visit
   featherframe.app/help", or a *learn more* link in the webapp.

## Decisions

- **One wiki, two front doors, one home per fact.** Home and the sidebar open
  with *Your Featherframe* (owners) and *Build your own* (makers). Every fact
  lives on one page; where Cloud and self-hosted differ, a page carries short
  labelled sections, as *Add a frame* already does. Parallel owner and maker
  pages were rejected: a GitHub wiki cannot include one page in another, so
  every shared fact would be copied.
- **Owners are on Featherframe Cloud.** A ready-made frame is registered at
  fulfilment and set up from the phone. Running your own server with a
  bought frame is the makers' *Install the server*.
- **Makers are assumed to have nothing.** No page on the maker side assumes a
  kit was bought or given.
- **What is in the box is left vague.** Owner pages say "a USB-C cable and
  power adapter", never that one is included: some kits include one.
- **No setup codes.** They are gone (W-892). The frame says "SCAN WITH YOUR
  PHONE TO SET UP / OR ENTER THE CODE AT / CLOUD.FEATHERFRAME.APP"; the
  sign-in page's *Set up a new frame* takes the code. A maker on Cloud needs
  an email invitation (the waitlist); with it, the same QR or code works.
- **Owners reach help by email.** *Still not working?* gives owners
  help@featherframe.app and makers GitHub issues.
- **Owners are linked through featherframe.app/help, never to the wiki.** The
  card and the webapp print `featherframe.app/help/<topic>`, a redirect table
  the site owns, so the pages can move (to the site itself, say) without a
  card being reprinted. GitHub keeps wikis of small repos out of search
  engines; that is accepted for now.
- **Naming** follows docs/STYLE.md: Featherframe Cloud and self-hosted, never
  "hosted".

- **A removed frame is a new kit again.** Giving a frame away is Remove on
  the page, then the new owner scans the new code. Today Remove lets the
  frame go but leaves its kit marked used (`Household.unpair` deletes only
  the registry row), so a new owner with a new email is quietly added to the
  waitlist. `unpair` also frees the kit (`kits.used_at`, `household_id`) when
  it was this household's.

## The sidebar

**Your Featherframe**

- **Set up your frame** (new, `/help/setup`): the first frame and the
  account, nothing else. You need: Wi-Fi on 2.4 GHz, a phone, a USB-C cable
  and power adapter. Plug it in; on the phone, join **Featherframe-Setup**
  and choose your Wi-Fi (if the page does not open, `http://192.168.4.1`;
  a new frame keeps offering it until it has Wi-Fi; opened later with KEY2,
  it closes after 10 minutes);
  scan the code, or enter it at cloud.featherframe.app under *Set up a new
  frame*; the setup page: email, and a detection source (a BirdWeather
  station near your phone's location or a ZIP code or town, or your own
  BirdNET-Pi or BirdNET-Go, connected afterwards); the two emails
  (*Welcome to Featherframe!*, and the confirmation, whose link works for 7
  days); hang it. A gift needs nothing from the giver. "Next: Your frame."
  No server address is mentioned: a Cloud kit's first setup has no such
  field.
- **Your frame** (new, `/help/frame`). The buttons (moved from *Build the
  frame*; the power-on KEY2 hold forgets Wi-Fi, it does not erase
  everything). The light's colours (moved from *Status LED*; amber is "shows
  its setup code" on Cloud, "waiting to be added" self-hosted). What the
  glass shows, first seen first: the Wi-Fi setup steps (its third step, the
  server address, is for self-hosted; a Cloud frame has no such field),
  Connecting, the setup code, *No detections yet / Listening since…*,
  *Detection source unreachable*, *Can't reach Wi-Fi*, *Can't reach server*
  (with the retries: 1, 5, then 15 minutes), the offline mark in the corner
  (after 30 minutes without a check-in), the button messages (*Loading
  image*, *Loading collage*, *Already up-to-date*, *Join
  Featherframe-Setup*), and, labelled self-hosted, *Add this frame on the
  Featherframe webapp*. Low battery stays on *Battery*: a sold kit has none.
  When the picture changes and why it sometimes waits (quiet hours, a new
  species heard twice, the first species of the day held): this page is its
  one home; Troubleshooting links to it. The EE02's 15-second change.
- **Troubleshooting** (reordered by symptom, `/help/troubleshooting`):
  - *The frame*: nothing on the glass or no light (power; the EE02's first
    paint takes 15 seconds); the setup page doesn't open; the Wi-Fi isn't
    listed or won't join (2.4 GHz); *Can't reach Wi-Fi*; *Can't reach
    server*; the code expired (a new one appears within seconds); the
    picture doesn't change (a line to *Your frame*).
  - *Your account*: the sign-in email didn't come (it comes only to an
    account's address or an invited one; the page says the same either
    way); set up with the wrong email (change it under **Settings →
    General** while the phone is still signed in, 30 days; else help@).
  - *Detection sources*: as today.
  - *Move, give away, or reset a frame*: new Wi-Fi (hold KEY2 3 seconds;
    leave **Server URL** as it is); give it away (Remove, then the new owner
    scans the new code); forget Wi-Fi (hold KEY2 while plugging in); reset
    everything (the USB installer's **Erase**, a maker's tool).
  - *Your own server*, last: mDNS, the other-server hijack, missing
    illustrations, the local database.
  - *Get help* (not a question: STYLE): help@featherframe.app, or a GitHub
    issue for makers.

**Use** (both audiences)

- Settings, Add a frame, Detection sources, Other screens, Species and
  illustrations, AI illustrations, How AI illustrations are made.
- **Add a frame** is the one home for a second frame: Featherframe Cloud
  (signed in on the phone, scan and **Add this frame to your account**;
  signed out, the emailed link does it; or **⋯ → Pair a frame** with the
  code) and self-hosted (**Add**). **Other screens** gains Featherframe
  Cloud's addresses.

**Build your own**

- Quickstart (the maker's; its "frames come pre-flashed" line goes to *Set
  up your frame*), Kits and screens (the *Frames* page's sidebar label, so
  it does not sit beside *Your frame*), BirdNET hardware, Build the frame
  (buttons move out), Status LED (wiring only), Battery, Flash the frame,
  Install the server (self-hosted; its Cloud section becomes one line to
  the waitlist).

**About**: Why Featherframe, About BirdNET, Sibling projects.

**Develop**: unchanged.

Home is the same map with a line under each heading. Every wiki page stays
under about 1000 words and in the wiki's own style (numbered steps, bold UI
names, no emoji, plain headings, "Next:" footers). "Hosted" goes from every
page (Detection sources' table, Add a frame, Flash the frame, Home,
Quickstart, Install the server), and `check_copy.py` retires it
(`hosted` but not `self-hosted`) so it stays gone.

## Links in

`site/src/help.json` is the table: a topic, a wiki page, and an optional
heading. `site/src/worker.ts` imports it and answers `/help` and
`/help/<topic>` (any case, with or without a trailing slash) with a 302 to
the wiki: 302, so a target can move without browsers caching the old one.

| Topic | Wiki page |
|---|---|
| (none) | Home |
| `setup` | Set-up-your-frame |
| `frame` | Your-frame |
| `buttons`, `light` | Your-frame, that section |
| `wifi` | Troubleshooting, the Wi-Fi section |
| `troubleshooting` | Troubleshooting |
| `account` | Troubleshooting, Your account |
| `move` | Troubleshooting, Move, give away, or reset a frame |
| `settings` | Settings |
| `collage`, `quiet-hours` | Settings, that part |
| `add` | Add-a-frame |
| `detection-sources` | Detection-sources |
| `birdweather`, `birdnet-pi`, `birdnet-go` | Detection-sources, that section |
| `source-problems` | Troubleshooting, Detection sources |
| `screens` | Other-screens |
| `ai` | AI-illustrations |
| `build` | Quickstart |

Anything else under `/help/` goes to Home, never a 404. The Worker's test
holds the routing; `check_copy.py --wiki` reads the same JSON and fails
when a page or a heading it names is missing from the wiki checkout, since
GitHub derives anchors from headings and a reworded heading would otherwise
break a link silently.

The webapp's four wiki links (`server/templates/index.html`: DIY
instructions, Hardware guide, *Learn* on AI, the failing source's link)
become `https://featherframe.app/help/<topic>`. The README keeps its direct
wiki links: its readers are makers on GitHub. docs/STYLE.md gains the rule
(link owners through `/help`, never to the wiki), and AGENTS.md one line
pointing at it.

## Shipping

1. The wiki pages, checked with `scripts/check_copy.py --wiki`, then pushed.
2. The PR: the table, the Worker and its test, `check_copy.py`, the
   webapp's links, `unpair` freeing the kit (and its test), STYLE.md,
   AGENTS.md.
3. Deploy the site from the branch before merging: a self-hosted owner who
   pulls main gets the `/help` links at once, so the redirects must answer
   first. Then merge, then deploy Featherframe Cloud.

## Not in this

- New *learn more* links in the webapp beyond the four that exist (W-880's
  empty-states pass is where they belong).
- A help link in the welcome email or on the glass.
- The baked Wi-Fi setup card's third step ("Fill in the IP address of your
  Featherframe webapp, if not auto-detected") on a Cloud kit: a firmware
  bake, its own ticket.
- Rendering the owner pages on featherframe.app.
