# Featherframe style guide

How Featherframe writes: the webapp, the frame's own screens, the README and
wiki, the marketing page, and the work around them (commits, PRs, Linear,
reports, image prompts). Every person and every agent working in this repo
follows it. The wiki's [Style guide](https://github.com/wr/featherframe/wiki/Style-guide)
page is a copy of this file; this file wins if they disagree.

## Enforcement

- `AGENTS.md` makes this guide required reading before writing any copy.
- `server/scripts/check_copy.py` checks the rules a machine can judge:
  retired names, emoji, the casual "bird" in the webapp, and jargon on the
  wiki's user pages. `tests/test_copy_style.py` runs it on the webapp, so CI
  fails on a violation.
- Before pushing the wiki, run it on the checkout:
  `./.venv/bin/python scripts/check_copy.py --wiki ../featherframe.wiki`
  (from `server/`).
- An agent that briefs a subagent writes every user-facing string itself,
  verbatim, in the brief. A subagent never invents copy.

## Voice

- **Plain and short.** Say the thing once, in the fewest ordinary words.
  Cut a sentence before you explain it.
- **Literal.** Say what happens: "the frame shows the latest detection",
  not "the latest bird lands on the glass".
- **Conventional.** Use the name every other product uses for the same
  control or pattern. Don't coin one.
- **Nothing self-evident.** Don't explain what the label, the layout or
  the screenshot already says.
- **No mechanism.** An owner reads what a thing does, never how the
  server does it.
