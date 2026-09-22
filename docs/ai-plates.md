# AI image generation

Featherframe is complete without an API key. A species Audubon painted gets
his plate. A species he never painted gets a typographic plate: its name under
an empty bough.

An API key adds two things, each behind its own switch on the Featherframe page:

- **A generated plate for species Audubon never painted**, drawn in the same
  lithograph style with real Havell plates as references.
- **Menagerie-style collages**: the day's species drawn together as one scene
  instead of a grid of separate plates.

## What is bought, and when

- **A generated plate** is bought once per species and kept in
  `data/generated/` (a PNG and a JSON sidecar recording what was drawn). It is
  replaced only when you click **Regenerate**. A new detection, a settings
  change or a restart never buys it again.
- **A menagerie collage** is bought at most once a day, and that sheet is
  reused for every redraw that day: the daytime rebuilds, the overnight
  collage, the button, a settings change. It is bought again only when the
  day's species list changes: a new species heard, or one dropping out of the
  species limit. A species being heard again does not count.
- **Nothing is bought** while a switch is off or no key is entered. Turning a
  switch off keeps every plate already bought.
- **A failure** (a bad key, a timeout, an outage) is retried no sooner than
  15 minutes later. Until then a species gets its typographic plate, and the
  collage keeps whatever sheet it had.

## How a plate is made

1. **The brief.** A text model describes the species (body, diagnostic
   marks, posture) and lists the plants tied to it, with their seasons. The
   brief is written once per species and cached with the plate.
2. **Art direction.** Each draw samples its own composition: how full the
   setting is, how many figures, how still the moment, which plant. A
   regenerate is told to avoid the previous draw's choices.
3. **The image.** The image model is called through its edits endpoint with
   two or three real Havell plates as style references, chosen from the
   species' own group (waterfowl for a waterfowl, other trunk foragers for a
   woodpecker), so it works from Audubon's hand rather than an average of
   "engraving".
4. **Onto the sheet.** The result is checked as a real image and its paper
   tone matched to the panel's, as a scan is. It is not cropped: a generated
   sheet is composed to fill the frame, so a crop could only cut into the art.
5. **The caption.** The same script title, Latin name and legend lines as
   any plate. In the corner where a real plate carries its Havell number
   ("Plate CLIX"), a generated sheet carries a small ✦, never a number Audubon
   did not engrave.
6. **If anything fails**, the species gets its typographic plate and the
   15-minute cooldown starts.

## Providers and models

- **OpenAI** (default): `gpt-image-2.5-sunburst`. Chosen over `-flare` after
  a side-by-side bake-off: flare tended to pad a lone figure into a pair,
  which desynchronised the printed figure key from the drawing. Only the 2.5
  models accept the `xhigh` and `max` qualities; an older model set to one of
  those is clamped to `high`.
- **Google Gemini**: `gemini-2.5-flash-image`, with the reference plates sent
  inline.
- **Replicate**: `black-forest-labs/flux-kontext-pro` by default; one key
  covers many hosted models. Replicate has no text model for the brief.
- **Self-hosted** (AUTOMATIC1111 / ComfyUI): your own `/sdapi` endpoint, no
  key and no per-image charge.
- **A separate text provider** (optional): the brief is normally written by
  the image provider with the same key. Under Advanced you can point it at
  OpenAI, Gemini, Anthropic or a local OpenAI-compatible server (Ollama, LM
  Studio) instead. With no text model at all, plates are still drawn, without
  a species-specific brief.

## Estimated costs

Estimates from the providers' list prices at the time of writing. Your cost
depends on how many new species you add, how often the collage's species list
changes during a day, and the providers' own price changes.

OpenAI, per image, by quality:

| Quality | Per image |
|---|---|
| Low | ≈ $0.03 |
| Medium | ≈ $0.04 |
| High (default) | ≈ $0.07 |
| Extra high, Max | see OpenAI's pricing page |

Menagerie collages, at about one image a night:

| Provider | Per month |
|---|---|
| OpenAI | ≈ $1 (low) to $2 (high) |
| Google Gemini | ≈ $0.30 (low) to $1 (high) |
| Replicate | ≈ $2 to $5, by model |
| Self-hosted | nothing beyond your own electricity |

Plates for missing species cost far less: once per species, ever. Once the
Audubon set has covered your regulars, a household adds a handful of new
species a month at most, which comes to well under $1 at the prices above.

## Privacy

The key is stored only in Featherframe's own database on this device, is
never written to the code or to git, and is sent only to the provider you
chose. The page shows it masked.

What leaves the device when an image is bought: the species' common and
scientific names, the brief, and up to three downscaled Havell plates as
references. No detection history, no location, no other settings.

## Managing generated plates

The **Generated plates on file** card lists every plate bought, with the model
that drew it, when, and an estimated cost from the usage the provider
reported.

- **Regenerate** buys a fresh image for that species with a new art
  direction. The old plate is kept if the new one fails.
- **Remove** deletes the plate; the next detection of that species buys a new
  one if the switch is on.
- **Download a backup** zips every plate, sidecar and brief. Plates live only
  on this machine, so back them up before moving or reinstalling the server.
- **Restore from a backup** brings them back. It never replaces a plate on file
  with an older one.
