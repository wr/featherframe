# featherframe.app Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A one-page marketing site at featherframe.app: a 3D frame on its kickstand that repaints between Audubon illustrations with a live label card beside it, then how it works, the art, sizes, FAQ, build-your-own and a "Keep me posted" form.

**Architecture:** Static HTML/CSS in `site/public/`, one esbuild bundle (`site/src/main.ts`, which lazy-imports `viewer.ts` + a copy of the shop's `epaper-refresh.ts`) into `site/dist/`, served by a tiny static-assets Worker (`featherframe-site`) that also redirects www → apex. The form posts to the existing `https://app.featherframe.app/api/waitlist`.

**Tech Stack:** HTML/CSS, TypeScript, three.js 0.185, esbuild, Playwright (chromium), Node 26 built-in test runner, Cloudflare Workers static assets (wrangler).

**Spec:** `docs/superpowers/specs/2026-09-24-featherframe-site-design.md`

## Global Constraints

- All user-visible copy is copied verbatim from this plan. Never invent or reword copy; if a string is missing, stop and ask.
- Copy rules: no "plates"; no casual "bird" where "species"/"detection" is meant; no metonymy ("on the wall", "on the glass"); no "every X you Y"; benefits over what is visible. US spelling ("color").
- Look: bright. Page `#FFFFFF`, ink `#16181B`, muted `#5E6570`, hairline `#E4E6E9`, card `#FFFFFF`. No beige, greige or paper texture.
- Fonts (Google Fonts): Pinyon Script (wordmark, species names), IM Fell Double Pica SC (small-caps labels), Inter 400/500/600 (everything else).
- Mobile: one column at ≤ 820 px, 16 px side gutter, no horizontal scroll.
- The page works without JavaScript except the 3D and the JSON form submit.
- Never add the `featherframe.app` / `www.featherframe.app` custom domains until Wells says yes in chat (Task 9).
- Every command runs from the worktree root unless a `cd` is shown. The site's package is `site/` with its own `package.json`.

## File map

```
site/
  package.json            deps + scripts (build, test, deploy)
  build.mjs               copies public/ → dist/, bundles src/main.ts, injects analytics
  wrangler.jsonc          Worker featherframe-site, assets = dist/
  playwright.config.ts
  src/
    main.ts               first-paint script: card data, size switch, form, lazy viewer
    card.ts               pure helpers: heardText(), species data types
    viewer.ts             three.js stage: GLB, lights, sway/drag, screen refresh
    epaper-refresh.ts     copy of shop's, + onShown and holdMs options
    worker.ts             www → apex redirect, else static assets
  public/
    index.html            the page
    styles.css
    species.json          species + per-size model/screens
    models/               featherframe.glb, featherframe-13.glb, screens/*.jpg
    img/                  poster-10.webp, poster-13.webp, exploded.webp, art/*.webp, og.jpg, ww.svg
    robots.txt, sitemap.xml, favicon.svg
  scripts/
    posters.mjs           Playwright capture of the posters from the viewer
  test/
    card.test.ts          node:test for card.ts
    worker.test.ts        node:test for worker.ts
    species.test.ts       node:test: species.json ↔ files on disk
    site.spec.ts          Playwright page tests
Makefile                  + site, site-test, site-deploy targets
hosted/src/pages.ts       waitlist thanks wording
```

---

### Task 1: Scaffold, build and a first passing page test

**Files:**
- Create: `site/package.json`, `site/build.mjs`, `site/playwright.config.ts`, `site/public/index.html`, `site/public/styles.css`, `site/src/main.ts`, `site/test/site.spec.ts`, `site/.gitignore`
- Modify: `Makefile` (append targets)

**Interfaces:**
- Produces: `npm run build` → `site/dist/` (static files + `main.js` + chunks); `npm test` runs node tests then Playwright against `python3 -m http.server 4321 -d dist`.

- [ ] **Step 1: Create `site/package.json`**

```json
{
  "name": "featherframe-site",
  "private": true,
  "type": "module",
  "scripts": {
    "build": "node build.mjs",
    "test:unit": "node --test test/*.test.ts",
    "test:e2e": "playwright test",
    "test": "npm run build && npm run test:unit && npm run test:e2e",
    "posters": "npm run build && node scripts/posters.mjs",
    "deploy": "npm run build && wrangler deploy"
  },
  "dependencies": { "three": "^0.185.1" },
  "devDependencies": {
    "@playwright/test": "^1.56.0",
    "@types/three": "^0.185.4",
    "esbuild": "^0.25.0",
    "wrangler": "^4.40.0"
  }
}
```

- [ ] **Step 2: Create `site/.gitignore`**

```
node_modules/
dist/
test-results/
playwright-report/
analytics.json
```

- [ ] **Step 3: Create `site/build.mjs`**

```js
// Builds featherframe.app into dist/: public/ copied as is, src/main.ts bundled
// (the viewer is a lazy chunk), and the Web Analytics beacon injected when
// analytics.json ({"token": "…"}) exists.
import { build } from 'esbuild';
import { cpSync, existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs';

const here = new URL('.', import.meta.url).pathname;
rmSync(`${here}dist`, { recursive: true, force: true });
cpSync(`${here}public`, `${here}dist`, { recursive: true });

await build({
  entryPoints: [`${here}src/main.ts`],
  bundle: true,
  splitting: true,
  format: 'esm',
  minify: true,
  target: 'es2020',
  outdir: `${here}dist`,
  logLevel: 'warning',
});

const cfg = `${here}analytics.json`;
if (existsSync(cfg)) {
  const { token } = JSON.parse(readFileSync(cfg, 'utf8'));
  const page = `${here}dist/index.html`;
  const beacon = `<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='{"token": "${token}"}'></script>`;
  writeFileSync(page, readFileSync(page, 'utf8').replace('</body>', `${beacon}\n</body>`));
}
```

- [ ] **Step 4: Create `site/playwright.config.ts`**

```ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'test',
  testMatch: '*.spec.ts',
  timeout: 45_000,
  use: {
    baseURL: 'http://127.0.0.1:4321',
    launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] },
  },
  webServer: {
    command: 'python3 -m http.server 4321 --bind 127.0.0.1 -d dist',
    url: 'http://127.0.0.1:4321/',
    reuseExistingServer: true,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
```

- [ ] **Step 5: Write the failing test `site/test/site.spec.ts`**

```ts
import { expect, test } from '@playwright/test';

test('the page loads without console errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/');
  await expect(page).toHaveTitle('Featherframe · Wells Workshop');
  await expect(page.locator('.wordmark').first()).toHaveText('Featherframe');
  expect(errors).toEqual([]);
});
```

- [ ] **Step 6: Install and run it to see it fail**

```bash
cd site && npm install && npx playwright install chromium && npm test
```

Expected: build fails (no `public/`, no `src/main.ts`).

- [ ] **Step 7: Create the minimal page**

`site/public/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Featherframe · Wells Workshop</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IM+Fell+Double+Pica+SC&family=Inter:wght@400;500;600&family=Pinyon+Script&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="styles.css">
  <script type="module" src="main.js"></script>
</head>
<body>
  <header class="nav">
    <a class="brand" href="/"><span class="wordmark">Featherframe</span></a>
  </header>
</body>
</html>
```

`site/public/styles.css`:

```css
:root {
  --page: #FFFFFF; --ink: #16181B; --muted: #5E6570; --hair: #E4E6E9; --card: #FFFFFF;
  --script: 'Pinyon Script', 'Snell Roundhand', cursive;
  --caps: 'IM Fell Double Pica SC', Georgia, serif;
  --sans: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
  --gutter: 16px; --max: 1180px;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--page); color: var(--ink); font: 400 17px/1.6 var(--sans); overflow-x: hidden; }
.wordmark { font: 400 34px/1 var(--script); color: var(--ink); }
```

`site/src/main.ts`:

```ts
// featherframe.app's first-paint script. Grows in later tasks.
export {};
```

- [ ] **Step 8: Run the test to see it pass**

```bash
cd site && npm test
```

Expected: 1 passed.

- [ ] **Step 9: Add Makefile targets** (append to the root `Makefile`)

```make
# featherframe.app, the marketing site (site/)
site:
	cd site && npm install && npm run build
site-test:
	cd site && npm install && npm test
site-deploy:
	cd site && npm install && npm run deploy
.PHONY: site site-test site-deploy
```

- [ ] **Step 10: Commit**

```bash
git add Makefile site/package.json site/package-lock.json site/.gitignore site/build.mjs site/playwright.config.ts site/public site/src site/test
git commit -m "featherframe.app: scaffold, build and first page test"
```

---

### Task 2: Assets and species data

**Files:**
- Create: `site/public/species.json`, `site/public/models/*`, `site/public/img/*`, `site/public/favicon.svg`, `site/test/species.test.ts`

**Interfaces:**
- Produces `species.json`:

```ts
interface SiteData {
  species: { name: string; latin: string; heard: string /* "HH:MM", 24 h */ }[];
  sizes: Record<'13' | '10', {
    label: string;              // "13-inch" | "10-inch"
    model: string;              // path under public/
    poster: string;             // path under public/
    waveform: 'spectra6' | 'gc16';
    screens: string[];          // one per species, same order
  }>;
}
```

- [ ] **Step 1: Write the failing test `site/test/species.test.ts`**

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const root = new URL('../public/', import.meta.url).pathname;
const data = JSON.parse(readFileSync(`${root}species.json`, 'utf8'));

test('every size has one screen per species, and every file exists', () => {
  assert.ok(data.species.length >= 4);
  for (const key of ['13', '10']) {
    const s = data.sizes[key];
    assert.equal(s.screens.length, data.species.length, `size ${key}`);
    for (const f of [s.model, ...s.screens]) assert.ok(existsSync(root + f), f);
  }
});

test('heard times are HH:MM', () => {
  for (const s of data.species) assert.match(s.heard, /^\d{2}:\d{2}$/);
});

test('the art and drawings exist', () => {
  for (const f of ['img/exploded.webp', 'img/ww.svg', 'img/og.jpg', 'favicon.svg',
    'img/art/nighthawk.webp', 'img/art/cardinal.webp', 'img/art/blue-jay.webp',
    'img/art/goldfinch.webp', 'img/art/robin.webp', 'img/art/carolina-wren.webp',
    'img/art/collage.webp']) {
    assert.ok(existsSync(root + f), f);
  }
});
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd site && npm run test:unit`
Expected: FAIL, `species.json` not found.

- [ ] **Step 3: Copy the models and screens from the shop**

```bash
S=~/Projects/shop/public/models
D=site/public/models
mkdir -p $D/screens
cp $S/featherframe.glb $S/featherframe-13.glb $D/
for b in "" -cardinal -blue-jay -goldfinch; do
  cp "$S/featherframe-screen$b.jpg" "$D/screens/10$b.jpg"
  cp "$S/featherframe-13-screen$b.jpg" "$D/screens/13$b.jpg"
done
mv $D/screens/10.jpg $D/screens/10-nighthawk.jpg
mv $D/screens/13.jpg $D/screens/13-nighthawk.jpg
ls $D $D/screens
```

Expected: 2 GLBs, 8 JPGs named `{10,13}-{nighthawk,cardinal,blue-jay,goldfinch}.jpg`.

- [ ] **Step 4: Make the images**

```bash
T=~/Projects/shop/scripts/addon-models/textures
O=/Users/wells/Projects/featherframe/test_output
A=site/public/img/art
mkdir -p $A
cwebp -quiet -q 82 -resize 900 0 $T/plate-color.jpg -o $A/nighthawk.webp
cwebp -quiet -q 82 -resize 900 0 $T/plate-color-cardinal.jpg -o $A/cardinal.webp
cwebp -quiet -q 82 -resize 900 0 $T/plate-color-blue-jay.jpg -o $A/blue-jay.webp
cwebp -quiet -q 82 -resize 900 0 $T/plate-color-goldfinch.jpg -o $A/goldfinch.webp
cwebp -quiet -q 82 -resize 900 0 $O/american_robin.png -o $A/robin.webp
cwebp -quiet -q 82 -resize 900 0 $O/carolina_wren.png -o $A/carolina-wren.webp
cwebp -quiet -q 82 -resize 900 0 $O/collage_6.png -o $A/collage.webp
cwebp -quiet -q 85 -resize 1600 0 -alpha_q 90 ~/Projects/shop/src/assets/drawings/featherframe-exploded.png -o site/public/img/exploded.webp
cp ~/Projects/shop/public/favicon.svg site/public/img/ww.svg
cp /Users/wells/Projects/featherframe/server/static/favicon.svg site/public/favicon.svg 2>/dev/null || ls server/static | grep -i fav
```

If the last `cp` finds no `favicon.svg`, copy whichever favicon file `ls` shows (the script F) and name it `favicon.svg` only if it is an SVG; otherwise copy it as `favicon.png` and change the test's `favicon.svg` to `favicon.png`.

Open `site/public/img/ww.svg` and confirm it is the Wells Workshop mark (the red/blue W). If it is not, find the mark in `~/Projects/shop/src` (`grep -rl "Wells Workshop" ~/Projects/shop/src/components | head`) and use that SVG.

Stand-in posters until Task 5 renders the real ones (so the page never 404s):

```bash
/Users/wells/Projects/featherframe/server/.venv/bin/python - <<'EOF2'
from PIL import Image
Image.open('/Users/wells/Projects/shop/public/og/featherframe--color-13.png').crop((600, 0, 1200, 630)).save('/tmp/p13.png')
EOF2
cwebp -quiet -q 82 /tmp/p13.png -o site/public/img/poster-13.webp
cwebp -quiet -q 82 ~/Projects/shop/src/assets/renders/featherframe.png -o site/public/img/poster-10.webp
```

OG image (1200×630, the colour render without the Workshop logo — the render sits in the right half of the shop's OG image):

```bash
/Users/wells/Projects/featherframe/server/.venv/bin/python - <<'EOF'
from PIL import Image
src = Image.open('/Users/wells/Projects/shop/public/og/featherframe--color-13.png').convert('RGB')
art = src.crop((600, 0, 1200, 630))
out = Image.new('RGB', (1200, 630), 'white')
out.paste(art, (300, 0))
out.save('site/public/img/og.jpg', quality=86)
EOF
```

- [ ] **Step 5: Write `site/public/species.json`**

Heard times are the shop's `plates.py` times (the screens show those same times).

```json
{
  "species": [
    { "name": "Common Nighthawk", "latin": "Chordeiles minor", "heard": "08:14" },
    { "name": "Northern Cardinal", "latin": "Cardinalis cardinalis", "heard": "09:02" },
    { "name": "Blue Jay", "latin": "Cyanocitta cristata", "heard": "10:37" },
    { "name": "American Goldfinch", "latin": "Spinus tristis", "heard": "11:51" }
  ],
  "sizes": {
    "13": {
      "label": "13-inch",
      "model": "models/featherframe-13.glb",
      "poster": "img/poster-13.webp",
      "waveform": "spectra6",
      "screens": ["models/screens/13-nighthawk.jpg", "models/screens/13-cardinal.jpg", "models/screens/13-blue-jay.jpg", "models/screens/13-goldfinch.jpg"]
    },
    "10": {
      "label": "10-inch",
      "model": "models/featherframe.glb",
      "poster": "img/poster-10.webp",
      "waveform": "gc16",
      "screens": ["models/screens/10-nighthawk.jpg", "models/screens/10-cardinal.jpg", "models/screens/10-blue-jay.jpg", "models/screens/10-goldfinch.jpg"]
    }
  }
}
```

(`poster` files are made in Task 5; the test does not check them yet.)

- [ ] **Step 6: Run the tests**

Run: `cd site && npm run test:unit`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add site/public site/test/species.test.ts
git commit -m "featherframe.app: models, screens, art and species data"
```

---

### Task 3: The page — every section, with its copy, responsive

**Files:**
- Modify: `site/public/index.html`, `site/public/styles.css`
- Test: `site/test/site.spec.ts`

**Interfaces:**
- Produces DOM hooks used by Tasks 4–6:
  - `#stage` (figure) containing `img.poster` and, added by the viewer, `canvas`
  - `#label` card: `.label-name`, `.label-latin`, `.label-heard`; size switch `button[data-size="13"]`, `button[data-size="10"]` with `aria-pressed`
  - `#keep-posted` form: `input[name=email]`, `button[type=submit]`, `.form-note` (status text, `aria-live="polite"`)
- Links: Pre-order → `https://shop.wells.ee/products/featherframe/`; Sign in → `https://app.featherframe.app/`; GitHub → `https://github.com/wr/featherframe`; DIY → `https://github.com/wr/featherframe#shopping-list`.

- [ ] **Step 1: Add failing tests to `site/test/site.spec.ts`**

```ts
test('every section and its key copy is there', async ({ page }) => {
  await page.goto('/');
  for (const id of ['how', 'art', 'sizes', 'faq', 'build']) await expect(page.locator(`#${id}`)).toBeVisible();
  await expect(page.locator('.promise')).toHaveText('No camera. No app. No subscription. Real paintings by a human hand, not AI.');
  await expect(page.locator('#label .label-kicker')).toHaveText('Heard near your home');
  await expect(page.locator('#label .label-name')).toHaveText('Common Nighthawk');
  await expect(page.locator('#label .label-heard')).toHaveText('Heard at 8:14 this morning');
  const preorder = page.getByRole('link', { name: 'Pre-order' }).first();
  await expect(preorder).toHaveAttribute('href', 'https://shop.wells.ee/products/featherframe/');
  await expect(page.getByRole('link', { name: 'Sign in' }).first()).toHaveAttribute('href', 'https://app.featherframe.app/');
  const body = (await page.locator('body').innerText()).toLowerCase();
  for (const banned of ['plate', 'on the wall', 'on the glass']) expect(body).not.toContain(banned);
});

