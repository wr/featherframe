// Renders the share image (public/img/og.jpg, 1200 × 630) from the page's own
// hero: dist/ served, the cover in a 1440 × 900 window drawn at 2×, its frame
// (the colour one) settled on its first picture and the Featherframe lockup set
// beside it in place of the headline, then cut to 1.9:1 and scaled to 1200 × 630.
import { chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';

const here = new URL('..', import.meta.url).pathname;
const server = spawn('python3', ['-m', 'http.server', '4325', '--bind', '127.0.0.1', '-d', `${here}dist`], { stdio: 'ignore' });
await new Promise((r) => setTimeout(r, 800));
let browser;
try {
  browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  await page.goto('http://127.0.0.1:4325/?hold=600000&size=13');
  // the frame and the Featherframe lockup beside it: no headline (the preview's title says it), no running head,
  // no switch, sentence or button
  await page.addStyleTag({ content: '.head,.tone.pill,.cover .speed,.cover h1{visibility:hidden}.cover .copy p,.cover .cta{display:none!important}'
    + '.og-mark{position:fixed;left:96px;top:452px;transform:translateY(-50%);width:560px;color:#121212;z-index:30}.og-mark svg{display:block;width:100%;height:auto}' });
  await page.evaluate(() => {
    const mark = document.createElement('div');
    mark.className = 'og-mark';
    mark.append(document.querySelector('.head .lockup').cloneNode(true));
    document.body.append(mark);
  });
  await page.locator('canvas.ff3d.live').waitFor({ timeout: 30_000 });
  await page.waitForTimeout(2000);
  const png = `${here}dist/og.png`;
  // the hero as a 1440 × 900 window draws it, cut to the share image's 1.9:1 around the lockup and the frame
  await page.screenshot({ path: png, clip: { x: 0, y: 92, width: 1440, height: 756 } });
  execFileSync('sips', ['-z', '630', '1200', png], { stdio: 'ignore' });
  execFileSync('sips', ['-s', 'format', 'jpeg', '-s', 'formatOptions', '88', png, '--out', `${here}public/img/og.jpg`], { stdio: 'ignore' });
  console.log('public/img/og.jpg');
} finally {
  await browser?.close();
  server.kill();
}
