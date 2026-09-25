import { expect, test } from '@playwright/test';

test('social card and search basics', async ({ page, request }) => {
  await page.goto('/');
  await expect(page.locator('meta[property="og:image"]')).toHaveAttribute('content', 'https://featherframe.app/img/og.jpg');
  await expect(page.locator('meta[property="og:title"]')).toHaveAttribute('content', 'Featherframe');
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', 'https://featherframe.app/');
  expect((await request.get('/robots.txt')).ok()).toBe(true);
  expect((await request.get('/sitemap.xml')).ok()).toBe(true);
});

test('the page loads without console errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/');
  await expect(page).toHaveTitle('Featherframe · Wells Workshop');
  await expect(page.locator('.head .word')).toHaveText('Featherframe');
  expect(errors).toEqual([]);
});

test('every section and its key copy is there', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('h1')).toHaveText('Let the outside in.');
  await expect(page.locator('h1')).toHaveCount(1);
  for (const id of ['art', 'how', 'collage', 'sizes', 'faq']) await expect(page.locator(`section#${id}`)).toBeVisible();
  await expect(page.locator('.head nav a')).toHaveText(['The art', 'How it works', 'Sizes', 'Questions', 'Pre-order']);
  expect(await page.locator('.head nav a').evaluateAll((as) => as.map((a) => a.getAttribute('href')))).toEqual(
    ['#art', '#how', '#sizes', '#faq', 'https://shop.wells.ee/products/featherframe/']);
  await expect(page.locator('#art h2')).toHaveText('More than 1,300 species, colored by hand.');
  await expect(page.locator('#how h2')).toHaveText('Meet the birds you only hear.');
  await expect(page.locator('#collage h2')).toHaveText('The whole day, on one sheet.');
  await expect(page.locator('#sizes h2')).toHaveText('Sixteen grays, or six inks.');
  await expect(page.locator('#faq dt')).toHaveCount(4);
  await expect(page.locator('.cat figure')).toHaveCount(12);
  for (const link of await page.getByRole('link', { name: 'Pre-order' }).all()) {
    await expect(link).toHaveAttribute('href', 'https://shop.wells.ee/products/featherframe/');
  }
  await expect(page.locator('.colophon .c').getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', 'https://app.featherframe.app/');
  const body = (await page.locator('body').innerText()).toLowerCase();
  for (const banned of ['plate', 'on the wall', 'on the glass']) expect(body).not.toContain(banned);
});

test('the 3D frame lands exactly on the poster', async ({ page }) => {
  await page.goto('/');
  // The stage holds the poster's whole canvas; the frame's own box is where
  // the frame is drawn in it (1200 × 1400, frame at 111,108 → 1086,1352).
  const [frame, stage] = await page.evaluate(() => ['.frame', '#stage'].map((s) => {
    const r = document.querySelector(s)!.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  }));
  expect(Math.abs(stage.x + stage.w * 111 / 1200 - frame.x)).toBeLessThan(2);
  expect(Math.abs(stage.y + stage.h * 108 / 1400 - frame.y)).toBeLessThan(2);
  expect(Math.abs(stage.w * 975 / 1200 - frame.w)).toBeLessThan(2);
});

test('no horizontal scroll on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(sw).toBeLessThanOrEqual(cw);
});

test('the frame repaints to the next species', async ({ page }) => {
  await page.goto('/?hold=300');
  await expect(page.locator('canvas.ff3d')).toHaveCount(1, { timeout: 20_000 });
  await expect(page.locator('#stage')).toHaveAttribute('data-shown', '1', { timeout: 30_000 });
});

