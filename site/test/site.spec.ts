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
  for (const id of ['art', 'how', 'collage', 'specs', 'faq']) await expect(page.locator(`section#${id}`)).toBeVisible();
  await expect(page.locator('section#sizes')).toHaveCount(0);
  await expect(page.locator('.head nav a')).toHaveText(['The art', 'How it works', 'Details', 'Questions', 'Pre-order']);
  expect(await page.locator('.head nav a').evaluateAll((as) => as.map((a) => a.getAttribute('href')))).toEqual(
    ['#art', '#how', '#specs', '#faq', 'https://shop.wells.ee/products/featherframe/']);
  await expect(page.locator('#art h2')).toHaveText('More than 1,300 species, colored by hand.');
  await expect(page.locator('#how h2')).toHaveText('Meet the birds you only hear.');
  await expect(page.locator('#collage h2')).toHaveText('The whole day, on one sheet.');
  await expect(page.locator('#specs .folio')).toHaveText('V. Technical details');
  await expect(page.locator('#specs .spec dt')).toHaveText(['Display', 'Frame', 'Size', 'Power', 'Connectivity', 'Detections', 'Hosting']);
  await expect(page.locator('#specs .spec dd').nth(2)).toHaveText('10-inch: 232 × 295 × 28 mm13-inch: 295 × 371 × 28 mm');
  await expect(page.locator('#specs .exploded img')).toHaveAttribute('src', 'img/exploded.webp');
  await expect(page.locator('#specs .ho')).toHaveCount(2);
  await expect(page.locator('#collage .season:not(.coda) figcaption')).toHaveText(
    ['Spring7 April 2026', 'Summer1 June 2026', 'Fall23 September 2026', 'Winter16 February 2026']);
  await expect(page.locator('#collage .season:not(.coda) img').first()).toHaveAttribute('alt', 'A collage painted by AI from the species heard on 7 April 2026');
  await expect(page.locator('#faq dt')).toHaveCount(4);
  await expect(page.locator('.cat figure')).toHaveCount(12);
  await expect(page.locator('.tone button')).toHaveText(['Color', 'B&W']);
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