test('no horizontal scroll on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(sw).toBeLessThanOrEqual(cw);
});
```

- [ ] **Step 2: Run to see them fail**

Run: `cd site && npm test`
Expected: the two new tests FAIL.

- [ ] **Step 3: Replace `<body>` in `site/public/index.html`**

Also add to `<head>` (after the title):

```html
  <meta name="description" content="A framed e-paper display that shows the species heard near your home, as John James Audubon painted them in 1827.">
  <link rel="icon" href="favicon.svg">
```

Body:

```html
<body>
  <header class="nav">
    <a class="brand" href="/" aria-label="Featherframe, by Wells Workshop">
      <img src="img/ww.svg" alt="" width="28" height="28"><span class="wordmark">Featherframe</span>
    </a>
    <nav class="links">
      <a href="#how">How it works</a><a href="#art">The art</a><a href="#sizes">Sizes</a><a href="#faq">FAQ</a>
      <a href="https://app.featherframe.app/">Sign in</a>
    </nav>
    <a class="btn" href="https://shop.wells.ee/products/featherframe/">Pre-order</a>
  </header>

  <main>
    <section class="hero">
      <figure id="stage" class="stage">
        <img class="poster" src="img/poster-13.webp" alt="Featherframe, a walnut frame on its kickstand, showing Audubon's Common Nighthawk" width="1200" height="1400">
      </figure>
      <aside id="label" class="label" aria-live="polite">
        <p class="label-kicker">Heard near your home</p>
        <h1 class="label-name">Common Nighthawk</h1>
        <p class="label-latin">Chordeiles minor</p>
        <p class="label-heard">Heard at 8:14 this morning</p>
        <p class="label-lede">Featherframe shows the species heard near your home, as John James Audubon painted them in 1827.</p>
        <div class="sizes-switch" role="group" aria-label="Size">
          <button type="button" data-size="13" aria-pressed="true">13-inch</button>
          <button type="button" data-size="10" aria-pressed="false">10-inch</button>
        </div>
        <div class="label-actions">
          <a class="btn" href="https://shop.wells.ee/products/featherframe/">Pre-order</a>
          <a class="quiet" href="#how">How it works</a>
        </div>
        <p class="label-ships">Ships November 2026</p>
      </aside>
    </section>

    <p class="promise">No camera. No app. No subscription. Real paintings by a human hand, not AI.</p>

    <section id="how" class="how">
      <img class="exploded" src="img/exploded.webp" alt="The parts of a Featherframe: glass, mat, e-paper display, walnut frame, backing and electronics" loading="lazy" width="1600" height="1280">
      <div>
        <h2>How it works</h2>
        <ol class="steps">
          <li><h3>It listens.</h3><p>A BirdWeather station near you, or your own BirdNET-Go box on the porch.</p></li>
          <li><h3>It finds the painting.</h3><p>One of 435 illustrations from <i>The Birds of America</i> (1827–38), matched to the species heard.</p></li>
          <li><h3>It updates the display.</h3><p>E-paper holds the picture without power. The frame wakes, updates and sleeps, and runs for months on a charge.</p></li>
        </ol>
      </div>
    </section>

    <section id="art" class="art">
      <h2>The art</h2>
      <p class="section-lede">Each illustration carries its species, its Latin name and Audubon's own legend. At night, the day's species appear together on one sheet.</p>
      <div class="art-grid">
        <figure><img src="img/art/nighthawk.webp" alt="Audubon's Common Nighthawk" loading="lazy" width="900" height="1200"><figcaption><span class="script">Common Nighthawk</span><span class="caps">Chordeiles minor</span></figcaption></figure>
        <figure><img src="img/art/cardinal.webp" alt="Audubon's Northern Cardinal" loading="lazy" width="900" height="1200"><figcaption><span class="script">Northern Cardinal</span><span class="caps">Cardinalis cardinalis</span></figcaption></figure>
        <figure><img src="img/art/robin.webp" alt="Audubon's American Robin, in gray" loading="lazy" width="900" height="1200"><figcaption><span class="script">American Robin</span><span class="caps">Turdus migratorius</span></figcaption></figure>
        <figure><img src="img/art/blue-jay.webp" alt="Audubon's Blue Jay" loading="lazy" width="900" height="1200"><figcaption><span class="script">Blue Jay</span><span class="caps">Cyanocitta cristata</span></figcaption></figure>
        <figure><img src="img/art/carolina-wren.webp" alt="Audubon's Carolina Wren, in gray" loading="lazy" width="900" height="1200"><figcaption><span class="script">Carolina Wren</span><span class="caps">Thryothorus ludovicianus</span></figcaption></figure>
        <figure><img src="img/art/goldfinch.webp" alt="Audubon's American Goldfinch" loading="lazy" width="900" height="1200"><figcaption><span class="script">American Goldfinch</span><span class="caps">Spinus tristis</span></figcaption></figure>
        <figure class="wide"><img src="img/art/collage.webp" alt="A collage of the day's species on one sheet" loading="lazy" width="900" height="1200"><figcaption><span class="script">A day's collage</span><span class="caps">Drawn each night</span></figcaption></figure>
      </div>
    </section>

    <section id="sizes" class="sizes">
      <h2>Two sizes</h2>
      <div class="size-cards">
        <article class="card"><p class="caps">10-inch · sixteen grays</p><p>Sixteen shades of gray on a 10.3-inch display, as crisp as an engraving.</p></article>
        <article class="card"><p class="caps">13-inch · six inks</p><p>Six inks on a 13.3-inch display, in Audubon's own colors.</p></article>
      </div>
      <p class="box-note"><b>BirdNET-Go box.</b> Add a BirdNET-Go box to detect the species in your own yard instead of at a nearby station.</p>
      <p class="box-note">Hosting is included with every frame.</p>
      <a class="btn" href="https://shop.wells.ee/products/featherframe/">Pre-order</a>
    </section>

    <section id="faq" class="faq">
      <h2>Questions</h2>
      <details><summary>Where do the detections come from?</summary><p>From BirdWeather, a public network of listening stations. You choose a station near you when you set up the frame. With a BirdNET-Go box, they come from a microphone at your own home instead.</p></details>
      <details><summary>Do I need Wi-Fi?</summary><p>Yes. The frame joins your Wi-Fi to fetch each new illustration. After setup, it needs nothing else.</p></details>
      <details><summary>How long does the battery last?</summary><p>Months between charges, depending on how often it updates. It charges over USB-C, and it can also stay plugged in.</p></details>
      <details><summary>What if Audubon never painted a species near me?</summary><p>Audubon painted 435 species, which covers most common species in North America. For the rest, the frame shows the species' name, set in the same type as the illustrations. You can choose to have an AI draw an illustration in Audubon's style instead, using your own OpenAI key. It's off by default, and every AI illustration is marked ✦.</p></details>
      <details><summary>What is hosting, and what does it cost?</summary><p>Hosting is the service that picks each illustration and sends it to your frame. It's included with every frame, with no subscription. If you'd rather run it yourself, the software is open source.</p></details>
      <details><summary>Is my data private?</summary><p>The frame has no camera and no microphone. It only receives pictures. Your account holds your frame's settings and the species it has shown. Nothing is sold or shared.</p></details>
    </section>

    <section id="build" class="build">
      <h2>Build your own</h2>
      <p>Featherframe is open source. Build one from a Seeed ePaper kit and run the server beside your BirdNET.</p>
      <p class="build-links"><a class="quiet" href="https://github.com/wr/featherframe">GitHub</a><a class="quiet" href="https://github.com/wr/featherframe#shopping-list">DIY instructions</a></p>
    </section>
  </main>

  <footer class="footer">
    <form id="keep-posted" class="keep-posted" method="post" action="https://app.featherframe.app/api/waitlist">
      <label for="email">Keep me posted</label>
      <div class="field"><input id="email" name="email" type="email" autocomplete="email" placeholder="you@example.com" required><button type="submit">Sign up</button></div>
      <p class="form-note" aria-live="polite"></p>
    </form>
    <div class="footer-meta">
      <p><a href="https://app.featherframe.app/">Sign in</a> · <a href="https://shop.wells.ee/">Wells Workshop</a></p>
      <p class="muted">Illustrations after the Havell edition of <i>The Birds of America</i>, public domain.</p>
    </div>
  </footer>
