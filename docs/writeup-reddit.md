# Reddit write-ups (WIP posts)

Two drafts, one per sub. Both are written as "here's where it is," not a launch.
Facts below are pulled from README/AGENTS and the git log; anything marked
[check] is something only you can confirm.

## What I read first

The other frame is u/arnegiacomo's "fugleramme": Inky Impression 13.3" (Spectra
6 color), Raspberry Pi 5, USB mic, BirdNET-Go, historic lithographs with a
European focus. 537 upvotes, 38 comments, two days old. The top comment thanks
him for public-domain art "instead of AI generated weirdness," and he says
every other bird frame he's seen uses Gemini/ChatGPT images. That's the room
you're walking into. Audubon-first is exactly what they liked. The AI fallback
is the thing they'll look for, so it's stated plainly and once, not hidden.

The questions his thread got, in order: where's the art from, is it US-usable,
how much art prep, what mic, why is it so crisp on Spectra. Yours pre-empts
the first three.

r/esp32 rules: link the source, explain what and why, schematics for custom
hardware. Top project post this month (an e-ink alarm clock, 2.4k upvotes) is
plain first person with "The hardware" / "The firmware" sections. First
comment was "is there a Git?", then resolution, refresh cadence, and IDF vs
Arduino. The draft answers all four before they ask.

The sub also just added an AI flair "for posts or code generated primarily by
AI or with AI themes." Your call whether Featherframe's code qualifies. If it
does, use the flair and say so in one line; that sub punishes being found out
much harder than it punishes the flair.

Photos: lead with the walnut frame on the dresser (Northern Flicker). Second
image the bench shot with the FPC cable and iron. r/esp32 gets the bench shot
first, the frame second.

---

## r/eink

**Title:** The other bird frame: Audubon plates on a 10.3" grayscale panel, battery ESP32, BirdNET-Pi

u/arnegiacomo posted fugleramme here two days ago and it turns out we've been
building the same thing on opposite sides of the Atlantic. His is a Pi 5 and a
color Inky with European lithographs. Mine is the North American version, and
it went a different direction on almost every hardware choice, so I figured
it was worth showing. Not done. Still on the bench half the time.

What it does: a BirdNET-Pi in the yard listens for birds. When it hears one, a
small server on the Pi pulls that species' plate from Audubon's *Birds of
America* (public domain, 1830s), crops the bird out, sets a caption in Garamond,
and renders it to the panel. The frame shows whatever called most recently,
with the time it was heard. A Tufted Titmouse at 4:55pm looks like a museum
plate that happens to know what's outside.

The panel is the 10.3" ED103TC2 from the Seeed XIAO ePaper kit, 1404x1872,
16 grays, IT8951 controller, driven by an ESP32-S3 that deep-sleeps between
checks so the frame can hang on a wall with no cable. I went monochrome on
purpose. The Audubon plates are engravings with hand color, and the engraving
survives the grayscale better than I expected. It reads as a print instead of
a screen, which was the whole point of putting it in a real frame with a mat.

Things I've learned that might be useful to anyone doing something similar:

- 16 grays is enough if you dither well. Blue-noise is fast enough to run on a
  Pi Zero; Stucki looks a bit richer but is a slow Python loop. Blue-noise is
  the default.
- Ghosting is real on a panel this size. Every image change is a full refresh,
  and I'm only repainting when the species actually changes. One of the bench
  photos has a leftover gray test square if you look.
- Audubon's names are 190 years out of date. Northern Cardinal is his
  "Cardinal Grosbeak," the junco is "Snow Bird," the Tufted Titmouse is
  "Crested Titmouse." I keep a hand-verified crosswalk of ~135 species to plate
  numbers because fuzzy name matching once handed me a Blackbird plate for a
  European Starling. Rule one is never a wrong bird.
- Some birds Audubon never painted (starlings, house sparrows, both introduced
  after his time). Those get a plain typographic plate, name set large with
  "first recorded <date>" under it. Being upfront since it came up in Arne's
  thread: there's an optional image-model fallback for exactly those gap
  species, generated once in the style of your own plate set and cached
  forever. It needs your own API key; with no key you get the typographic
  plate. The real plates are the product. I'd rather show a scan of a
  190-year-old engraving than anything a model makes, and 120 of the 135
  species do.

Still to do: a "day in review" sheet that hangs overnight listing every species
heard that day, battery life on the deep-sleep build (I have a model, not a
measurement), and a proper mat cut so the panel bezel disappears.