for (const [w, h] of [[390, 844], [1024, 768]]) {
  test(`no horizontal scroll at ${w}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await page.goto('/');
    await page.evaluate(() => document.fonts.ready);
    for (const y of [0, 0.5, 1]) {
      await page.evaluate((f) => scrollTo(0, f * document.documentElement.scrollHeight), y);
      const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
      expect(sw).toBeLessThanOrEqual(cw);
    }
  });
}

test('the seasons slide sideways as the page scrolls down, on a desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await expect(page.locator('html')).toHaveClass(/\bseasons-live\b/);
  const box = await page.locator('.seasons').evaluate((e) => ({ y: e.getBoundingClientRect().top + scrollY, h: e.getBoundingClientRect().height }));
  expect(box.h).toBeGreaterThan(900 * 3);
  const x = async (f: number) => {
    await page.evaluate((y) => scrollTo(0, y), box.y + f * box.h);
    // the row follows on the next frame
    const want = await page.evaluate(() => {
      const b = document.querySelector('.seasons')!.getBoundingClientRect();
      const nav = document.querySelector('.head')!.getBoundingClientRect().height;
      return Math.max(0, Math.min(1, (nav - b.top) / (b.height - (innerHeight - nav))));
    });
    await expect.poll(() => page.locator('.row').evaluate((e) => Number(e.dataset.progress))).toBeCloseTo(want, 2);
    return page.locator('.season').nth(1).evaluate((e) => e.getBoundingClientRect().left);
  };
  const stageTop = async () => page.locator('.stage-row').evaluate((e) => e.getBoundingClientRect().top);
  const a = await x(0.1);
  const t1 = await stageTop();
  const b = await x(0.4);
  const t2 = await stageTop();
  const c = await x(0.7);
  expect(a - b).toBeGreaterThan(200);
  expect(b - c).toBeGreaterThan(200);
  expect(Math.abs(t1 - t2)).toBeLessThan(1); // pinned
  // by the end the row has come round to spring again
  await x(1);
  const coda = await page.locator('.season.coda').evaluate((e) => { const r = e.getBoundingClientRect(); return r.left + r.width / 2; });
  expect(Math.abs(coda - 720)).toBeLessThan(40);
});

test('on a phone the seasons stack', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('html')).not.toHaveClass(/\bseasons-live\b/);
  const tops = await page.locator('.season:not(.coda)').evaluateAll((es) => es.map((e) => e.getBoundingClientRect().top));
  for (let i = 1; i < tops.length; i++) expect(tops[i]).toBeGreaterThan(tops[i - 1] + 300);
  await expect(page.locator('.season.coda')).toBeHidden();
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

test('the song plays itself muted in view, and Unmute plays it again with sound', async ({ page }) => {
  await page.goto('/');
  const song = page.locator('#song');
  const unmute = page.getByRole('button', { name: 'Play the song with sound' });
  await expect(unmute).toHaveText('Unmute');
  await expect(song).toHaveAttribute('preload', 'none');
  await page.locator('.spectro').scrollIntoViewIfNeeded();
  // (the swiftshader page is slow to get round to it)
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => !a.paused && a.muted && a.currentTime > 0.3), { timeout: 25_000 }).toBe(true);
  await expect(page.locator('.playhead')).toBeVisible();
  await unmute.click();
  await expect(unmute).toBeHidden();
  const mute = page.getByRole('button', { name: 'Mute' });
  await expect(mute).toBeVisible();
  // from the start, with sound
  expect(await song.evaluate((a: HTMLAudioElement) => [a.muted, a.currentTime < 0.6])).toEqual([false, true]);
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => !a.paused && !a.muted)).toBe(true);
  await mute.click();
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => a.muted)).toBe(true);
  await expect(unmute).toBeVisible();
});

test('with reduced motion the song waits for its button', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.locator('.spectro').scrollIntoViewIfNeeded();
  await page.waitForTimeout(1500);
  const song = page.locator('#song');
  expect(await song.evaluate((a: HTMLAudioElement) => a.paused)).toBe(true);
  const button = page.getByRole('button', { name: 'Play the song with sound' });
  await expect(button).toHaveText('Play the song');
  await button.click();
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => !a.paused && !a.muted)).toBe(true);
});

test('each new detection is announced and the frame on the table repaints to it', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  // the recordings, sped up: the test server cannot seek
  await page.addInitScript(() => {
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function (this: HTMLMediaElement) { this.playbackRate = 3; return play.call(this); };
  });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  await page.evaluate(() => scrollTo(0, document.querySelector('.spectro')!.getBoundingClientRect().top + scrollY - 120));
  const table = page.locator('#table-slot');
  const toast = page.locator('.toast');
  await expect(toast).toHaveClass(/\bon\b/);
  await expect(toast.locator('.nm')).toHaveText('Northern Cardinal');
  await expect(table).toHaveAttribute('data-shown', 'cardinal', { timeout: 15_000 });
  await expect(toast.locator('.nm')).toHaveText('Blue Jay', { timeout: 15_000 });
  await expect(table).toHaveAttribute('data-shown', 'blue-jay', { timeout: 15_000 });
  await expect(page.locator('.spectro img')).toHaveAttribute('src', 'img/spectrogram-blue-jay.webp');
  await expect(toast.locator('.nm')).toHaveText('American Goldfinch', { timeout: 15_000 });
  await expect(table).toHaveAttribute('data-shown', 'goldfinch', { timeout: 15_000 });
});

test('the running head and the wall\'s folio stay in view while their sections scroll', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const top = (sel: string) => page.locator(sel).evaluate((e) => e.getBoundingClientRect().top);
  await page.evaluate(() => scrollTo(0, 3000));
  expect(await top('.head')).toBe(0);
  const wall = await page.locator('.wall').evaluate((e) => ({ y: e.getBoundingClientRect().top + scrollY, h: e.getBoundingClientRect().height }));
  const nav = await page.locator('.head').evaluate((e) => e.getBoundingClientRect().height);
  for (const f of [0.3, 0.6]) {
    await page.evaluate((y) => scrollTo(0, y), wall.y + wall.h * f);
    expect(Math.abs((await top('.wall .folio')) - nav)).toBeLessThan(1);
    expect(await top('.head')).toBe(0);
  }
  // past the wall, the folio goes with it
  await page.evaluate((y) => scrollTo(0, y), wall.y + wall.h + 400);
  expect(await top('.wall .folio')).toBeLessThan(0);
  expect(await top('.head')).toBe(0);
});

test('the reservation comes before the questions', async ({ page }) => {
  await page.goto('/');
  const order = await page.evaluate(() => [...document.querySelectorAll('main > section')].map((s) => s.id || s.className));
  expect(order.indexOf('close')).toBe(order.indexOf('faq') - 1);
  expect(order[order.length - 1]).toBe('faq');
});

test('on a phone the frame stays in the cover, with no page-wide canvas', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?hold=300');
  await expect(page.locator('#stage canvas')).toHaveCount(1, { timeout: 20_000 });
  await expect(page.locator('#stage')).toHaveAttribute('data-shown', '1', { timeout: 30_000 });
  await expect(page.locator('canvas.ff3d')).toHaveCount(0);
  await expect(page.locator('html')).not.toHaveClass(/\bchoreo\b/);
});

test('the wall switches between Color and B&W, and remembers', async ({ page }) => {
  await page.goto('/');
  const stills = page.locator('.wall img[data-still]');
  await expect(stills).toHaveCount(12);
  const srcs = () => stills.evaluateAll((imgs) => imgs.map((i) => i.getAttribute('src')));
  for (const src of await srcs()) expect(src).toMatch(/^img\/wall\/13-/);
  await page.getByRole('button', { name: 'B&W' }).click();
  await expect(page.getByRole('button', { name: 'B&W' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: 'Color' })).toHaveAttribute('aria-pressed', 'false');
  for (const src of await srcs()) expect(src).toMatch(/^img\/wall\/10-/);
  await page.reload();
  await expect(page.getByRole('button', { name: 'B&W' })).toHaveAttribute('aria-pressed', 'true');
  for (const src of await srcs()) expect(src).toMatch(/^img\/wall\/10-/);
  await page.getByRole('button', { name: 'Color' }).click();
  for (const src of await srcs()) expect(src).toMatch(/^img\/wall\/13-/);
});

test("B&W hangs the wall's frames smaller, at the 10-inch's true size", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const first = page.locator('.wall .cat figure:first-child .im');
  const caption = page.locator('.wall .cat figure:first-child figcaption');
  const gap = async () => (await caption.boundingBox())!.y - ((await first.boundingBox())!.y + (await first.boundingBox())!.height);
  const colour = (await first.boundingBox())!;
  const colourGap = await gap();
  await page.getByRole('button', { name: 'B&W' }).click();
  await expect.poll(async () => (await first.boundingBox())!.height / colour.height).toBeCloseTo(295 / 371, 2);
  // hung from the same line: the frames still meet their captions as before
  expect(Math.abs((await gap()) - colourGap)).toBeLessThan(1);
  await page.getByRole('button', { name: 'Color' }).click();
  await expect.poll(async () => (await first.boundingBox())!.height).toBeCloseTo(colour.height, 0);
});

test('the art spread holds the frame while its text scrolls past', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  const slot = page.locator('#art-slot');
  const text = page.locator('#art .text h2');
  await slot.scrollIntoViewIfNeeded();
  await page.evaluate(() => scrollBy(0, 200));
  const a = [(await slot.boundingBox())!.y, (await text.boundingBox())!.y];
  await page.evaluate(() => scrollBy(0, 250));
  const b = [(await slot.boundingBox())!.y, (await text.boundingBox())!.y];
  expect(Math.abs(b[0] - a[0])).toBeLessThan(1);
  expect(a[1] - b[1]).toBeGreaterThan(240);
});

test('the frame lands in the wall\'s first place and hands over to its print', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  const first = page.locator('.wall .cat figure:first-child img');
  // Above the wall the first place is empty: the frame is on its way.
  await expect(first).toHaveCSS('visibility', 'hidden');
  await page.evaluate(() => scrollTo(0, document.querySelector('.wall .cat')!.getBoundingClientRect().top + scrollY - 100));
  await expect(page.locator('.wall')).toHaveClass(/\blanded\b/);
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\bempty\b/);
  await expect(first).toHaveCSS('visibility', 'visible');
  // Back up, the frame takes over again.
  await page.evaluate(() => scrollTo(0, 0));
  await expect(page.locator('.wall')).not.toHaveClass(/\blanded\b/);
  await expect(page.locator('canvas.ff3d')).not.toHaveClass(/\bempty\b/);
});

test('past the wall its last frame tears off and lands on the table', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  const last = page.locator('.wall .cat figure:last-child img');
  await page.evaluate(() => scrollTo(0, document.getElementById('table-slot')!.getBoundingClientRect().top + scrollY - 200));
  await expect(page.locator('.wall')).toHaveClass(/\btorn\b/);
  await expect(last).toHaveCSS('visibility', 'hidden');
  await expect(page.locator('canvas.ff3d')).not.toHaveClass(/\bempty\b/);
  await expect(page.locator('#table-slot .still')).toHaveCSS('visibility', 'hidden');
});

for (const [name, setup] of [
  ['on a phone', async (page: import('@playwright/test').Page) => { await page.setViewportSize({ width: 390, height: 844 }); }],
  ['with reduced motion', async (page: import('@playwright/test').Page) => { await page.emulateMedia({ reducedMotion: 'reduce' }); }],
] as const) {
  test(`${name} there is no journey: the stills show`, async ({ page }) => {
    await setup(page);
    await page.goto('/?hold=600000');
    await page.waitForTimeout(3000);
    await expect(page.locator('canvas.ff3d')).toHaveCount(0);
    await expect(page.locator('html')).not.toHaveClass(/\bchoreo\b/);
    for (const sel of ['#art-slot .still', '#table-slot .still', '.wall .cat figure:first-child img', '.wall .cat figure:last-child img']) {
      await page.locator(sel).scrollIntoViewIfNeeded();
      await expect(page.locator(sel)).toBeVisible();
      await expect.poll(() => page.locator(sel).evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
    }
    const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    expect(sw).toBeLessThanOrEqual(cw);
  });
}