</body>
```

- [ ] **Step 4: Append the layout to `site/public/styles.css`**

```css
a { color: inherit; }
h1, h2, h3 { margin: 0; font-weight: 600; letter-spacing: -0.01em; }
h2 { font-size: clamp(28px, 3.4vw, 40px); line-height: 1.1; margin-bottom: 16px; }
h3 { font-size: 18px; }
.caps, .label-kicker, .label-latin { font-family: var(--caps); text-transform: uppercase; letter-spacing: 0.2em; font-size: 13px; color: var(--muted); }
.script { font-family: var(--script); font-size: 30px; line-height: 1.1; }
.muted { color: var(--muted); }
.btn { display: inline-block; background: var(--ink); color: #fff; text-decoration: none; font: 500 15px/1 var(--sans); padding: 12px 20px; border-radius: 999px; }
.quiet { color: var(--ink); text-decoration: underline; text-underline-offset: 4px; text-decoration-color: var(--hair); }

.nav { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 24px; padding: 14px max(var(--gutter), calc((100vw - var(--max)) / 2)); background: rgba(255,255,255,.9); backdrop-filter: saturate(1.4) blur(10px); border-bottom: 1px solid var(--hair); }
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none; }
.links { display: flex; gap: 20px; margin-left: auto; font-size: 15px; }
.links a { text-decoration: none; color: var(--muted); }
.links a:hover { color: var(--ink); }