Happy to go into the render pipeline if anyone wants. Code is at [repo link,
check it's public] if you want to poke at it.

---

## r/esp32

**Title:** ESP32-S3 e-paper frame that fetches a pre-rendered framebuffer and deep-sleeps. Design notes, and one thing I still can't confirm.

Building a wall frame that shows the last bird my BirdNET-Pi heard as an
Audubon plate on a 10.3" 16-gray panel. Photos are the current state: the
always-awake build is what's flashed; the deep-sleep build exists and the
battery numbers for it are still a model.

The part that might interest this sub is the split. The ESP32 does almost
nothing on purpose.

**The hardware.** Seeed XIAO ePaper Kit EE03: a XIAO ESP32-S3 Plus on Seeed's
EE03 driver board, driving a 10.3" E Ink ED103TC2 (1404x1872, 16 grays,
IT8951 controller) through the board's FPC connector. 1S LiPo on the JST-PH,
charged over the XIAO's USB-C. Three user buttons on the board. No custom
PCB; the kit ships with SenseCraft firmware, which I replaced.

**The server (Python on the Pi, not the ESP32):** reads BirdNET's SQLite
read-only with a rowid cursor, renders the plate, dithers to 4bpp, packs it
into a framebuffer with a 16-byte header, and serves it at `/api/frame` with
an ETag.

**The firmware (Arduino core via PlatformIO, Seeed_GFX):** all logic in
`setup()`, `loop()` is empty. Wake, join Wi-Fi, `GET /api/frame` with
`If-None-Match`, and either get a 304 and go back to sleep, or get ~1.3 MB of
framebuffer, `pushImage` + `update()`, then sleep. Timer wake plus ext1 on
the buttons. The frame stores the ETag so it never repaints an unchanged
image. Every paint is a full refresh; on a typical day that's maybe 20, and
never overnight (quiet hours hold the image).

Why this way: the panel is 1404x1872 and I didn't want to do image work, font
rendering, or PNG decode on the MCU. The 4bpp packed format matches
Seeed_GFX's sprite exactly (2 px per byte, high nibble left, 0=black) so the
firmware just copies bytes. The panel's `setRotation()` is a no-op on this
combo, so the server sends it pre-rotated in native landscape and the firmware
rejects anything that isn't exactly 1872x1404.

Power model, not measured yet: ~100 µA deep sleep, ~8 s of Wi-Fi + HTTP per
wake at ~90 mA, ~20 refreshes/day. On a 2000 mAh 1S cell that's ~7-8 weeks at
a 15-min wake, ~14 weeks at 60 min. Wi-Fi association dominates each wake.
Below 3.45 V it stops using Wi-Fi and sleeps 4 h at a time; OTA is refused
under 3.70 V with rollback on a bad image. The always-awake dev build (polling
every 15 s) gets 4-5 days on the same cell, which is what's actually on the
wall right now.

Battery read: 10k/10k divider behind a load switch on the EE03, so the pin
sees VBAT/2. Use `analogReadMilliVolts()`, not raw `analogRead()`. On my unit
the raw counts flattened near the top of the range (same ~2360 counts at
3.87 V and 4.13 V), so a single-point scale read a full cell as 3.83 V / 55%.
The eFuse-calibrated read plus a small trim from a meter on the JST leads
fixed it.

The thing I can't confirm: whether ext1 button wake works from deep sleep at
all on this board. The keys only read while the panel's T-CON is powered, and
I power it down before sleeping. If anyone has the EE03 and has gotten a
button to wake the S3 from deep sleep, I'd like to hear how.

First boot is a WiFiManager captive portal for SSID + server URL, saved to
NVS. Hold a key 3 s to reopen it.

Firmware and server are in one repo at [repo link, check it's public].
PlatformIO, Seeed_GFX via TFT_eSPI. It's WIP; the deep-sleep branch is the
less-tested one.

---

## Before posting

- Confirm the repo is public. r/esp32's rules require a source link for
  project posts, so without one, don't post there yet.
- r/eink: comment on Arne's thread first, today, with the framed photo:
  "Same idea, same week, other side of the Atlantic. Went Audubon and
  grayscale, ESP32 on battery instead of a Pi 5." Then post your own thread
  a day or two later and link his in the first line, as drafted.
- r/esp32 will ask for the FFF header layout and how the buttons are wired.
  Have `framebuffer.py`'s struct line and the KEY0/1/2 pins ready to paste.
- Decide on the AI flair for r/esp32 before posting, not after someone asks.
- The AI-plates paragraph stays in the r/eink draft. That thread already had
  the "no AI weirdness" conversation; leaving it out and having someone find
  `genart.py` is worse than one plain sentence.
