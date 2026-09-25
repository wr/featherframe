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
    await page.addStyleTag({ content: 'html,body{background:transparent!important}.nav,.label,.promise,main>section:not(.hero),footer{display:none!important}.hero{display:block!important;padding:0!important}.stage{width:1200px;height:1400px;aspect-ratio:auto}.poster{display:none}' });
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