main, .footer { max-width: var(--max); margin: 0 auto; padding: 0 var(--gutter); }
section { padding: 72px 0; border-top: 1px solid var(--hair); }

.hero { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); gap: 40px; align-items: center; border-top: 0; padding-top: 32px; }
.stage { position: relative; margin: 0; aspect-ratio: 6 / 7; }
.stage .poster, .stage canvas { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
.stage canvas { opacity: 0; transition: opacity .6s ease; touch-action: pan-y; cursor: grab; }
.stage.live canvas { opacity: 1; }
.stage.live .poster { opacity: 0; transition: opacity .6s ease; }
.label { background: var(--card); border: 1px solid var(--hair); border-radius: 4px; padding: 28px 28px 24px; max-width: 420px; }
.label-kicker { margin: 0 0 14px; }
.label-name { font: 400 46px/1.05 var(--script); letter-spacing: 0; }
.label-latin { margin: 8px 0 4px; }
.label-heard { margin: 0 0 18px; color: var(--muted); font-size: 15px; }
.label-lede { margin: 0 0 20px; }
.label.changing .label-name, .label.changing .label-latin, .label.changing .label-heard { opacity: 0; }
.label-name, .label-latin, .label-heard { transition: opacity .35s ease; }
.sizes-switch { display: inline-flex; border: 1px solid var(--hair); border-radius: 999px; padding: 3px; margin-bottom: 20px; }
.sizes-switch button { font: 500 14px var(--sans); border: 0; background: none; padding: 7px 14px; border-radius: 999px; color: var(--muted); cursor: pointer; }
.sizes-switch button[aria-pressed="true"] { background: var(--ink); color: #fff; }
.label-actions { display: flex; align-items: center; gap: 18px; }
.label-ships { margin: 12px 0 0; font-size: 14px; color: var(--muted); }

.promise { text-align: center; font-size: clamp(18px, 2vw, 22px); margin: 8px auto 0; padding: 40px 0; max-width: 760px; }

.how { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 48px; align-items: center; }
.exploded { width: 100%; height: auto; }
.steps { list-style: none; padding: 0; margin: 0; counter-reset: s; }
.steps li { counter-increment: s; padding: 18px 0 18px 44px; position: relative; border-top: 1px solid var(--hair); }
.steps li::before { content: counter(s); position: absolute; left: 0; top: 16px; font: 400 28px/1 var(--script); }
.steps p { margin: 6px 0 0; color: var(--muted); }

.section-lede { max-width: 640px; color: var(--muted); margin: 0 0 32px; }
.art-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 28px; }
.art-grid figure { margin: 0; }
.art-grid img { width: 100%; height: auto; display: block; border: 1px solid var(--hair); }
.art-grid figcaption { display: flex; flex-direction: column; gap: 2px; margin-top: 10px; }

.size-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; margin-bottom: 24px; }
.card { border: 1px solid var(--hair); border-radius: 4px; padding: 24px; }
.card p { margin: 0; } .card .caps { margin-bottom: 10px; }
.box-note { margin: 0 0 12px; max-width: 640px; }
.sizes .btn { margin-top: 12px; }