- **Every fact kept.** Short means fewer words, not fewer facts.
- **No AI tics.** No metonymy for "shown" or "made of" ("on the wall", "on
  the glass", "in walnut"), no "every X you Y", no "not X, but Y", no
  rule-of-three lists for rhythm, no postmodifier chains ("a frame whose own
  server stops answering adopts it").
- **No colloquialisms.** Not "sells the effect", "goes home", "little
  hands".
- **No emoji**, anywhere. The page's symbols (⋯ menu, ✓ ✕ state, ⟳ refresh,
  ✦ generated) are symbols, not emoji. In tables write Yes and No.
- **American spelling** in anything an owner reads: color, gray. Code and
  internal notes may use either.
- **Typography.** Sentence case for headings, labels and buttons. En dash
  for number ranges (1–5). Curly quotes are optional; be consistent within
  a page.
- **Times, as people write them.** A 12-hour clock with AM and PM: 7:00 PM,
  6:30 AM. A span of time takes an arrow: 10:00 PM → 6:00 AM, Sunset →
  Sunrise. A recent moment is relative: just now, 4 min ago, 2 hours ago,
  yesterday. Anything older is a date: 24 Sep.

## Lexicon

Use the left column. The right column is what not to say.

| Say | Meaning | Not |
|---|---|---|
| Featherframe | The product. One word, capital F. | FeatherFrame, Feather Frame |
| Featherframe webapp, the webapp | The page an owner opens to set things up. | dashboard, web app, admin, UI |
| frame | Any screen Featherframe draws for: a kit, a TRMNL, a tablet, an e-reader. | viewer, device, display, client, primary frame, active frame |
| screen | The physical glass or device, when it matters which kind. "Other screens" is the wiki's page for tablets and e-readers. | |
| kit | A Seeed XIAO ePaper kit (EE03, EE02) running Featherframe firmware. | board (except where the board itself is meant) |
| e-paper | The screen technology. | e-ink, eInk (E Ink is a brand) |
| detection | One identification by the detector. | bird, sighting, hit |
| species | The kind of bird. "A species new today." | bird |
| illustration | The artwork a frame shows. | plate (in the webapp), image, art |
| generated illustration, AI illustration | An illustration made by image generation. | AI plate, generated plate |
| picture | What a frame shows, as a whole. "The frame shows its first picture." | image, render, output |
| Individual detections | The Content option: the latest species. | Plates, single mode, latest bird |
| Collage | The Content option: every species heard today, on one sheet. | day in review, summary, mosaic |
| quiet hours | The overnight window. During it every frame shows the day's collage. | night mode, dark mode, sleep |
| detection source | Where detections come from: BirdWeather, BirdNET-Pi, BirdNET-Go. | feed, integration |
| detector | The software that listens (BirdNET-Pi, BirdNET-Go). | |
| Region | Which book is asked first. | edition, pack |
| server | What draws the pictures, hosted or on your BirdNET device. | backend, box, container |
| hosted | Featherframe run for you at app.featherframe.app. | cloud, SaaS |
| check in | A frame asking the server for its picture. "Last checked in 5 min ago." | poll, fetch, ping |
| Add, Ignore, Forget, Remove, Pair a frame | The frame list's actions. | approve, reject, replace, claim, adopt |
| Refresh, Repaint, Manual override | The illustration's tools. | rerender, force, test detection |
| household settings, Settings | Settings shared by every frame: the **Settings** card. | global settings, config |

Proper names are spelled as their owners spell them: BirdNET, BirdNET-Pi,
BirdNET-Go, BirdWeather, TRMNL, Kobo, Kindle, Wi-Fi, Audubon's *The Birds of
America*, Gould's *The Birds of Europe*, *The Birds of Australia*, *The Birds
of Asia*, *The Birds of Great Britain*.

**"Bird."** In the webapp, never use "bird" for a detection, a species or an
update: "a new detection reaches the frame", not "a new bird shows up".
Names and titles that contain the word are fine. The wiki, README and
marketing page may say "bird" where a person would.

**"Plate."** A plate is a numbered sheet of a printed book. The webapp never
says it (an owner sees illustrations). Docs may, once the page has said what
a plate is, and the corner mark cites one ("Plate CLIX").

## The webapp

- **Setting names are plain nouns**: Name, Content, Rotation, Power, Update
  interval, Screen size. Not verbs or coinages ("Shows", "Check every",
  "Look").
- **Options are named for what they are**: Individual detections, Collage,
  USB, Battery. Not house metaphors ("Plates", "Paper").
- **A hint exists only if a person would miss it.** One short sentence at
  most. Most settings have none. If a hint restates the label, delete it.
- **A setting that needs a paragraph should be cut**, not explained.
- **An empty field beats a magic value**: a blank with the placeholder "No
  limit", not "0 shows every species".
- **Related settings live together** (the AI collage switch sits with the
  collage settings).
- **Buttons are the action**: Save, Add, Remove, Check for updates. A
  confirmation asks the question and says the consequence in one line:
  "Delete the stored key? Generated illustrations are kept."
- **States are one word or a short phrase**: Overdue, Battery low, Unsaved
  changes, Up to date.
- **Empty states invite**: "No frames yet. There are two ways to start."
  Never apologize.
- **Errors say what to do**, not what broke inside.
- **No architecture.** A section never opens with an intro about how the
  system works.

## The frame's own screens

What the glass says when there is no picture: boot, pairing, errors, toasts.

- Engraved capitals, a few words: "ADD THIS FRAME ON THE FEATHERFRAME
  WEBAPP", "BATTERY LOW, CHARGE ME".
- Say what to do next, never the fault code.
- The wordmark is the plate title's script, everywhere.
- A change to baked text means re-baking the screens
  (`firmware/tools/screens/bake_screens.py`) for every panel.

## README and wiki

- Match the existing pages before writing a new one. Read two or three.
- **Lead each step with the action.** One action per numbered step.
  "In your Featherframe webapp, go to **Frames**." "Click **Add**."
- **UI names in bold**, exactly as the webapp spells them.
- Full short sentences. Short and friendly means fewer, shorter steps, not a
  chattier register ("Pick one", "Done." are out).
- Plain headings, never questions.
- Multi-line code blocks, one command per line, not `&&` chains.
- GitHub callouts (`> [!NOTE]`) only where they carry weight.
- End a step-by-step page with "Next: [Page](Page)."
- Under about 1000 words a page.
- **No jargon on user pages**: ETag, mDNS, venv, dither, framebuffer, NVS,
  endpoint. The Develop pages (How it works, Porting to another panel,
  Development) may use them.
- The wiki's first-person pages (Why Featherframe, AI illustrations) are
  Wells's voice; keep them his.

## Marketing (featherframe.app)

- Sell what it does for the owner, not what is plainly visible in the photo
  ("WALNUT, GLASS, E-PAPER" says nothing).
- Say "illustrations" or "Audubon's paintings", not "plates".
- Literal sentences: "it shows the latest bird your station heard", not "it
  brings your backyard to the wall".
- The Voice rules above apply in full, the AI tics above all.

## Commits, PRs, Linear and reports

- **Commit and PR titles** say the outcome in product terms, as a sentence
  without a final period, with the ticket in parentheses:
  "A detection with no Latin name prints no lone period (#256)",
  "Status LED: one addressable RGB pixel on GPIO39 says what the frame is
  doing (W-876)".
- **PR bodies** say what changed and why, how it was verified, and what is
  left. Detail lives here, not in chat.
- **Linear tickets** are cited as ID and title together in inline code:
  `W-876 Status LED`. Never a bare ID.
- **Reports to Wells** are six lines or fewer: what shipped with its link,
  what Wells must do, what is broken. No walkthroughs, no restating the
  spec.
- Explain options in product terms, not engineering ones.
- Dates are absolute: 25 Sep 2026, never "yesterday".
- **Code comments** match the surrounding code: say why, in plain sentences,
  and name the ticket that set a rule (`W-821`).

## Image-generation prompts

- State principles, never examples. A model treats any named example as a
  target for every sample ("chewed leaves" ravages every leaf).
- Never negate ("not a moth" plants the moth). Assert what the subject is.
- Frequency lives in the sampler, not the sentence: a clause that is always
  present is applied always.
- Before adding a clause to fix a fault, look for the clause, or the code,
  that causes it.

## Checklist before shipping copy

1. Would a person miss this sentence? If not, cut it.
2. Is every noun from the lexicon?
3. Does it say what happens, literally?
4. Is anything explained that the label or layout already says?
5. Run `check_copy.py` (with `--wiki` for wiki changes).
