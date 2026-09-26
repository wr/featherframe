// Renders the share image (public/img/og.jpg, 1200 × 630) from the page's own
// hero: dist/ served, the cover in a 1440 × 900 window drawn at 2× with its headline
// and frame alone (no running head, sentence or button), the frame settled on
// its first picture, then cut to 1.9:1 and scaled to 1200 × 630.
import { chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';

const here = new URL('..', import.meta.url).pathname;
const server = spawn('python3', ['-m', 'http.server', '4325', '--bind', '127.0.0.1', '-d', `${here}dist`], { stdio: 'ignore' });
await new Promise((r) => setTimeout(r, 800));
let browser;
try {
  browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  await page.goto('http://127.0.0.1:4325/?hold=600000');
  // the headline and the frame: no running head, and the sentence and button left to the page
  await page.addStyleTag({ content: '.head{visibility:hidden}.cover .copy p,.cover .cta{display:none!important}' });
  await page.locator('canvas.ff3d.live').waitFor({ timeout: 30_000 });
  await page.waitForTimeout(2000);
  const png = `${here}dist/og.png`;
  // the hero as a 1440 × 900 window draws it, cut to the share image's 1.9:1 around the headline and the frame
  await page.screenshot({ path: png, clip: { x: 0, y: 92, width: 1440, height: 756 } });
  execFileSync('sips', ['-z', '630', '1200', png], { stdio: 'ignore' });
  execFileSync('sips', ['-s', 'format', 'jpeg', '-s', 'formatOptions', '88', png, '--out', `${here}public/img/og.jpg`], { stdio: 'ignore' });
  console.log('public/img/og.jpg');
} finally {
  await browser?.close();
  server.kill();
}