.faq details { border-top: 1px solid var(--hair); padding: 16px 0; max-width: 760px; }
.faq summary { cursor: pointer; font-weight: 500; }
.faq details p { color: var(--muted); margin: 10px 0 0; }

.build-links { display: flex; gap: 24px; }

.footer { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 32px; padding-top: 48px; padding-bottom: 64px; border-top: 1px solid var(--hair); }
.keep-posted label { display: block; font-weight: 600; margin-bottom: 10px; }
.field { display: flex; gap: 8px; max-width: 420px; }
.field input { flex: 1; min-width: 0; font: 400 16px var(--sans); padding: 11px 14px; border: 1px solid var(--hair); border-radius: 999px; }
.field button { font: 500 15px var(--sans); padding: 11px 18px; border-radius: 999px; border: 1px solid var(--ink); background: #fff; cursor: pointer; }
.form-note { min-height: 1.6em; margin: 8px 0 0; font-size: 15px; }
.footer-meta { font-size: 15px; } .footer-meta p { margin: 0 0 8px; }

@media (max-width: 820px) {
  .links { display: none; }
  .nav .btn { margin-left: auto; }
  .hero, .how, .footer { grid-template-columns: minmax(0, 1fr); }
  .label { max-width: none; }
  .art-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  .size-cards { grid-template-columns: minmax(0, 1fr); }
  section { padding: 48px 0; }
}
```

- [ ] **Step 5: Run the tests**

Run: `cd site && npm test`
Expected: all pass.

- [ ] **Step 6: Look at it**

Open the page in the browser pane (`preview_start` with a `.claude/launch.json` entry `{"name": "site", "runtimeExecutable": "python3", "runtimeArgs": ["-m", "http.server", "4321", "--bind", "127.0.0.1", "-d", "site/dist"], "port": 4321}`), screenshot at desktop width and at `resize_window` preset `mobile`. Fix anything that overlaps or overflows.

- [ ] **Step 7: Commit**

```bash
git add site/public site/test/site.spec.ts
git commit -m "featherframe.app: every section, with its copy"
```

---

### Task 4: The label card logic and the 3D viewer

**Files:**
- Create: `site/src/card.ts`, `site/test/card.test.ts`, `site/src/viewer.ts`, `site/src/epaper-refresh.ts`
- Modify: `site/src/main.ts`, `site/test/site.spec.ts`

**Interfaces:**
- `card.ts`:
  - `export interface Species { name: string; latin: string; heard: string }`
  - `export interface Size { label: string; model: string; poster: string; waveform: 'spectra6' | 'gc16'; screens: string[] }`
  - `export interface SiteData { species: Species[]; sizes: Record<'13' | '10', Size> }`
  - `export function heardText(hhmm: string): string` — `"08:14"` → `"Heard at 8:14 this morning"`, `"13:05"` → `"Heard at 1:05 this afternoon"`, `"19:30"` → `"Heard at 7:30 this evening"`
- `epaper-refresh.ts`: shop's file + options `onShown?: (index: number) => void` and `holdMs?: number`.
- `viewer.ts`: `export async function startViewer(stage: HTMLElement, size: Size, opts: { holdMs?: number; onShown: (i: number) => void; poster?: boolean }): Promise<{ dispose(): void }>`

- [ ] **Step 1: Write the failing unit test `site/test/card.test.ts`**

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { heardText } from '../src/card.ts';

test('heardText names the part of the day', () => {
  assert.equal(heardText('08:14'), 'Heard at 8:14 this morning');
  assert.equal(heardText('12:00'), 'Heard at 12:00 this afternoon');
  assert.equal(heardText('13:05'), 'Heard at 1:05 this afternoon');
  assert.equal(heardText('19:30'), 'Heard at 7:30 this evening');
  assert.equal(heardText('00:40'), 'Heard at 12:40 last night');
});
```

- [ ] **Step 2: Run to see it fail**

Run: `cd site && npm run test:unit`
Expected: FAIL, cannot find `../src/card.ts`.

- [ ] **Step 3: Write `site/src/card.ts`**

```ts
// The label card's data and wording.
export interface Species { name: string; latin: string; heard: string }
export interface Size { label: string; model: string; poster: string; waveform: 'spectra6' | 'gc16'; screens: string[] }
export interface SiteData { species: Species[]; sizes: Record<'13' | '10', Size> }

export function heardText(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number);
  const part = h < 5 ? 'last night' : h < 12 ? 'this morning' : h < 17 ? 'this afternoon' : 'this evening';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `Heard at ${h12}:${String(m).padStart(2, '0')} ${part}`;
}
```

- [ ] **Step 4: Run the unit test**

Run: `cd site && npm run test:unit`
Expected: PASS.

- [ ] **Step 5: Copy the refresh module and add the two options**

```bash
cp ~/Projects/shop/src/lib/epaper-refresh.ts site/src/epaper-refresh.ts
```

Edit `site/src/epaper-refresh.ts`:
1. Prepend to the header comment: `// Copied from wells/shop src/lib/epaper-refresh.ts (24 Sep 2026); additions: onShown, holdMs.`
2. In `createEpaperRefresh`'s options type add:
   ```ts
     /** Called with the plate's index once a refresh to it has settled. */
     onShown?: (index: number) => void;
     /** How long each plate holds before the next refresh (default HOLD_MS). */
     holdMs?: number;
   ```
3. Right after `const { renderer, first, spec, wake, motionOk } = opts;` add `const hold = opts.holdMs ?? HOLD_MS;`
4. Replace the three uses of `HOLD_MS` inside the function body (in `whenHeld`, in `tick`'s hold check, and in `freeze`) with `hold`. Leave the `const HOLD_MS = 6000;` declaration.
5. In `tick`, change
   ```ts
        current = next();
        trim();
   ```
   to
   ```ts
        current = next();
        opts.onShown?.(current);
        trim();
   ```

Verify: `grep -n "HOLD_MS\|hold\b\|onShown" site/src/epaper-refresh.ts` shows the declaration, the one default, three `hold` uses and the `onShown` call.

- [ ] **Step 6: Write `site/src/viewer.ts`**

```ts
// The hero's frame: the GLB on its kickstand, lit by a neutral studio
// environment, swaying slowly and turnable by drag; its "screen" material is an
// e-paper refresh between the size's screens (epaper-refresh.ts).
import {
  ACESFilmicToneMapping, Box3, Color, Mesh, MeshStandardMaterial, PerspectiveCamera, PMREMGenerator,
  Quaternion, Scene, SRGBColorSpace, Vector3, WebGLRenderer, type Object3D,
} from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { createEpaperRefresh } from './epaper-refresh';
import type { Size } from './card';

const YAW = -0.42;        // three-quarter view, radians
const SWAY = 0.06;        // idle sway amplitude, radians
const SWAY_PERIOD = 14;   // seconds
const DRAG_LIMIT = 0.9;   // radians either side

const loadImage = (src: string) => new Promise<HTMLImageElement>((ok, no) => {
  const img = new Image();
  img.decoding = 'async';
  img.onload = () => ok(img);
  img.onerror = no;
  img.src = src;
});

/** Turn the model so its screen faces +Z and its long side is vertical. */
function standUp(model: Object3D, screen: Mesh) {
  model.updateMatrixWorld(true);
  const n = new Vector3().fromBufferAttribute(screen.geometry.getAttribute('normal') as never, 0)
    .transformDirection(screen.matrixWorld);
  model.quaternion.premultiply(new Quaternion().setFromUnitVectors(n.normalize(), new Vector3(0, 0, 1)));
  model.updateMatrixWorld(true);
  const box = new Box3().setFromObject(screen);
  const s = box.getSize(new Vector3());
  if (s.x > s.y) model.rotateOnWorldAxis(new Vector3(0, 0, 1), Math.PI / 2);
}

export async function startViewer(
  stage: HTMLElement, size: Size,
  opts: { holdMs?: number; onShown: (i: number) => void; poster?: boolean },
): Promise<{ dispose(): void }> {
  const canvas = document.createElement('canvas');
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: !!opts.poster });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.setClearColor(0x000000, 0);

  const scene = new Scene();
  const pmrem = new PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  const camera = new PerspectiveCamera(24, 1, 0.01, 20);

  const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  const [gltf, ...images] = await Promise.all([loader.loadAsync(size.model), ...size.screens.map(loadImage)]);
  const model = gltf.scene;
  let screen: Mesh | undefined;
  model.traverse((o) => {
    const m = o as Mesh;
    if (m.isMesh && (m.material as MeshStandardMaterial).name === 'screen') screen = m;
  });
  if (!screen) throw new Error('no screen material');
  standUp(model, screen);

  // Centre the frame and put the camera where the whole of it fits at any sway.
  const box = new Box3().setFromObject(model);
  const centre = box.getCenter(new Vector3());
  model.position.sub(centre);
  const pivot = new Scene();
  pivot.add(model);
  scene.add(pivot);
  const height = box.getSize(new Vector3()).y;
  camera.position.set(0, height * 0.06, height * 3.1);
  camera.lookAt(0, 0, 0);

  const refresh = createEpaperRefresh({
    renderer,
    first: images[0],
    spec: { waveform: size.waveform, plates: size.screens.slice(1) },
    anisotropy: renderer.capabilities.getMaxAnisotropy(),
    wake: () => {},
    motionOk: () => !document.hidden,
    holdMs: opts.holdMs,
    onShown: opts.onShown,
  });
  const mat = screen.material as MeshStandardMaterial;
  mat.map = refresh.texture;
  mat.emissiveMap = refresh.texture;
  mat.emissive = new Color(0xffffff);
  mat.emissiveIntensity = 0.35;
  mat.needsUpdate = true;

  const resize = () => {
    const { width, height: h } = stage.getBoundingClientRect();
    renderer.setSize(width, h, false);
    camera.aspect = width / h;
    camera.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(stage);
  resize();

  // Drag to turn; it eases back to the three-quarter view when let go.
  let drag = 0, dragTarget = 0, startX = 0, dragging = false;
  canvas.addEventListener('pointerdown', (e) => { dragging = true; startX = e.clientX - dragTarget * 300; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', (e) => {
    if (dragging) dragTarget = Math.max(-DRAG_LIMIT, Math.min(DRAG_LIMIT, (e.clientX - startX) / 300));
  });
  const release = () => { dragging = false; dragTarget = 0; };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);

  let visible = true;
  const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; });
  io.observe(stage);

  let raf = 0;
  const t0 = performance.now();
  const frame = (now: number) => {
    raf = requestAnimationFrame(frame);
    if (!visible || document.hidden) return;
    refresh.tick(now);
    drag += (dragTarget - drag) * 0.12;
    const sway = opts.poster ? 0 : Math.sin(((now - t0) / 1000) * (2 * Math.PI / SWAY_PERIOD)) * SWAY;
    pivot.rotation.set(0, YAW + sway + drag, 0);
    renderer.render(scene, camera);
  };
  raf = requestAnimationFrame(frame);

  stage.appendChild(canvas);
  // Show the canvas only once it has drawn, so the poster never blinks out.
  requestAnimationFrame(() => requestAnimationFrame(() => stage.classList.add('live')));

  return {
    dispose() {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
      refresh.dispose();
      pmrem.dispose();
      renderer.dispose();
      canvas.remove();
      stage.classList.remove('live');
    },
  };
}
```

If, when you look at it in Step 10, the frame's kickstand is in front of the frame (i.e. you see the back), change `YAW` to `Math.PI + YAW`; if the frame leans the wrong way or lies on its back, inspect `featherframe_stand`'s world bounding box and rotate `model` about X by ±π/2 before `standUp`. Record whichever fix you used in a comment.

- [ ] **Step 7: Write `site/src/main.ts`**

```ts
// featherframe.app's first-paint script: the label card, the size switch, the
// Keep me posted form, and — when WebGL is there and motion is welcome — the
// 3D frame, loaded after the page has painted.
import { heardText, type SiteData } from './card';

const params = new URLSearchParams(location.search);
const holdMs = params.has('hold') ? Number(params.get('hold')) : undefined;

const stage = document.getElementById('stage')!;
const poster = stage.querySelector<HTMLImageElement>('.poster')!;
const label = document.getElementById('label')!;
const nameEl = label.querySelector('.label-name')!;
const latinEl = label.querySelector('.label-latin')!;
const heardEl = label.querySelector('.label-heard')!;
const buttons = [...label.querySelectorAll<HTMLButtonElement>('.sizes-switch button')];

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const webgl = (() => {
  try { return !!document.createElement('canvas').getContext('webgl2'); } catch { return false; }
})();

let data: SiteData | undefined;
let size: '13' | '10' = '13';
let viewer: { dispose(): void } | undefined;
let generation = 0;

function showSpecies(i: number) {
  const s = data!.species[i];
  label.classList.add('changing');
  window.setTimeout(() => {
    nameEl.textContent = s.name;
    latinEl.textContent = s.latin;
    heardEl.textContent = heardText(s.heard);
    label.classList.remove('changing');
  }, 350);
}

async function mount() {
  const mine = ++generation;
  viewer?.dispose();
  viewer = undefined;
  if (!data || reduced || !webgl) return;
  const { startViewer } = await import('./viewer');
  if (mine !== generation) return;
  try {
    const v = await startViewer(stage, data.sizes[size], { holdMs, onShown: showSpecies, poster: params.has('poster') });
    if (mine !== generation) v.dispose(); else viewer = v;
  } catch (e) {
    console.warn('featherframe: 3D frame unavailable', e);
  }
}

function choose(next: '13' | '10') {
  if (next === size) return;
  size = next;
  for (const b of buttons) b.setAttribute('aria-pressed', String(b.dataset.size === size));
  if (data) poster.src = data.sizes[size].poster;
  if (data) showSpecies(0);
  void mount();
}

for (const b of buttons) b.addEventListener('click', () => choose(b.dataset.size as '13' | '10'));

const start = async () => {
  data = await (await fetch('species.json')).json();
  const want = params.get('size');
  if (want === '10') choose('10'); else void mount();
};
if (document.readyState === 'complete') void start();
else addEventListener('load', () => void start(), { once: true });
```

- [ ] **Step 8: Add Playwright tests to `site/test/site.spec.ts`**

```ts
test('the card follows the frame from one species to the next', async ({ page }) => {
  await page.goto('/?hold=300');
  await expect(page.locator('#stage canvas')).toHaveCount(1, { timeout: 20_000 });
  await expect(page.locator('#label .label-name')).toHaveText('Northern Cardinal', { timeout: 20_000 });
  await expect(page.locator('#label .label-heard')).toHaveText('Heard at 9:02 this morning');
});

test('the 10-inch switch swaps the frame', async ({ page }) => {
  await page.goto('/?hold=300');
  await page.getByRole('button', { name: '10-inch' }).click();
  await expect(page.getByRole('button', { name: '10-inch' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#stage .poster')).toHaveAttribute('src', 'img/poster-10.webp');
  await expect(page.locator('#stage canvas')).toHaveCount(1, { timeout: 20_000 });
  await expect(page.locator('#label .label-name')).toHaveText('Northern Cardinal', { timeout: 20_000 });
});

test('without WebGL the poster and first species stay', async ({ page }) => {
  await page.addInitScript(() => {
    const orig = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
      return kind.startsWith('webgl') ? null : (orig as Function).call(this, kind, ...rest);
    } as typeof orig;
  });
  await page.goto('/?hold=300');
  await page.waitForTimeout(3000);
  await expect(page.locator('#stage canvas')).toHaveCount(0);
  await expect(page.locator('#stage .poster')).toBeVisible();
  await expect(page.locator('#label .label-name')).toHaveText('Common Nighthawk');
});

test('with reduced motion nothing cycles', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/?hold=300');
  await page.waitForTimeout(3000);
  await expect(page.locator('#stage canvas')).toHaveCount(0);
  await expect(page.locator('#label .label-name')).toHaveText('Common Nighthawk');
});
```

- [ ] **Step 9: Run everything**

Run: `cd site && npm test`
Expected: all pass. If the card test times out, check `read_console_messages` in the browser pane for GLB/decoder errors first.

- [ ] **Step 10: Look at it**

In the browser pane, reload the site, screenshot the hero at desktop width once while the 13-inch is mid-refresh and once settled; switch to 10-inch and screenshot; `resize_window` preset `mobile` and screenshot. The frame must stand on its kickstand at a three-quarter angle, fill most of the stage, and never clip at the sway's extremes.

- [ ] **Step 11: Commit**

```bash
git add site/src site/test
git commit -m "featherframe.app: the 3D frame and its live label card"
```

---

### Task 5: Posters from the viewer

**Files:**
- Create: `site/scripts/posters.mjs`
- Create (generated): `site/public/img/poster-13.webp`, `site/public/img/poster-10.webp`

**Interfaces:**
- Consumes: `?poster&size=10|13` (Task 4's `main.ts` passes `poster: true`, which stops the sway and keeps the drawing buffer).

- [ ] **Step 1: Write `site/scripts/posters.mjs`**

```js
// Renders the hero's two posters from the viewer itself, so the still and the
// 3D frame are the same picture: dist/ served, each size at ?poster, the stage
// captured with a transparent background, written to public/img as WebP.
import { chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';

const here = new URL('..', import.meta.url).pathname;
const server = spawn('python3', ['-m', 'http.server', '4322', '--bind', '127.0.0.1', '-d', `${here}dist`], { stdio: 'ignore' });
await new Promise((r) => setTimeout(r, 800));
const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 1400 }, deviceScaleFactor: 1 });
  for (const size of ['13', '10']) {
    await page.goto(`http://127.0.0.1:4322/?poster&size=${size}&hold=600000`);
    await page.addStyleTag({ content: '.nav,.label,.promise,main>section:not(.hero),footer{display:none!important}.hero{display:block!important;padding:0!important}.stage{width:1200px;height:1400px;aspect-ratio:auto}.poster{display:none}' });
    await page.locator('#stage.live canvas').waitFor({ timeout: 30_000 });
    await page.waitForTimeout(1500);
    const png = `${here}dist/poster-${size}.png`;
    await page.locator('#stage').screenshot({ path: png, omitBackground: true });
    execFileSync('cwebp', ['-quiet', '-q', '86', '-alpha_q', '90', png, '-o', `${here}public/img/poster-${size}.webp`]);
    console.log(`public/img/poster-${size}.webp`);
  }
} finally {
  await browser.close();
  server.kill();
}
```

- [ ] **Step 2: Run it**

Run: `cd site && npm run posters`
Expected: prints both paths.

- [ ] **Step 3: Look at both posters**

Read `site/public/img/poster-13.webp` and `poster-10.webp` (convert to PNG with `dwebp` if the viewer can't show WebP). Each must show the whole frame on its kickstand on a transparent background, showing the Common Nighthawk.

- [ ] **Step 4: Run the tests** (the posters now exist; the page must still pass)

Run: `cd site && npm test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add site/scripts/posters.mjs site/public/img/poster-13.webp site/public/img/poster-10.webp
git commit -m "featherframe.app: posters rendered from the viewer"
```

---

### Task 6: Keep me posted

**Files:**
- Modify: `site/src/main.ts`, `site/test/site.spec.ts`, `hosted/src/pages.ts:95`

**Interfaces:**
- Consumes: `POST https://app.featherframe.app/api/waitlist`, JSON `{email}` → `{ok: true}` or `{ok: false, error}` (status 400); CORS allows `https://featherframe.app` and `https://www.featherframe.app`.

- [ ] **Step 1: Add failing tests**

```ts
test('Keep me posted signs up without leaving the page', async ({ page }) => {
  let body = '';
  await page.route('https://app.featherframe.app/api/waitlist', async (route) => {
    body = route.request().postData() || '';
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await page.goto('/');
  await page.fill('#email', 'ada@example.com');
  await page.click('#keep-posted button[type=submit]');
  await expect(page.locator('#keep-posted .form-note')).toHaveText("Thanks. We'll write when there's news.");
  expect(JSON.parse(body)).toEqual({ email: 'ada@example.com' });
  await expect(page).toHaveURL(/127\.0\.0\.1:4321\/$/);
});

test('Keep me posted says when it did not go through', async ({ page }) => {
  await page.route('https://app.featherframe.app/api/waitlist', (route) => route.abort());
  await page.goto('/');
  await page.fill('#email', 'ada@example.com');
  await page.click('#keep-posted button[type=submit]');
  await expect(page.locator('#keep-posted .form-note')).toHaveText("That didn't go through. Try again.");
});
```

- [ ] **Step 2: Run to see them fail**

Run: `cd site && npm test`
Expected: both FAIL (the form navigates away).

- [ ] **Step 3: Append the form handler to `site/src/main.ts`**

```ts
const form = document.getElementById('keep-posted') as HTMLFormElement;
const note = form.querySelector('.form-note')!;
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = (form.elements.namedItem('email') as HTMLInputElement).value.trim();
  const button = form.querySelector('button')!;
  button.disabled = true;
  try {
    const res = await fetch(form.action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const out = await res.json() as { ok: boolean; error?: string };
    if (out.ok) { note.textContent = "Thanks. We'll write when there's news."; form.reset(); }
    else note.textContent = out.error || "That didn't go through. Try again.";
  } catch {
    note.textContent = "That didn't go through. Try again.";
  } finally {
    button.disabled = false;
  }
});
```

- [ ] **Step 4: Run the tests**

Run: `cd site && npm test`
Expected: all pass.

- [ ] **Step 5: Fix the no-JS thanks page's wording**

In `hosted/src/pages.ts` (the success branch of `waitlistThanksPage`), change `We'll email ${escapeHtml(email)} when there's room.` to `We'll email ${escapeHtml(email)} when there's news.` Then:

```bash
cd hosted && npx tsc --noEmit && npm test
```

Expected: no type errors; tests pass. (Deployed with the next hosted deploy, Task 9.)

- [ ] **Step 6: Commit**

```bash
git add site/src/main.ts site/test/site.spec.ts hosted/src/pages.ts
git commit -m "featherframe.app: Keep me posted"
```

---

### Task 7: SEO, social card and analytics hook

**Files:**
- Modify: `site/public/index.html` (`<head>`), `site/test/site.spec.ts`
- Create: `site/public/robots.txt`, `site/public/sitemap.xml`

- [ ] **Step 1: Add a failing test**

```ts
test('social card and search basics', async ({ page, request }) => {
  await page.goto('/');
  await expect(page.locator('meta[property="og:image"]')).toHaveAttribute('content', 'https://featherframe.app/img/og.jpg');
  await expect(page.locator('meta[property="og:title"]')).toHaveAttribute('content', 'Featherframe');
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', 'https://featherframe.app/');
  expect((await request.get('/robots.txt')).ok()).toBe(true);
  expect((await request.get('/sitemap.xml')).ok()).toBe(true);
});
```

- [ ] **Step 2: Run to see it fail**

Run: `cd site && npm test` — Expected: FAIL.

- [ ] **Step 3: Add to `<head>` after the description**

```html
  <link rel="canonical" href="https://featherframe.app/">
  <meta property="og:type" content="website">
  <meta property="og:url" content="https://featherframe.app/">
  <meta property="og:title" content="Featherframe">
  <meta property="og:description" content="A framed e-paper display that shows the species heard near your home, as John James Audubon painted them in 1827.">
  <meta property="og:image" content="https://featherframe.app/img/og.jpg">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
```

`site/public/robots.txt`:

```
User-agent: *
Allow: /
Sitemap: https://featherframe.app/sitemap.xml
```

`site/public/sitemap.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://featherframe.app/</loc></url>
</urlset>
```

- [ ] **Step 4: Run the tests** — `cd site && npm test` — Expected: all pass.

- [ ] **Step 5: Analytics token**

Cloudflare Web Analytics needs a site token, which wrangler cannot create. Try the API with whatever token is at hand:

```bash
security find-generic-password -s CF_API_TOKEN -w 2>/dev/null || security find-generic-password -l featherframe-cf-api-token -w 2>/dev/null
```

If a token prints, get the account id (`cd site && npx wrangler whoami`) and run:

```bash
curl -s -X POST "https://api.cloudflare.com/client/v4/accounts/$ACCOUNT/rum/site_info" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"host":"featherframe.app","auto_install":false}' | python3 -c 'import json,sys;r=json.load(sys.stdin)["result"];print(r["site_token"])'
```

and write the printed token to `site/analytics.json` as `{"token": "<printed token>"}` (gitignored; `build.mjs` injects the beacon). If no token works, this is blocked on Wells: ask him to add featherframe.app under Cloudflare dashboard → Analytics & Logs → Web Analytics → Add a site, and paste the site token from the JS snippet. Continue with Task 8 meanwhile; the build simply omits the beacon until `analytics.json` exists.

Verify when present: `cd site && npm run build && grep -c cloudflareinsights dist/index.html` → `1`.

- [ ] **Step 6: Commit**

```bash
git add site/public site/test/site.spec.ts
git commit -m "featherframe.app: social card, robots, sitemap, analytics hook"
```

---

### Task 8: The Worker, and a preview deploy

**Files:**
- Create: `site/src/worker.ts`, `site/test/worker.test.ts`, `site/wrangler.jsonc`

**Interfaces:**
- `worker.ts`: `export default { fetch(request: Request, env: { ASSETS: { fetch(r: Request): Promise<Response> } }): Promise<Response> | Response }`

- [ ] **Step 1: Write the failing test `site/test/worker.test.ts`**

```ts
import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/worker.ts';

const env = { ASSETS: { fetch: async (r: Request) => new Response(`asset ${new URL(r.url).pathname}`) } };

test('www redirects to the apex, keeping the path and query', async () => {
  const res = await worker.fetch(new Request('https://www.featherframe.app/img/og.jpg?x=1'), env);
  assert.equal(res.status, 301);
  assert.equal(res.headers.get('Location'), 'https://featherframe.app/img/og.jpg?x=1');
});

test('the apex is served from the assets', async () => {
  const res = await worker.fetch(new Request('https://featherframe.app/'), env);
  assert.equal(await res.text(), 'asset /');
});
```

- [ ] **Step 2: Run to see it fail** — `cd site && npm run test:unit` — Expected: FAIL, no `worker.ts`.

- [ ] **Step 3: Write `site/src/worker.ts`**

```ts
// featherframe.app's Worker: www goes to the apex; everything else is dist/.
interface Env { ASSETS: { fetch(request: Request): Promise<Response> } }

export default {
  fetch(request: Request, env: Env): Promise<Response> | Response {
    const url = new URL(request.url);
    if (url.hostname === 'www.featherframe.app') {
      url.hostname = 'featherframe.app';
      return Response.redirect(url.toString(), 301);
    }
    return env.ASSETS.fetch(request);
  },
};
```

- [ ] **Step 4: Run the unit tests** — `cd site && npm run test:unit` — Expected: PASS.

- [ ] **Step 5: Write `site/wrangler.jsonc`**

```jsonc
// featherframe.app, the marketing site. Custom domains are added only at
// launch (see docs/superpowers/plans/2026-09-24-featherframe-site.md, Task 9).
{
  "name": "featherframe-site",
  "main": "src/worker.ts",
  "compatibility_date": "2026-09-01",
  "workers_dev": true,
  "assets": { "directory": "./dist", "binding": "ASSETS", "run_worker_first": true },
  "observability": { "enabled": true }
}
```

- [ ] **Step 6: Deploy the preview**

```bash
cd site && npm test && npm run deploy
```

Expected: wrangler prints `https://featherframe-site.<subdomain>.workers.dev`. Then:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://featherframe-site.<subdomain>.workers.dev/
curl -s -o /dev/null -w "%{http_code}\n" https://featherframe-site.<subdomain>.workers.dev/models/featherframe-13.glb
```

Expected: `200` twice. Open the URL in the browser pane and confirm the frame repaints and the card follows. Note: the form's CORS does not allow the workers.dev origin, so on the preview the JSON submit shows "That didn't go through. Try again." — that is expected until the apex is live.

- [ ] **Step 7: Commit, push, open the PR**

```bash
git add site/src/worker.ts site/test/worker.test.ts site/wrangler.jsonc
git commit -m "featherframe.app: the Worker and a preview deploy"
git push -u origin wells/featherframe-site
gh pr create --title "featherframe.app: the marketing site" --body "Spec: docs/superpowers/specs/2026-09-24-featherframe-site-design.md
Plan: docs/superpowers/plans/2026-09-24-featherframe-site.md

Preview: <workers.dev URL>

The apex and www domains are not attached yet; that is the launch step, on Wells's yes."
```

Send Wells the preview URL and the PR link, and ask for review (FAQ answers included — the privacy answer especially needs his check).

---

### Task 9: Launch (only after Wells says yes in chat)

**Files:**
- Modify: `site/wrangler.jsonc`

- [ ] **Step 1: Add the domains**

In `site/wrangler.jsonc` add:

```jsonc
  "routes": [
    { "pattern": "featherframe.app", "custom_domain": true },
    { "pattern": "www.featherframe.app", "custom_domain": true }
  ],
```

- [ ] **Step 2: Deploy and check**

```bash
cd site && npm run deploy
curl -s -o /dev/null -w "%{http_code}\n" https://featherframe.app/
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" https://www.featherframe.app/
```

Expected: `200`; `301 https://featherframe.app/`.

- [ ] **Step 3: Deploy the hosted Worker** (for Task 6's thanks-page wording; Docker must be running)

```bash
cd hosted && npx wrangler deploy
```

- [ ] **Step 4: Real form round trip**

In the browser pane at `https://featherframe.app/`, sign up with `hi+site-test@wells.ee`; expect "Thanks. We'll write when there's news." Confirm the row in `/admin` (source `site`), then remove it there.

- [ ] **Step 5: Commit, merge**

```bash
git add site/wrangler.jsonc
git commit -m "featherframe.app: live on the apex and www"
git push
gh pr merge --squash --delete-branch
```