test('the poster stays until the 3D frame has actually drawn', async ({ page }) => {
  // A slow page: every rAF callback waits ~500 ms, and the page reports itself
  // hidden until the test says otherwise, so the frame loop runs without drawing.
  await page.addInitScript(() => {
    const w = window as unknown as { __draws: number; __drawsAtLive: number | null; __posterAtLive: string | null };
    w.__draws = 0;
    w.__drawsAtLive = null;
    const raf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = (cb) => raf(() => window.setTimeout(() => raf(cb), 500));
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => !(window as unknown as { __show?: boolean }).__show });
    // Count draws to the canvas itself (no framebuffer bound), not the
    // environment map or the e-paper screen's offscreen target.
    const proto = WebGL2RenderingContext.prototype;
    const bound = new WeakMap<WebGL2RenderingContext, unknown>();
    const bind = proto.bindFramebuffer;
    proto.bindFramebuffer = function (this: WebGL2RenderingContext, target: number, fb: WebGLFramebuffer | null) {
      bound.set(this, fb);
      return bind.call(this, target, fb);
    };
    for (const name of ['drawElements', 'drawArrays'] as const) {
      const orig = proto[name] as (...a: unknown[]) => void;
      (proto as unknown as Record<string, unknown>)[name] = function (this: WebGL2RenderingContext, ...args: unknown[]) {
        if (!bound.get(this)) w.__draws++;
        return orig.apply(this, args);
      };
    }
    addEventListener('DOMContentLoaded', () => {
      const stage = document.getElementById('stage')!;
      new MutationObserver(() => {
        if (stage.classList.contains('live') && w.__drawsAtLive === null) w.__drawsAtLive = w.__draws;
      }).observe(stage, { attributes: true, attributeFilter: ['class'] });
    });
  });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveCount(1, { timeout: 20_000 });
  // Canvas in place and rAFs running, but nothing drawn: the poster holds.
  await page.waitForTimeout(2500);
  expect(await page.evaluate(() => (window as unknown as { __draws: number }).__draws)).toBe(0);
  await expect(page.locator('#stage')).not.toHaveClass(/\blive\b/);
  expect(await page.locator('#stage .poster').evaluate((e) => getComputedStyle(e).opacity)).toBe('1');
  await page.evaluate(() => { (window as unknown as { __show: boolean }).__show = true; });
  await expect(page.locator('#stage')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  expect(await page.evaluate(() => (window as unknown as { __drawsAtLive: number }).__drawsAtLive)).toBeGreaterThan(0);
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
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(page.locator('#stage .poster')).toBeVisible();
  expect(await page.locator('#stage').getAttribute('data-shown')).toBeNull();
});

test('with reduced motion nothing cycles', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/?hold=300');
  await page.waitForTimeout(3000);
  await expect(page.locator('canvas')).toHaveCount(0);
  expect(await page.locator('#stage').getAttribute('data-shown')).toBeNull();
});

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

test('?size=10 shows its poster once species.json arrives late', async ({ page }) => {
  await page.addInitScript(() => {
    const orig = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...rest: unknown[]) {
      return kind.startsWith('webgl') ? null : (orig as Function).call(this, kind, ...rest);
    } as typeof orig;
  });
  await page.route('**/species.json', async (route) => {
    await new Promise((r) => setTimeout(r, 1000));
    await route.continue();
  });
  await page.goto('/?size=10');
  await expect(page.locator('#stage .poster')).toHaveAttribute('src', 'img/poster-13.webp');
  await expect(page.locator('#stage .poster')).toHaveAttribute('src', 'img/poster-10.webp', { timeout: 5000 });
});

test('a frame whose model never arrives leaves the poster showing', async ({ page }) => {
  await page.route('**/models/featherframe-13.*.glb', (route) => route.abort());
  await page.goto('/?hold=300');
  await page.waitForTimeout(3000);
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(page.locator('#stage')).not.toHaveClass(/\blive\b/);
  expect(await page.locator('#stage .poster').evaluate((e) => getComputedStyle(e).opacity)).toBe('1');
  expect(await page.locator('#stage').getAttribute('data-shown')).toBeNull();
});

test('the song plays, pauses and resets when it ends', async ({ page }) => {
  await page.goto('/');
  const button = page.locator('button.play');
  await expect(button).toHaveText('Play the song');
  const song = page.locator('#song');
  await expect(song).toHaveAttribute('preload', 'none');
  await expect(button).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('.playhead')).toBeHidden();
  await button.click();
  await expect(button).toHaveText('Pause');
  await expect(button).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.playhead')).toBeVisible();
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => a.currentTime)).toBeGreaterThan(0.2);
  await button.click();
  await expect(button).toHaveText('Play the song');
  await expect(button).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('.playhead')).toBeHidden();
  // Played out (fast: the test server cannot seek), it stands ready to play from the start.
  await song.evaluate((a: HTMLAudioElement) => { a.playbackRate = 8; });
  await button.click();
  await expect(button).toHaveAttribute('aria-pressed', 'true');
  await expect(button).toHaveText('Play the song', { timeout: 10_000 });
  await expect(button).toHaveAttribute('aria-pressed', 'false');
  expect(await song.evaluate((a: HTMLAudioElement) => a.currentTime)).toBe(0);
});

test('on a phone the frame stays in the cover, with no page-wide canvas', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?hold=300');
  await expect(page.locator('#stage canvas')).toHaveCount(1, { timeout: 20_000 });
  await expect(page.locator('#stage')).toHaveAttribute('data-shown', '1', { timeout: 30_000 });
  await expect(page.locator('canvas.ff3d')).toHaveCount(0);
  await expect(page.locator('html')).not.toHaveClass(/\bchoreo\b/);
});
