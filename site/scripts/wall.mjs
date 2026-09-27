// Renders the page's stills of the frame from the live frame itself (the page
// at ?wall=), so a still and the 3D frame are the same picture and the hand-off
// between them does not jump: dist/ served; for each size, the frame dead-on
// showing each of the gallery wall's twelve screens, and (13-inch) the frame on
// III's table showing the cardinal; each captured with a transparent
// background at the pose's own aspect, written to public/img/wall as WebP.
import { chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { copyFileSync, mkdirSync, readFileSync } from 'node:fs';

const here = new URL('..', import.meta.url).pathname;
let HEIGHT = 760; // px: twice the wall's frames at 1440 wide
const data = JSON.parse(readFileSync(`${here}public/species.json`, 'utf8'));
const slug = (f) => f.match(/wall-\d+-(.+)\.jpg$/)[1];
mkdirSync(`${here}public/img/wall`, { recursive: true });

const server = spawn('python3', ['-m', 'http.server', '4323', '--bind', '127.0.0.1', '-d', `${here}dist`], { stdio: 'ignore' });
await new Promise((r) => setTimeout(r, 800));
let browser;
try {
  browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 600, height: HEIGHT }, deviceScaleFactor: 1 });
  // (swiftshader now and then never finishes a page: try again)
  const render = async (...a) => {
    for (let n = 1; ; n++) {
      try { return await render1(...a); } catch (e) { if (n >= 3) throw e; console.warn(`retrying ${a[2]}`); }
    }
  };
  const render1 = async (size, which, name) => {
    const url = `http://127.0.0.1:4323/?wall=${which}&size=${size}`;
    const ready = () => page.waitForFunction(() => document.documentElement.dataset.wall === 'ready', null, { timeout: 60_000 });
    // once to learn the pose's aspect, once at it
    await page.setViewportSize({ width: 600, height: HEIGHT });
    await page.goto(url);
    await ready();
    const aspect = Number(await page.evaluate(() => document.documentElement.dataset.aspect));
    await page.setViewportSize({ width: Math.round(HEIGHT * aspect), height: HEIGHT });
    await page.goto(url);
    await ready();
    const png = `${here}dist/${name.replace(/\W+/g, '-')}.png`;
    await page.screenshot({ path: png, omitBackground: true });
    execFileSync('cwebp', ['-quiet', '-q', '86', '-alpha_q', '90', png, '-o', `${here}public/img/wall/${name}.webp`]);
    console.log(`public/img/wall/${name}.webp  ${Math.round(HEIGHT * aspect)} × ${HEIGHT}`);
  };
  // `node scripts/wall.mjs seasons <dir> [13|10]` renders IV's four frames from <dir>/season-<size>-<season>.jpg:
  // each season's collage sheet as a screen texture (13: 1543 × 2072 in colour; 10: 1179 × 1572 in the
  // panel's 16 grays; the sheet fitted by width on white, as scripts/screens.sh composes a screen), dead-on,
  // into public/img/seasons/<size>-<season>.webp
  if (process.argv[2] === 'seasons') {
    const dir = process.argv[3];
    const size = process.argv[4] ?? '13';
    HEIGHT = 1100;
    mkdirSync(`${here}dist/_seasons`, { recursive: true });
    mkdirSync(`${here}public/img/seasons`, { recursive: true });
    for (const season of ['spring', 'summer', 'fall', 'winter']) {
      copyFileSync(`${dir}/season-${size}-${season}.jpg`, `${here}dist/_seasons/${size}-${season}.jpg`);
      await render(size, `screen&src=_seasons/${size}-${season}.jpg`, `../seasons/${size}-${season}`);
    }
  } else if (process.argv[2] === 'large') {
    // `node scripts/wall.mjs large [slug,…]`: the wall's frames at 1600 px tall, for the
    // lightbox (public/img/wall/large/<size>-<slug>.webp)
    HEIGHT = 1600;
    const only = process.argv[3]?.split(',');
    mkdirSync(`${here}public/img/wall/large`, { recursive: true });
    for (const size of ['13', '10']) {
      for (const [i, f] of data.sizes[size].wall.entries()) if (!only || only.includes(slug(f))) await render(size, i, `large/${size}-${slug(f)}`);
    }
  } else if (process.argv[2] !== 'table') {
    // `node scripts/wall.mjs table` renders the table's still alone; `node scripts/wall.mjs only <slug,…>`
    // those of the wall's frames alone
    const only = process.argv[2] === 'only' ? process.argv[3].split(',') : null;
    for (const size of ['13', '10']) {
      for (const [i, f] of data.sizes[size].wall.entries()) if (!only || only.includes(slug(f))) await render(size, i, `${size}-${slug(f)}`);
    }
  }
  if (process.argv[2] !== 'seasons' && process.argv[2] !== 'large' && process.argv[2] !== 'only') {
    await render('13', 'table', 'table-13-cardinal');
    await render('10', 'table', 'table-10-cardinal');
    // Technical details' 13-inch shows the hero's cardinal, dead-on
    mkdirSync(`${here}dist/_screens`, { recursive: true });
    copyFileSync(`${here}public/models/screens/13-cardinal.jpg`, `${here}dist/_screens/13-cardinal.jpg`);
    await render('13', 'screen&src=_screens/13-cardinal.jpg', '13-cardinal');
  }
} finally {
  await browser?.close();
  server.kill();
}
