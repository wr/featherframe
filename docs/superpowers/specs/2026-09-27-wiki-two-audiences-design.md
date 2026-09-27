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

## The sidebar

**Your Featherframe**

- **Set up your frame** (new, `/help/setup`). You need: Wi-Fi (2.4 GHz), a
  phone, a USB-C cable and power adapter. Plug it in; join
  **Featherframe-Setup** and choose your Wi-Fi, leaving the server address
  blank; scan the code, or enter it at cloud.featherframe.app under *Set up a
  new frame*; the setup page's email and detection source (a BirdWeather
  station found by ZIP code or town, or your own BirdNET-Pi or BirdNET-Go,
  connected afterwards); the two emails (*Welcome to Featherframe!* and the
  confirmation); hang it. A gift needs nothing from the giver. Already have
  an account: the setup page's link adds the frame to it.
- **Your frame** (new, `/help/frame`). The buttons (moved from *Build the
  frame*). The status light's colours (moved from *Status LED*). What each
  screen on the glass means: Wi-Fi setup, Connecting, Can't reach Wi-Fi, Can't
  reach server, the setup code, Add this frame on the Featherframe webapp,
  the offline mark in the corner (Low battery stays on *Battery*: a sold
  kit has none). When the picture changes and
  why it sometimes waits (quiet hours, a new species heard twice, the first
  species of the day held), and the EE02's 15-second change.
- **Troubleshooting** (reordered by symptom, `/help/troubleshooting`). The
  frame (by what the glass or the light shows; 2.4 GHz Wi-Fi), your account
  (the sign-in email, changing it), detection sources (as today), moving,
  giving away or resetting a frame (change Wi-Fi with KEY2; Remove, then the
  new owner scans the new code; erase), then *Your own server* last (mDNS,
  the other-server hijack, missing illustrations, the local database).
  *Still not working?* ends it.

**Use** (both audiences)

- Settings, Add a frame, Detection sources, Other screens (gains Featherframe
  Cloud's addresses), Species and illustrations, AI illustrations, How AI
  illustrations are made.

**Build your own**

- Quickstart (the maker's; its "frames come pre-flashed" line goes to *Set
  up your frame*), Frames, BirdNET, Build the frame (buttons move out), Status
  LED (wiring only), Battery, Flash the frame, Install the server
  (self-hosted; its Cloud section becomes one line to *Set up your frame* and
  the waitlist).

**About**: Why Featherframe, About BirdNET, Sibling projects.

**Develop**: unchanged.

Home is the same map with a line under each heading. Every wiki page stays
under about 1000 words and in the wiki's own style (numbered steps, bold UI
names, no emoji, "Next:" footers).

## Links in

`site/src/worker.ts` answers `/help` and `/help/<topic>` with a 302 to the
wiki (302, so a target can move without browsers caching the old one):

| Path | Wiki page |
|---|---|
| `/help` | Home |
| `/help/setup` | Set-up-your-frame |
| `/help/frame` | Your-frame |
| `/help/wifi` | Troubleshooting, its Wi-Fi section |
| `/help/troubleshooting` | Troubleshooting |
| `/help/settings` | Settings |
| `/help/detection-sources` | Detection-sources |
| `/help/birdweather`, `/help/birdnet-pi`, `/help/birdnet-go` | Detection-sources, that section |
| `/help/screens` | Other-screens |
| `/help/ai` | AI-illustrations |
| `/help/build` | Quickstart |

Anything else under `/help/` goes to Home, never a 404. A test holds the
table.

The webapp's six wiki links (`server/templates/index.html`) become
`https://featherframe.app/help/<topic>`. The README keeps its direct wiki
links: its readers are makers on GitHub. AGENTS.md's *Writing* section gains
one line: link owners through `/help`, never to the wiki.

## Shipping

1. The wiki pages, checked with `scripts/check_copy.py --wiki`, then pushed.
2. One PR: the redirect table and its test, the webapp's links, AGENTS.md.
3. After it merges: deploy the site (the redirects must answer before any
   `/help` link is live), then Featherframe Cloud.

## Not in this

- New *learn more* links in the webapp beyond the six that exist (W-880's
  empty-states pass is where they belong).
- A help link in the welcome email or on the glass.
- Rendering the owner pages on featherframe.app.
