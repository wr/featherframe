// Renders the hero's two posters from the viewer itself, so the still and the
// 3D frame are the same picture: dist/ served, each size at ?poster, the stage
// captured with a transparent background, written to public/img as WebP. At
// this width the page runs its journey (choreo.ts), so the frame is drawn on the
// page's fixed canvas, over the stage, exactly as the live hero draws it.
import { chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';

const here = new URL('..', import.meta.url).pathname;
const server = spawn('python3', ['-m', 'http.server', '4322', '--bind', '127.0.0.1', '-d', `${here}dist`], { stdio: 'ignore' });
await new Promise((r) => setTimeout(r, 800));
let browser;
try {
  browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const page = await browser.newPage({ viewport: { width: 1200, height: 1400 }, deviceScaleFactor: 1 });
  for (const size of ['13', '10']) {
    await page.goto(`http://127.0.0.1:4322/?poster&size=${size}&hold=600000`);
    await page.addStyleTag({ content: 'html,body{background:transparent!important}.head,.tag,.cover>:not(.frame),main>section:not(.cover),.stop-centre,footer{display:none!important}.frame::after,.frame::before,.cover::before{display:none!important}.stage{position:fixed;left:0;top:0;width:1200px;height:1400px;margin:0;aspect-ratio:auto;filter:none}.poster{display:none}' });
    await page.locator('canvas.ff3d.live').waitFor({ timeout: 30_000 });
    await page.waitForTimeout(1500);
    const png = `${here}dist/poster-${size}.png`;
    await page.locator('#stage').screenshot({ path: png, omitBackground: true });
    execFileSync('cwebp', ['-quiet', '-q', '86', '-alpha_q', '90', png, '-o', `${here}public/img/poster-${size}.webp`]);
    console.log(`public/img/poster-${size}.webp`);
  }
} finally {
  await browser?.close();
  server.kill();
}
