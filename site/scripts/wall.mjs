// Renders the page's stills of the frame from the live frame itself (the page
// at ?wall=), so a still and the 3D frame are the same picture and the hand-off
// between them does not jump: dist/ served; for each size, the frame dead-on
// showing each of the gallery wall's twelve screens, and (13-inch) the frame on
// III's table showing the cardinal; each captured with a transparent
// background at the pose's own aspect, written to public/img/wall as WebP.
import { chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';

const here = new URL('..', import.meta.url).pathname;
const HEIGHT = 760; // px: twice the wall's frames at 1440 wide
const data = JSON.parse(readFileSync(`${here}public/species.json`, 'utf8'));
const slug = (f) => f.match(/wall-\d+-(.+)\.jpg$/)[1];
mkdirSync(`${here}public/img/wall`, { recursive: true });

const server = spawn('python3', ['-m', 'http.server', '4323', '--bind', '127.0.0.1', '-d', `${here}dist`], { stdio: 'ignore' });
await new Promise((r) => setTimeout(r, 800));
let browser;
try {
  browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 600, height: HEIGHT }, deviceScaleFactor: 1 });
  const render = async (size, which, name) => {
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
    const png = `${here}dist/${name}.png`;
    await page.screenshot({ path: png, omitBackground: true });
    execFileSync('cwebp', ['-quiet', '-q', '86', '-alpha_q', '90', png, '-o', `${here}public/img/wall/${name}.webp`]);
    console.log(`public/img/wall/${name}.webp  ${Math.round(HEIGHT * aspect)} × ${HEIGHT}`);
  };
  // `node scripts/wall.mjs table` renders the table's still alone
  if (process.argv[2] !== 'table') {
    for (const size of ['13', '10']) {
      for (const [i, f] of data.sizes[size].wall.entries()) await render(size, i, `${size}-${slug(f)}`);
    }
  }
  await render('13', 'table', 'table-13-cardinal');
} finally {
  await browser?.close();
  server.kill();
}
