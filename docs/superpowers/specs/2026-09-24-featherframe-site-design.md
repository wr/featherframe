# featherframe.app — the marketing site

Date: 24 Sep 2026. Status: design approved in conversation; awaiting spec review.

## Purpose

featherframe.app has no page today (the zone is on Cloudflare, the apex has no
record). The site sells the frame to people who have never heard of BirdNET,
takes pre-orders through the Wells Workshop shop, collects email from people
not ready to buy, and points makers at the repo.

**Customers, most to least likely:** gift buyers (a beautiful object for a
bird lover) → backyard birders and existing BirdNET owners → makers.

**Offer:** a frame fed by a public BirdWeather station near the buyer, hosting
at app.featherframe.app included, no subscription. The BirdNET-Go box is an
optional add-on that hears the buyer's own yard.

**Position:** against smart bird feeders, never named: "No camera. No app. No
subscription." Real art by a human hand, not AI, is said up front; AI
illustrations appear only in the FAQ (opt-in, off by default, marked ✦).

**Brand:** a Wells Workshop product (Workshop mark in the nav). Quiet product
voice — a museum label: short, precise, no "I", no exclamation marks.

## Copy rules

- No metonymy or AI-flavoured constructions: not "on the wall", "on the
  glass", "in walnut", not "every X you Y". Say it literally.
- No niche jargon: never "plates"; say "illustrations", "Audubon's
  paintings", or "hand-coloured engravings" where precision helps.
- Never the casual "bird" where "species" or "detection" is meant.
- Benefits, not what the eye already sees (no "walnut, glass, e-paper").
- All copy is written by the implementer verbatim from this spec or reviewed
  by Wells; a subagent never invents copy.

## Look

Direction C, "gallery", made bright: near-white page (#FAFAF9 or white),
black ink, white cards with a hairline border. Colour comes only from the art
and the walnut. No beige, greige, or aged-paper texture.

Type: Pinyon Script (the wordmark and species names, bundled OFL font from
`server/featherframe/fonts/`), IM Fell Double Pica SC (small capitals for
labels), Inter (body and UI). Mobile collapses to one column, 16 px gutter,
no horizontal scroll.

## Page, top to bottom (one page)

1. **Nav:** Wells Workshop mark + *Featherframe* in script · How it works ·
   The art · Sizes · FAQ · Sign in · **Pre-order** (button).
2. **Hero.** The frame standing on its kickstand, large, rendered in 3D (see
   below). Every ~8 s it repaints to another species. Beside it a label card
   that updates in step with each repaint:
   - species name in script; Latin name in small capitals; "Heard at 6:12
     this morning" (time per species from `species.json`)
   - small capitals: HEARD NEAR YOUR HOME
   - "Featherframe shows the species heard near your home, as John James
     Audubon painted them in 1827."
   - a 10-inch · 13-inch switch (swaps the model; 13-inch first)
   - **Pre-order** · Ships November 2026 · How it works
3. **Promise line:** "No camera. No app. No subscription. Real paintings by a
   human hand, not AI."
4. **How it works** (three steps beside the exploded drawing):
   - **It listens.** A BirdWeather station near you, or your own BirdNET-Go
     box on the porch.
   - **It finds the painting.** One of 435 illustrations from *The Birds of
     America* (1827–38), matched to the species heard.
   - **It updates the display.** E-paper holds the picture without power. The
     frame wakes, updates and sleeps, and runs for months on a charge.
5. **The art:** a grid of ~6 real renders from the pipeline (gray and colour)
   and one day's collage, each captioned in the folio type. "Each
   illustration carries its species, its Latin name and Audubon's own
   legend. At night, the day's species appear together on one sheet."
6. **Sizes (no prices):** two cards, *10-inch · sixteen grays* and *13-inch ·
   six inks*; the box: "Add a BirdNET-Go box to hear your own yard instead of
   a nearby station." "Hosting is included with every frame." Pre-order.
7. **FAQ:** Where do the detections come from? · Do I need Wi-Fi? · How long
   does the battery last? · What if Audubon never painted a species near me?
   (a lettered card by default; AI illustrations opt-in, marked ✦) · What is
   hosting, and what does it cost? (included) · Is my data private?
8. **Build your own:** "Featherframe is open source. Build one from a Seeed
   ePaper kit and run the server beside your BirdNET." → GitHub, DIY
   instructions (`github.com/wr/featherframe#shopping-list`).
9. **Footer:** "Keep me posted" email field · Sign in
   (app.featherframe.app) · Wells Workshop credit · "Illustrations after the
   Havell edition of *The Birds of America*, public domain."

FAQ answers are drafted during implementation and reviewed by Wells before
launch.

## The 3D frame

- `site/src/viewer.ts` (~250 lines): three.js scene, the GLB, a studio light,
  slow idle sway + drag to turn, no zoom.
- `site/src/epaper-refresh.ts`: copy of the shop's
  `~/Projects/shop/src/lib/epaper-refresh.ts` with one addition, an
  `onShown(index)` callback when a repaint settles, which drives the card.
  The header comment names its source.
- Models: `featherframe.glb` (10-inch) and `featherframe-13.glb` (13-inch)
  from `~/Projects/shop/public/models/` (~0.5 MB each; both include
  `featherframe_stand`). 13-inch loads first; 10-inch on first switch.
- Screen images: ~6 species rendered by our pipeline at the shop's screen
  texture size, gray for the 10-inch, six-ink dithered for the 13-inch; the
  shop's Cardinal, Blue Jay and Goldfinch are the starting set.
  `site/species.json` holds, per species: image per size, common name, Latin
  name, heard time.
- Bundled by esbuild to `site/dist/viewer.js` (three.js inside).
- **Fallback:** the page first paints a still render with the card filled.
  No WebGL or `prefers-reduced-motion` → the still stays, the card does not
  cycle. The viewer loads after first paint.

## Hosting, forms, analytics

- A separate static-assets Worker, `featherframe-site` (`site/wrangler.jsonc`,
  `assets.directory: "dist"`), custom domains `featherframe.app` and
  `www.featherframe.app` (www → apex redirect). Shares nothing with the
  hosted Worker.
- `make site` builds; `make site-deploy` builds and runs `wrangler deploy`.
- **Keep me posted:** `<form method="post"
  action="https://app.featherframe.app/api/waitlist">`; with JS it posts JSON
  and the field becomes "Thanks. We'll write when there's news." Rows land in
  D1 `waitlist` with `source: "site"`, visible in `/admin`. The existing CORS
  already allows the apex and www.
- **Pre-order** links to `https://shop.wells.ee/products/featherframe/` now,
  while the listing is still hidden; unhiding it is a separate step in the
  shop repo.
- Cloudflare Web Analytics (no cookies, no banner).
- Title, description, Open Graph image (the colour render, no Workshop logo
  in the crop), `robots.txt`, `sitemap.xml`.

## Launch order

1. Build; review locally in the browser pane (desktop + phone widths).
2. Deploy to `featherframe-site.<account>.workers.dev` for Wells to review.
3. Only on Wells's yes: add the apex and www custom domains (this publishes).

## Testing

- Playwright smoke test (`site/test/`): page loads without console errors,
  the card changes after a repaint, the still + static card with WebGL off
  and with reduced motion, the waitlist form posts (mocked).
- Visual check in the browser pane at 1280 px and 375 px.

## Out of scope

Prices on the site; a checkout of its own; a blog; a second page; naming
competitors; changing the hosted Worker (beyond the thanks-page wording, if
needed).
