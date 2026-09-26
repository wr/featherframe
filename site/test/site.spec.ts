import { expect, test } from '@playwright/test';

test('social card and search basics', async ({ page, request }) => {
  await page.goto('/');
  await expect(page.locator('meta[property="og:image"]')).toHaveAttribute('content', 'https://featherframe.app/img/og.jpg');
  await expect(page.locator('meta[property="og:title"]')).toHaveAttribute('content', 'Featherframe — the birds you hear, illustrated');
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', 'https://featherframe.app/');
  expect((await request.get('/robots.txt')).ok()).toBe(true);
  expect((await request.get('/sitemap.xml')).ok()).toBe(true);
});

test('the page loads without console errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('/');
  await expect(page).toHaveTitle('Featherframe — the birds you hear, illustrated · Wells Workshop');
  await expect(page.locator('.head .brand .by')).toHaveText('by Wells Workshop');
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
  await expect(page.locator('#art h2')).toHaveText('More than 1,300 species, each painted by hand.');
  await expect(page.locator('#art h2 i')).toHaveText('each painted by hand.');
  await expect(page.locator('#how h2')).toHaveText('Meet the birds you only hear.');
  await expect(page.locator('#collage h2')).toHaveText('Each night, a portrait of the day.');
  // "Each night," roman, "a portrait of the day." italic
  await expect(page.locator('#collage h2 i')).toHaveText('a portrait of the day.');
  await expect(page.locator('#collage .lede')).toHaveText('The frame shows every species heard that day on one sheet, numbered and keyed like a page in an old natural history book.');
  await expect(page.locator('#specs .folio')).toHaveText('Technical details');
  await expect(page.locator('#specs .spec dt')).toHaveText(['Display', 'Frame', 'Size', 'Power', 'Connectivity', 'Detections', 'Software']);
  // inches first, millimetres after
  await expect(page.locator('#specs .spec dd').nth(2)).toHaveText('10-inch: 9.1 × 11.6 × 1.1 in (232 × 295 × 28 mm)13-inch: 11.6 × 14.6 × 1.1 in (295 × 371 × 28 mm)');
  await expect(page.locator('#specs .spec dd').nth(5)).toHaveText('A BirdWeather station near you, or your own BirdNET-Go');
  await expect(page.locator('#specs .spec dd').nth(6)).toHaveText('Open source. See it on GitHub');
  await expect(page.locator('#specs .spec dd').nth(6).getByRole('link', { name: 'See it on GitHub' })).toHaveAttribute('href', 'https://github.com/wr/featherframe');
  await expect(page.locator('#specs .spec')).not.toContainText('Service');
  // the prices: the hero, each size, the close
  await expect(page.locator('.cover .cta .it')).toHaveText('From $349. Ships in time for the holidays.');
  await expect(page.locator('#specs .ho figcaption')).toHaveText(['10-inch$349 · Depth 1.1 in (28 mm)', '13-inch$479 · Depth 1.1 in (28 mm)']);
  await expect(page.locator('#specs .ho .diag span')).toHaveText(['10.3-inch display', '13.3-inch display']);
  await expect(page.locator('#specs .dim')).toHaveCount(0);
  await expect(page.locator('.close h2')).toHaveText('Pre-order yours.');
  await expect(page.locator('.close h2 i')).toHaveText('yours.');
  await expect(page.locator('.close p')).toHaveText('From $349. Ships in time for the holidays, with nothing to pay after.');
  await expect(page.locator('body')).not.toContainText('Reserve');
  await expect(page.locator('body')).not.toContainText('November');
  await expect(page.locator('.cover .copy p')).toHaveText('Your neighborhood birds, shown as they’re heard, in illustrations from the finest natural history books of the 1800s. A framed e-paper display with no subscription.');
  for (const sel of ['meta[name="description"]', 'meta[property="og:description"]'])
    await expect(page.locator(sel)).toHaveAttribute('content', 'Your neighborhood birds, shown as they’re heard, in illustrations from the finest natural history books of the 1800s. A framed e-paper display with no subscription.');
  // no exploded drawing anywhere: the reservation is its headline, line and button
  await expect(page.locator('.exploded, img[src*="exploded"]')).toHaveCount(0);
  await expect(page.locator('.close > *')).toHaveCount(3);
  await expect(page.locator('#how .logos .sc')).toHaveText('Works with');
  await expect(page.locator('#how')).not.toContainText('Detections by');
  await expect(page.locator('body')).not.toContainText('heard at 07:02');
  await expect(page.locator('.tcap')).toHaveCount(0);
  // a mark for each season on the timeline
  await expect(page.locator('#collage .season figcaption svg.ico')).toHaveCount(4);
  await expect(page.locator('#specs .ho')).toHaveCount(2);
  await expect(page.locator('#collage .season figcaption')).toHaveText(
    ['Spring7 April 2026', 'Summer1 June 2026', 'Fall23 September 2026', 'Winter16 February 2026']);
  await expect(page.locator('#collage .season img').first()).toHaveAttribute('alt', 'A collage painted by AI from the species heard on 7 April 2026');
  await expect(page.locator('#faq dt')).toHaveText([
    'Do I need Wi-Fi?', 'Is there a listening station near me?', 'Does it work outside North America?',
    'What if a species near me was never illustrated?', 'Do the collages need AI?', 'What is hosting, and what does it cost?', 'Is my data private?']);
  await expect(page.locator('#faq dd').nth(1)).toHaveText('Probably. BirdWeather has listening stations across North America, Europe and beyond, and the frame can use any of them. If none is close, choose one near a place you love, or add your own.');
  await expect(page.locator('#faq dd').nth(2)).toHaveText('Yes. Gould’s books cover the birds of Europe, Asia and Australia, and the frame picks the book that illustrated your species.');
  await expect(page.locator('#faq dd').nth(4)).toHaveText('No. Without AI, the day is laid out in the original illustrations, numbered and keyed like a page in an old natural history book. With AI illustration turned on, the day’s species are painted together in one scene, marked ✦.');
  await expect(page.locator('#faq')).not.toContainText('OpenAI key');
  await expect(page.locator('#keep-posted .form-why')).toHaveText("Not ready to order? We'll write once, when the frames ship.");
  await expect(page.locator('.cat figure')).toHaveCount(12);
  // the wall, in Wells's order: the Wild Turkey first (the art stop's bird), the Carolina Wren last (the one that tears off)
  const A = 'John James Audubon · The Birds of America';
  await expect(page.locator('.cat figure figcaption')).toHaveText([
    `Wild TurkeyMeleagris gallopavo${A}`,
    `Blue JayCyanocitta cristata${A}`,
    `Great Horned OwlBubo virginianus${A}`,
    `Cedar WaxwingBombycilla cedrorum${A}`,
    `Green-breasted MangoAnthracothorax prevostii${A}`,
    `Tufted TitmouseBaeolophus bicolor${A}`,
    'Laughing KookaburraDacelo novaeguineaeJohn Gould · The Birds of Australia',
    `Hermit ThrushCatharus guttatus${A}`,
    `Northern Saw-whet OwlAegolius acadicus${A}`,
    `Gray CatbirdDumetella carolinensis${A}`,
    `Swainson’s WarblerLimnothlypis swainsonii${A}`,
    `Carolina WrenThryothorus ludovicianus${A}`,
  ]);
  await expect(page.locator('.cat figure').first().locator('img')).toHaveAttribute('data-still', 'wild-turkey');
  await expect(page.locator('.cat figure').last().locator('img')).toHaveAttribute('data-still', 'carolina-wren');
  await expect(page.locator('#art-slot .still')).toHaveAttribute('src', 'img/wall/13-wild-turkey.webp');
  for (const gone of ['Parakeet', 'Wood Duck', 'Oriole']) await expect(page.locator('body')).not.toContainText(gone);
  expect(await page.locator('img[src*="parakeet"], img[src*="wood-duck"]').count()).toBe(0);
  await expect(page.locator('body')).not.toContainText('Flamingo');
  await expect(page.locator('.tone button')).toHaveText(['Color', 'B&W']);
  for (const link of await page.getByRole('link', { name: 'Pre-order' }).all()) {
    await expect(link).toHaveAttribute('href', 'https://shop.wells.ee/products/featherframe/');
  }
  // the singing videos, credited in the colophon
  for (const c of ['Northern Cardinal recording by Jonathon Jongsma, xeno-canto XC175226, CC BY-SA 4.0.', 'Northern Cardinal video by Courtney Celley, U.S. Fish and Wildlife Service, public domain.', 'Eastern Bluebird video by Paul Danese, Wikimedia Commons, CC BY-SA 4.0.', 'American Goldfinch video by teyi 徐, Pexels.', 'Eastern Bluebird recording by Jonathon Jongsma, XC79976, CC BY-SA 3.0.', 'American Goldfinch recording by Jonathon Jongsma, XC127600, CC BY-SA 3.0.'])
    await expect(page.locator('.colophon .d')).toContainText(c);
  // the licences and sources, linked
  expect(await page.locator('.colophon .d a').evaluateAll((as) => as.map((a) => a.getAttribute('href')))).toEqual([
    'https://xeno-canto.org/175226', 'https://creativecommons.org/licenses/by-sa/4.0/',
    'https://xeno-canto.org/79976', 'https://creativecommons.org/licenses/by-sa/3.0/',
    'https://xeno-canto.org/127600', 'https://creativecommons.org/licenses/by-sa/3.0/',
    'https://commons.wikimedia.org/wiki/File:20250518_eastern_bluebird_bafflin_wm.webm', 'https://creativecommons.org/licenses/by-sa/4.0/']);
  await expect(page.locator('.colophon .d')).not.toContainText('Blue Jay');
  await expect(page.locator('#how video')).toHaveAttribute('poster', 'video/cardinal.webp');
  await expect(page.locator('#how .t2 .credit')).toHaveText('Video by Courtney Celley, U.S. Fish and Wildlife Service');
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

for (const [w, h] of [[390, 844], [393, 852], [768, 1024], [1024, 768]]) {
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
  expect(box.h).toBeGreaterThan(900 * 2.5);
  const x = async (f: number) => {
    await page.evaluate((y) => scrollTo(0, y), box.y + f * box.h);
    // the row follows on the next frame
    const want = await page.evaluate(() => {
      const b = document.querySelector('.seasons')!.getBoundingClientRect();
      const nav = document.querySelector('.head')!.getBoundingClientRect().height;
      // winter is reached before the stretch ends, and held centred for its last 6%
      return Math.max(0, Math.min(1, (nav - b.top) / ((b.height - (innerHeight - nav)) * 0.94)));
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
  // four seasons, spring to winter, and no repeat of spring at the end
  await expect(page.locator('.season')).toHaveCount(4);
  await expect(page.locator('.season .sc')).toHaveText(['Spring', 'Summer', 'Fall', 'Winter']);
  await expect(page.locator('.season.coda')).toHaveCount(0);
  // winter settles centred before the pin lets go, and stays there to the end of the stretch
  const winter = () => page.locator('.season').last().evaluate((e) => { const r = e.getBoundingClientRect(); return r.left + r.width / 2; });
  await x(0.9);
  expect(Math.abs((await winter()) - 720)).toBeLessThan(40);
  await expect(page.locator('.season').last()).toHaveClass(/\bnow\b/);
  await x(1);
  expect(Math.abs((await winter()) - 720)).toBeLessThan(40);
});

test('the four seasons\' collages load', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const imgs = page.locator('#collage .season img');
  await expect(imgs).toHaveCount(4);
  for (const [i, season] of ['spring', 'summer', 'fall', 'winter'].entries()) {
    const img = imgs.nth(i);
    await expect(img).toHaveAttribute('src', `img/seasons/${season}.webp`);
    await img.evaluate((e: HTMLImageElement) => { e.loading = 'eager'; return e.decode(); });
    expect(await img.evaluate((e: HTMLImageElement) => e.naturalWidth)).toBe(874);
  }
});

test('on a phone the seasons stack', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('html')).not.toHaveClass(/\bseasons-live\b/);
  const tops = await page.locator('.season').evaluateAll((es) => es.map((e) => e.getBoundingClientRect().top));
  expect(tops).toHaveLength(4);
  for (let i = 1; i < tops.length; i++) expect(tops[i]).toBeGreaterThan(tops[i - 1] + 300);
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
  await expect(page.locator('#keep-posted .form-note')).toHaveText("Thanks. We'll write once, when the frames ship.");
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

test('the detections run on the page\'s own clock, silent, and Unmute plays the song with sound', async ({ page }) => {
  await page.goto('/');
  const song = page.locator('#song');
  const unmute = page.locator('.unmute');
  const sg = page.locator('.spectro .sg');
  await expect(unmute).toHaveText('Unmute');
  await expect(unmute).toHaveAttribute('aria-label', 'Play the song with sound');
  await expect(sg).toHaveAttribute('aria-label', 'Play the song with sound');
  await expect(song).toHaveAttribute('preload', 'none');
  await page.locator('.spectro').scrollIntoViewIfNeeded();
  // the playhead crosses the spectrogram by the clock; nothing is played for it
  await expect(page.locator('.playhead')).toBeVisible({ timeout: 15_000 });
  const left = () => page.locator('.playhead').evaluate((e) => parseFloat((e as HTMLElement).style.left));
  const a = await left();
  await page.waitForTimeout(1200);
  expect(await left()).toBeGreaterThan(a);
  expect(await song.evaluate((a: HTMLAudioElement) => a.paused)).toBe(true);
  await unmute.click();
  await expect(unmute).toBeHidden();
  await expect(sg).toHaveAttribute('aria-pressed', 'true');
  await expect(sg).toHaveAttribute('aria-label', 'Mute the song');
  // there is no separate Mute control: the video is the switch
  await expect(page.locator('.mute')).toHaveCount(0);
  // from the start, with sound
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => !a.paused && !a.muted), { timeout: 15_000 }).toBe(true);
  expect(await song.evaluate((a: HTMLAudioElement) => a.currentTime < 1.5)).toBe(true);
  // a click on the video mutes it
  await page.locator('#how video').click({ position: { x: 40, y: 80 } });
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => a.paused)).toBe(true);
  await expect(sg).toHaveAttribute('aria-pressed', 'false');
  await expect(unmute).toBeVisible();
  // and a click on it again plays it with sound, as Unmute does
  await page.locator('#how video').click({ position: { x: 40, y: 80 } });
  await expect(sg).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => !a.paused && !a.muted), { timeout: 15_000 }).toBe(true);
});

test('the detection cycle runs even when the browser refuses to play any audio', async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 852 });
  await page.addInitScript(() => {
    (window as unknown as { __plays: number }).__plays = 0;
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function (this: HTMLMediaElement) {
      if (this instanceof HTMLAudioElement) { (window as unknown as { __plays: number }).__plays++; return Promise.reject(new DOMException('refused', 'NotAllowedError')); }
      return play.call(this);
    };
  });
  await page.goto('/?rate=4');
  await page.locator('#how .ph').scrollIntoViewIfNeeded();
  const name = page.locator('#how .toast .nm');
  await expect(name).toHaveText('Northern Cardinal');
  await expect(name).toHaveText('Eastern Bluebird', { timeout: 10_000 });
  await expect(name).toHaveText('American Goldfinch', { timeout: 10_000 });
  await expect(page.locator('.spectro img')).toHaveAttribute('src', 'img/spectrogram-goldfinch.webp');
  // and it never asked to play the song to get there
  expect(await page.evaluate(() => (window as unknown as { __plays: number }).__plays)).toBe(0);
});

test('with reduced motion the song waits for its button', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.locator('.spectro').scrollIntoViewIfNeeded();
  await page.waitForTimeout(1500);
  const song = page.locator('#song');
  expect(await song.evaluate((a: HTMLAudioElement) => a.paused)).toBe(true);
  const button = page.locator('.unmute');
  await expect(button).toHaveText('Play the song');
  await button.click();
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => !a.paused && !a.muted)).toBe(true);
});

test('each new detection is announced and the frame on the table repaints to it', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  // the detections' clock, sped up
  await page.goto('/?hold=600000&rate=3');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  await page.evaluate(() => scrollTo(0, document.querySelector('.spectro')!.getBoundingClientRect().top + scrollY - 120));
  const table = page.locator('#table-slot');
  const toast = page.locator('.toast');
  const video = page.locator('#how video');
  const credit = page.locator('#how .t2 figcaption');
  const still = page.locator('#table-slot .still');
  const src = () => video.evaluate((v: HTMLVideoElement) => v.currentSrc || v.querySelector('source')!.src);
  await expect(toast).toHaveClass(/\bon\b/);
  await expect(toast.locator('.nm')).toHaveText('Northern Cardinal');
  await expect(credit).toHaveText('Video by Courtney Celley, U.S. Fish and Wildlife Service');
  expect(await src()).toMatch(/video\/cardinal\.(webm|mp4)$/);
  await expect(table).toHaveAttribute('data-shown', 'cardinal', { timeout: 15_000 });
  await expect(toast.locator('.nm')).toHaveText('Eastern Bluebird', { timeout: 15_000 });
  // the card stays up through the detections; the video follows the species
  await expect(toast).toHaveClass(/\bon\b/);
  await expect.poll(src).toMatch(/video\/eastern-bluebird\.(webm|mp4)$/);
  await expect(video).toHaveAttribute('poster', 'video/eastern-bluebird.webp');
  await expect(credit).toHaveText('Video by Paul Danese, Wikimedia Commons');
  await expect(table).toHaveAttribute('data-shown', 'eastern-bluebird', { timeout: 15_000 });
  // the table's still says what it shows
  await expect(still).toHaveAttribute('alt', 'The frame showing the Eastern Bluebird from The Birds of America');
  await expect(page.locator('.spectro img')).toHaveAttribute('src', 'img/spectrogram-eastern-bluebird.webp');
  await expect(page.locator('.spectro img')).toHaveAttribute('alt', 'A spectrogram of an Eastern Bluebird’s song');
  await page.waitForTimeout(5000);
  await expect(toast).toHaveClass(/\bon\b/);
  expect(await toast.evaluate((e) => getComputedStyle(e).opacity)).toBe('1');
  await expect(toast.locator('.nm')).toHaveText('American Goldfinch', { timeout: 15_000 });
  await expect(toast).toHaveClass(/\bon\b/);
  await expect.poll(src).toMatch(/video\/goldfinch\.(webm|mp4)$/);
  await expect(credit).toHaveText('Video by teyi 徐, Pexels');
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

test('on a phone there is no 3D frame: the cover keeps its poster, and no model is fetched', async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 852 });
  const models: string[] = [];
  page.on('request', (r) => { if (/\/models\/|choreo-/.test(r.url())) models.push(r.url()); });
  await page.goto('/?hold=300');
  await page.waitForLoadState('load');
  await page.waitForTimeout(3000);
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(page.locator('#stage .poster')).toBeVisible();
  expect(await page.locator('#stage .poster').evaluate((e: HTMLImageElement) => e.complete && e.naturalWidth > 0)).toBe(true);
  await expect(page.locator('html')).not.toHaveClass(/\bchoreo\b/);
  expect(models).toEqual([]);
});

test('on a phone the sentence and Pre-order come before the frame, on the first screen', async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 659 });
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const [copy, cta, frame] = await Promise.all(['.cover .copy p', '.cover .cta .btn', '.cover .frame'].map((s) => page.locator(s).boundingBox()));
  expect(copy!.y).toBeLessThan(frame!.y);
  expect(cta!.y + cta!.height).toBeLessThan(frame!.y);
  expect(cta!.y + cta!.height).toBeLessThanOrEqual(659);
  // the art's headline at the display size
  const [h2, mega] = await Promise.all(['#art h2', '#how h2'].map((s) => page.locator(s).evaluate((e) => parseFloat(getComputedStyle(e).fontSize))));
  expect(Math.abs(h2 - mega)).toBeLessThan(1);
});

test('on a phone the controls are a thumb\'s size', async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 852 });
  await page.goto('/');
  for (const sel of ['.head .word', '.head .btn', '.tone button', '.unmute', '.field button', '.colophon .c .ul', '.colophon .signin', '.logos a']) {
    for (const el of await page.locator(sel).all()) {
      const b = (await el.boundingBox())!;
      expect(b.height, sel).toBeGreaterThanOrEqual(44);
      expect(b.width, sel).toBeGreaterThanOrEqual(24);
    }
  }
  // the head is slimmer, and the wall's folio is not stuck under it
  expect(await page.locator('.head').evaluate((e) => e.getBoundingClientRect().height)).toBeLessThanOrEqual(57);
  expect(await page.locator('.wall .folio').evaluate((e) => getComputedStyle(e).position)).toBe('static');
});

test('on a phone the wall is one row walked sideways, and B&W keeps its size', async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 852 });
  await page.goto('/');
  const cat = page.locator('.wall .cat');
  await cat.scrollIntoViewIfNeeded();
  const [sw, cw, snap] = await cat.evaluate((e) => [e.scrollWidth, e.clientWidth, getComputedStyle(e).scrollSnapType]);
  expect(sw).toBeGreaterThan(cw * 5);
  expect(snap).toContain('x');
  const tops = await page.locator('.wall .cat figure').evaluateAll((fs) => fs.map((f) => Math.round(f.getBoundingClientRect().top)));
  expect(new Set(tops).size).toBe(1);
  // the book is named only where it changes
  await expect(page.locator('.wall .cat .by:visible')).toHaveCount(3);
  const w = (await page.locator('.wall .cat .im').first().boundingBox())!.width;
  await page.getByRole('button', { name: 'B&W' }).click();
  await page.waitForTimeout(900);
  expect(Math.abs((await page.locator('.wall .cat .im').first().boundingBox())!.width - w)).toBeLessThan(1);
});

test('in the lightbox a swipe steps through the wall, and the running head hides', async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 852 });
  await page.goto('/');
  const names = await page.locator('.wall .cat .nm').allTextContents();
  const first = page.locator('.wall .cat .im').first();
  await first.scrollIntoViewIfNeeded();
  await first.click();
  const box = page.locator('.lightbox');
  await expect(box).toHaveAttribute('aria-label', names[0]);
  await page.waitForTimeout(600);
  expect(await page.locator('.head').evaluate((e) => getComputedStyle(e).visibility)).toBe('hidden');
  const swipe = async (from: number, to: number) => {
    await page.mouse.move(from, 400);
    await page.mouse.down();
    await page.mouse.move((from + to) / 2, 404, { steps: 4 });
    await page.mouse.move(to, 406, { steps: 4 });
    await page.mouse.up();
  };
  await swipe(320, 80);
  await expect(box).toHaveAttribute('aria-label', names[1]);
  await swipe(80, 320);
  await expect(box).toHaveAttribute('aria-label', names[0]);
  await swipe(80, 320);
  await expect(box).toHaveAttribute('aria-label', names[11]);
  // still open: a swipe is not a click outside
  await expect(box).toHaveCount(1);
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
  await page.evaluate(() => scrollBy(0, 100));
  const a = [(await slot.boundingBox())!.y, (await text.boundingBox())!.y];
  await page.evaluate(() => scrollBy(0, 150));
  const b = [(await slot.boundingBox())!.y, (await text.boundingBox())!.y];
  expect(Math.abs(b[0] - a[0])).toBeLessThan(1);
  expect(a[1] - b[1]).toBeGreaterThan(140);
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

test('the spectrogram is the song\'s switch: a click turns the sound on, and off again', async ({ page }) => {
  await page.goto('/');
  const song = page.locator('#song');
  const sg = page.locator('.spectro .sg');
  await expect(sg).toHaveAttribute('role', 'button');
  await expect(sg).toHaveAttribute('aria-pressed', 'false');
  await sg.scrollIntoViewIfNeeded();
  await sg.click({ position: { x: 20, y: 20 } });
  await expect(sg).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => !a.paused && !a.muted), { timeout: 15_000 }).toBe(true);
  await expect(page.locator('.unmute')).toBeHidden();
  await sg.click({ position: { x: 20, y: 20 } });
  await expect(sg).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(() => song.evaluate((a: HTMLAudioElement) => a.paused)).toBe(true);
  await expect(page.locator('.unmute')).toBeVisible();
  // and from the keyboard
  await sg.focus();
  await page.keyboard.press('Enter');
  await expect(sg).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press(' ');
  await expect(sg).toHaveAttribute('aria-pressed', 'false');
});

test('a new detection is a notification pinned to the video', async ({ page }) => {
  await page.goto('/');
  const toast = page.locator('#how .toast');
  await expect(toast).toHaveCount(1);
  // on the photograph, at its top-left corner
  await expect(page.locator('.t2 .ph .toast')).toHaveCount(1);
  await expect(toast.locator('.k')).toHaveText('New detection');
  await expect(toast.locator('.nm')).toHaveText('Northern Cardinal');
  await expect(toast.locator('.when')).toHaveText('Just now');
  await expect(toast.locator('.ic svg')).toHaveCount(1);
  const fonts = await toast.evaluate((t) => [t, ...t.querySelectorAll('*')].map((e) => getComputedStyle(e).fontFamily + '|' + getComputedStyle(e).fontVariantCaps + '|' + getComputedStyle(e).textTransform));
  for (const f of fonts) {
    expect(f).not.toMatch(/Pinyon|IM Fell/);
    expect(f).not.toMatch(/small-caps|uppercase/);
  }
  const [name, kicker] = await Promise.all(['.nm', '.k'].map((s) => toast.locator(s).evaluate((e) => {
    const c = getComputedStyle(e);
    return [c.fontFamily.split(',')[0].replace(/['"]/g, ''), c.fontSize, c.fontWeight];
  })));
  expect(name).toEqual(['Inter', '16px', '600']);
  expect(kicker).toEqual(['Inter', '12px', '500']);
  const [t, ph] = await Promise.all([toast.boundingBox(), page.locator('.t2 .ph video').boundingBox()]);
  expect(t!.x).toBeLessThan(ph!.x + 10);
  expect(t!.y).toBeLessThan(ph!.y + 10);
  expect(t!.y + t!.height).toBeGreaterThan(ph!.y);
});

test('the page turns to night at the collage and stays night to the end, day again above it', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const html = page.locator('html');
  // 'rgb(…)' or color-mix's 'color(srgb …)', as 0–255
  const rgb = (c: string) => {
    const n = c.match(/[\d.]+/g)!.slice(0, 3).map(Number);
    return (c.startsWith('color(') ? n.map((v) => v * 255) : n).map(Math.round).join(',');
  };
  const colour = (sel: string, prop: 'color' | 'backgroundColor') => page.locator(sel).evaluate((e, p) => getComputedStyle(e)[p], prop).then(rgb);
  const bg = () => colour('body', 'backgroundColor');
  await expect(html).not.toHaveClass(/\bnight\b/);
  const day = await bg();
  const box = await page.locator('#collage').evaluate((e) => ({ y: e.getBoundingClientRect().top + scrollY, h: e.getBoundingClientRect().height }));
  const nav = await page.locator('.head').evaluate((e) => e.getBoundingClientRect().bottom);
  const brow = await page.locator('#collage .eyebrow').evaluate((e) => e.getBoundingClientRect().top + scrollY);
  // the line is 150 px below the running head's bottom, or 45% of the window where that is further (night.ts)
  const early = Math.max(150, Math.round(0.45 * 900));
  // the eyebrow a few pixels short of that line: still day
  await page.evaluate((y) => scrollTo(0, y), brow - nav - early - 6);
  await page.waitForTimeout(700);
  await expect(html).not.toHaveClass(/\bnight\b/);
  expect(await bg()).toBe(day);
  // just past it: night, and the colours arrive by themselves in about 450 ms, without further scrolling
  await page.evaluate((y) => scrollTo(0, y), brow - nav - early + 2);
  await expect(html).toHaveClass(/\bnight\b/);
  const t0 = Date.now();
  await page.waitForTimeout(100);
  // (time-based: part-way after 100 ms)
  expect(await bg()).not.toBe('26,26,26');
  await expect.poll(bg, { intervals: [25], timeout: 1000 }).toBe('26,26,26');
  expect(Date.now() - t0).toBeLessThan(700);
  // back down below the line: day again
  await page.evaluate((y) => scrollTo(0, y), brow - nav - early - 6);
  await expect(html).not.toHaveClass(/\bnight\b/);
  await expect.poll(bg).toBe(day);
  await page.evaluate((y) => scrollTo(0, y), box.y + 200);
  await expect(html).toHaveClass(/\bnight\b/);
  await expect.poll(bg).toBe('26,26,26');
  // (exactly, once the 450 ms ease has finished)
  await expect.poll(() => page.locator('body').evaluate((e) => getComputedStyle(e).backgroundColor)).toMatch(/^rgb\(26, 26, 26\)$|^color\(srgb 0\.10196\d* 0\.10196\d* 0\.10196\d*\)$/);
  // the running head goes dark with it
  expect(await colour('.head', 'backgroundColor')).toBe('26,26,26');
  expect(await colour('#collage h2', 'color')).toBe('242,241,236');
  // and it stays night below the collage, through the details, the reservation, the questions and the colophon
  for (const sel of ['#specs', '.close', '#faq', '.colophon']) {
    await page.locator(sel).evaluate((e) => e.scrollIntoView({ block: 'center' }));
    await expect(html).toHaveClass(/\bnight\b/);
  }
  await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
  await expect(html).toHaveClass(/\bnight\b/);
  await expect.poll(bg).toBe('26,26,26');
  expect(await colour('#specs .spec dd:first-of-type', 'color')).toBe('242,241,236');
  expect(await colour('.close .btn', 'backgroundColor')).toBe('242,241,236');
  expect(await colour('.close .btn', 'color')).toBe('26,26,26');
  expect(await colour('.qa dt:first-of-type', 'color')).toBe('242,241,236');
  expect(await colour('.field input', 'color')).toBe('242,241,236');
  // the display's diagonal is drawn on the picture: black on white, whatever the page's light
  expect(await page.locator('.diag span').first().evaluate((e) => getComputedStyle(e).color).then(rgb)).toBe('18,18,18');
  // scrolled back up above the threshold: day again
  await page.evaluate((y) => scrollTo(0, y), brow - nav - early - 200);
  await expect(html).not.toHaveClass(/\bnight\b/);
  await expect.poll(bg).toBe(day);
  expect(day).toBe('255,255,255');
});

test('the frame\'s shadow is the table\'s: none until its foot meets the table', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  const [tear, table] = await page.evaluate(() => {
    const f = (window as any).__ff();
    const n = f.stops.length;
    return [f.stops[n - 2][1], f.stops[n - 1][0]];
  });
  const ground = async (y: number, want: number) => {
    await page.evaluate((y) => scrollTo(0, y), y);
    expect(await page.evaluate(() => (window as any).__ff().st.ground)).toBe(want);
    // (the canvas follows on its next frame)
    await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--land').trim())).toBe(want.toFixed(3));
  };
  // in the air, most of the way down: no shadow at all
  for (const f of [0.3, 0.6, 0.85]) await ground(tear + f * (table - tear), 0);
  // at rest on the table
  await ground(table + 20, 1);
});

test('on a phone the cover\'s frame stands on the table too', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const bg = await page.locator('.cover .frame').evaluate((e) => {
    const c = getComputedStyle(e, '::before');
    return { content: c.content, image: c.backgroundImage };
  });
  expect(bg.content).not.toBe('none');
  expect(bg.image).toContain('linear-gradient');
  const [room, frame] = await Promise.all([
    page.locator('.cover .frame').evaluate((e) => { const c = getComputedStyle(e, '::before'); return parseFloat(c.width); }),
    page.locator('.cover .frame').boundingBox(),
  ]);
  expect(room).toBeGreaterThan(frame!.width);
});

test('the video plays muted in view, with the spectrogram over its lower third', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const video = page.locator('#how video');
  await expect(video).toHaveJSProperty('muted', true);
  await expect(video).toHaveAttribute('loop', '');
  await video.scrollIntoViewIfNeeded();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => !v.paused && v.currentTime > 0.2), { timeout: 20_000 }).toBe(true);
  const [v, sp, un] = await Promise.all([video, page.locator('.spectro'), page.locator('.unmute')].map((l) => l.boundingBox()));
  // a band across the video's lower third
  expect(Math.abs(sp!.y + sp!.height - (v!.y + v!.height))).toBeLessThan(2);
  expect(Math.abs(sp!.height - v!.height / 3)).toBeLessThan(4);
  expect(Math.abs(sp!.width - v!.width)).toBeLessThan(2);
  // Unmute in the middle of the video
  expect(Math.abs(un!.x + un!.width / 2 - (v!.x + v!.width / 2))).toBeLessThan(2);
  expect(Math.abs(un!.y + un!.height / 2 - (v!.y + v!.height / 2))).toBeLessThan(2);
  // the ink drawn bright white over a dark scrim: inverted, lifted and contrasted
  const filter = await page.locator('.spectro img').evaluate((e) => getComputedStyle(e).filter);
  expect(filter).toContain('invert(1)');
  expect(Number(filter.match(/brightness\(([\d.]+)\)/)![1])).toBeGreaterThanOrEqual(2);
  expect(Number(filter.match(/contrast\(([\d.]+)\)/)![1])).toBeGreaterThan(1);
  const scrim = await page.locator('.spectro').evaluate((e) => getComputedStyle(e).backgroundImage);
  expect(Math.max(...[...scrim.matchAll(/rgba\(0, 0, 0, ([\d.]+)\)/g)].map((m) => Number(m[1])))).toBeGreaterThanOrEqual(0.7);
});

test('with reduced motion the video shows its poster and does not play', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const video = page.locator('#how video');
  await video.scrollIntoViewIfNeeded();
  await page.waitForTimeout(1500);
  await expect(video).toHaveAttribute('poster', 'video/cardinal.webp');
  expect(await video.evaluate((v: HTMLVideoElement) => [v.paused, v.autoplay])).toEqual([true, false]);
});

test('the frame freezes dead centre for its dwell while a light bar sweeps its glass', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  const [s0, s1] = await page.evaluate(() => (window as any).__ff().stops[1].slice(0, 2));
  expect(s1 - s0).toBeGreaterThan(200);
  const at = async (y: number) => {
    await page.evaluate((y) => scrollTo(0, y), y);
    return page.evaluate(() => (window as any).__ff().st);
  };
  const states = [];
  for (const f of [0, 0.25, 0.5, 0.75, 1]) states.push(await at(Math.round(s0 + f * (s1 - s0))));
  for (const st of states) {
    expect(st.rect.y).toBeCloseTo(states[0].rect.y, 3);
    expect(st.rect.h).toBeCloseTo(states[0].rect.h, 3);
    expect(st.rect.x).toBeCloseTo(states[0].rect.x, 3);
  }
  // dead centre in the window below the running head, and wholly on screen
  const nav = await page.locator('.head').evaluate((e) => e.getBoundingClientRect().height);
  const r = states[0].rect;
  expect(Math.abs(r.y - nav - (900 - nav - r.h) / 2)).toBeLessThan(1);
  expect(r.y).toBeGreaterThanOrEqual(nav);
  expect(r.y + r.h).toBeLessThanOrEqual(900);
  // the bar rises through the dwell
  const bars = states.map((st) => st.bar);
  for (let i = 1; i < bars.length; i++) expect(bars[i]).toBeGreaterThan(bars[i - 1]);
  // on the way in from the cover, the frame never leaves the window's top or bottom
  for (const f of [0.2, 0.4, 0.6, 0.8]) {
    const st = await at(Math.round(f * s0));
    expect(st.rect.y).toBeGreaterThanOrEqual(nav - 1);
    expect(st.rect.y + st.rect.h).toBeLessThanOrEqual(901);
  }
});

test('a wall frame opens large and closes again', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const frame = page.locator('.wall .cat figure').nth(4).locator('.im');
  await expect(frame).toHaveAttribute('role', 'button');
  await expect(frame).toHaveAttribute('aria-label', 'Green-breasted Mango');
  await frame.scrollIntoViewIfNeeded();
  const box = page.locator('.lightbox');
  // a click opens it, centred, as large as fits, with its caption's three lines
  await frame.click();
  await expect(box).toBeVisible();
  await expect(box).toHaveAttribute('role', 'dialog');
  await expect(box.locator('figcaption')).toHaveText('Green-breasted MangoAnthracothorax prevostiiJohn James Audubon · The Birds of America');
  const close = box.getByRole('button', { name: 'Close' });
  await expect(close).toBeFocused();
  await page.waitForTimeout(600);
  // (the flight in is done: under the suite's load its first frames can come late)
  await box.locator('img').evaluate((i) => Promise.all(i.getAnimations().map((a) => a.finished)));
  const img = (await box.locator('img').boundingBox())!;
  expect(img.height).toBeGreaterThan(900 * 0.75);
  expect(Math.abs(img.x + img.width / 2 - 720)).toBeLessThan(2);
  // white at 96%
  const bgc = await box.evaluate((e) => getComputedStyle(e).backgroundColor);
  expect(Number(bgc.match(/([\d.]+)\)$/)![1])).toBeCloseTo(0.96, 1);
  expect(bgc).toMatch(/^(rgba\(255, 255, 255|oklab\(0\.99|color\(srgb 1 1 1)/);
  // focus stays in it, going round Close, Previous and Next
  const prevB = box.getByRole('button', { name: 'Previous' });
  const nextB = box.getByRole('button', { name: 'Next' });
  await page.keyboard.press('Tab');
  await expect(prevB).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(nextB).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(close).toBeFocused();
  // Esc closes it, and the focus goes back to the frame
  await page.keyboard.press('Escape');
  await expect(box).toHaveCount(0);
  await expect(frame).toBeFocused();
  // Enter opens it; a click outside closes it
  await page.keyboard.press('Enter');
  await expect(box).toBeVisible();
  await page.waitForTimeout(600);
  await page.mouse.click(30, 150); // (the left edge's middle is Previous)
  await expect(box).toHaveCount(0);
  await expect(frame).toBeFocused();
  // and Close closes it
  await frame.click();
  await page.waitForTimeout(600);
  await close.click();
  await expect(box).toHaveCount(0);
  await expect(frame).toBeFocused();
});

test('the last frame tears off under the sticky folio, and flies under it and the running head', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  const tear = await page.evaluate(() => { const f = (window as any).__ff(); return f.stops[f.stops.length - 2][0]; });
  await page.evaluate((y) => scrollTo(0, y), tear);
  const [nav, folio, last] = await Promise.all(['.head', '.wall .folio', '.wall .cat figure:last-child .im'].map((s) =>
    page.locator(s).evaluate((e) => { const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom }; })));
  // it sets off while wholly on screen, its top just under the folio
  expect(Math.abs(folio.top - nav.bottom)).toBeLessThan(2);
  expect(last.top).toBeGreaterThanOrEqual(folio.bottom);
  expect(last.top - folio.bottom).toBeLessThan(30);
  // on its way to the table it is drawn over the text but under the folio and the head
  await page.evaluate((y) => scrollTo(0, y + 150), tear);
  await expect(page.locator('canvas.ff3d:not(.empty)')).toHaveClass(/\bover\b/);
  const z = (sel: string) => page.locator(sel).first().evaluate((e) => Number(getComputedStyle(e).zIndex));
  const canvas = await z('canvas.ff3d:not(.empty)');
  expect(canvas).toBeGreaterThan(0);
  expect(canvas).toBeLessThan(await z('.wall .folio'));
  expect(canvas).toBeLessThan(await z('.head'));
  // and it does not ride up with the page as it leaves
  const st = await page.evaluate(() => (window as any).__ff().st.rect);
  expect(st.y).toBeGreaterThanOrEqual(folio.bottom - 1);
});

test('each chapter opens with an eyebrow, not a numbered rule', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.folio .eyebrow')).toHaveText(['The art', 'From the collection', 'How it works', 'The collage', 'Technical details', 'Questions']);
  for (const f of await page.locator('.folio').all()) {
    expect(await f.evaluate((e) => getComputedStyle(e).borderTopWidth)).toBe('0px');
  }
  const [size, colour, graphite] = await page.locator('.folio .eyebrow').first().evaluate((e) => {
    const c = getComputedStyle(e);
    const g = document.createElement('span');
    g.style.color = 'var(--graphite)';
    document.body.append(g);
    const out = [c.fontSize, c.color, getComputedStyle(g).color];
    g.remove();
    return out;
  });
  expect(size).toBe('12px');
  expect(colour).toBe(graphite);
  expect(await page.locator('body').innerText()).not.toMatch(/\b(I|II|III|IV|V|VI)\. /);
  // the collection's sticky row keeps its switch
  await expect(page.locator('.wall .folio .tone button')).toHaveCount(2);
});

test('in the lightbox the arrow keys step through the wall, wrapping', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const names = await page.locator('.wall .cat .nm').allTextContents();
  expect(names.length).toBe(12);
  const box = page.locator('.lightbox');
  const shown = () => box.locator('figure:last-of-type .nm').textContent();
  const last = page.locator('.wall .cat figure').last().locator('.im');
  await last.scrollIntoViewIfNeeded();
  await last.click();
  await expect(box).toHaveAttribute('aria-label', names[11]);
  // Right from the last wraps to the first
  await page.keyboard.press('ArrowRight');
  await expect(box).toHaveAttribute('aria-label', names[0]);
  await expect.poll(shown).toBe(names[0]);
  // a quick cross-fade: one figure left once it is done
  await expect(box.locator('figure')).toHaveCount(1);
  await page.keyboard.press('ArrowRight');
  await expect.poll(shown).toBe(names[1]);
  // Left twice: back past the first to the last
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowLeft');
  await expect(box).toHaveAttribute('aria-label', names[11]);
  await expect.poll(shown).toBe(names[11]);
  // the buttons do the same
  await box.hover();
  await box.getByRole('button', { name: 'Next' }).click();
  await expect.poll(shown).toBe(names[0]);
  await box.getByRole('button', { name: 'Previous' }).click();
  await expect.poll(shown).toBe(names[11]);
  // it closes on the frame it shows, and the focus goes back to that frame
  await page.keyboard.press('ArrowLeft');
  await expect.poll(shown).toBe(names[10]);
  await page.keyboard.press('Escape');
  await expect(box).toHaveCount(0);
  await expect(page.locator('.wall .cat figure').nth(10).locator('.im')).toBeFocused();
});

test('no hatched surface anywhere, and the live frame draws no shadow floor', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  const hatched = await page.evaluate(() => [...document.querySelectorAll('*')].flatMap((e) =>
    [getComputedStyle(e), getComputedStyle(e, '::before'), getComputedStyle(e, '::after')]
      .filter((c) => /repeating-|url\([^)]*(hatch|table|floor)/.test(c.backgroundImage))
      .map(() => e.tagName + '.' + e.className)));
  expect(hatched).toEqual([]);
  expect(await page.evaluate(() => (window as any).__ff().floor)).toBe(false);
});

test("the cover's room fades to white across the text column", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  for (const [sel, pseudo] of [['.cover', '::before'], ['.table', '::before']]) {
    const mask = await page.locator(sel).evaluate((e, p) => { const c = getComputedStyle(e, p); return c.maskImage || c.webkitMaskImage; }, pseudo);
    expect(mask).toMatch(/^linear-gradient\(to right, (transparent|rgba\(0, 0, 0, 0\))/);
  }
});

test('each eyebrow sits right above its headline', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  for (const id of ['art', 'how', 'collage']) {
    const [e, h] = await Promise.all([page.locator(`#${id} .eyebrow`).boundingBox(), page.locator(`#${id} h2`).boundingBox()]);
    const gap = h!.y - (e!.y + e!.height);
    expect(gap).toBeGreaterThanOrEqual(0);
    expect(gap).toBeLessThanOrEqual(20);
    expect(Math.abs(e!.x - h!.x)).toBeLessThan(12);
  }
});

test('the wall\'s folio paints its paper only while it is stuck', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  const folio = page.locator('.wall .folio');
  const paper = () => folio.evaluate((e) => [getComputedStyle(e).backgroundColor, getComputedStyle(e).boxShadow]);
  // coming up the page, not yet under the running head: bare
  await page.evaluate(() => scrollTo(0, document.querySelector('.wall')!.getBoundingClientRect().top + scrollY - 500));
  await expect(folio).not.toHaveClass(/\bstuck\b/);
  expect(await paper()).toEqual(['rgba(0, 0, 0, 0)', 'none']);
  // stuck under it: paper
  await page.evaluate(() => scrollTo(0, document.querySelector('.wall')!.getBoundingClientRect().top + scrollY + 600));
  await expect(folio).toHaveClass(/\bstuck\b/);
  expect((await paper())[0]).not.toBe('rgba(0, 0, 0, 0)');
});

test('the glass never refreshes while the frame is in flight', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  const stops = await page.evaluate(() => (window as any).__ff().stops.map((x: number[]) => [x[0], x[1]]));
  const st = async (y: number) => { await page.evaluate((y) => scrollTo(0, y), Math.round(y)); return page.evaluate(() => (window as any).__ff().st); };
  // from the cover to the centre: the cycle, then no change, until the frame is all but still
  for (const f of [0.2, 0.5, 0.8]) expect((await st(f * stops[1][0])).screen).not.toBe('art');
  expect((await st(stops[1][0] + 5)).screen).toBe('art');
  // from the wall's last place to the table: the wren all the way, the detection once it has landed
  const n = stops.length;
  const [tear, table] = [stops[n - 2][1], stops[n - 1][0]];
  for (const f of [0.2, 0.5, 0.8, 0.97]) expect((await st(tear + f * (table - tear))).screen).toBe('last');
  expect((await st(table + 5)).screen).toBe('table');
});

test('each of the running head\'s links lands its section\'s eyebrow just under the head', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/?hold=600000');
  await expect(page.locator('canvas.ff3d')).toHaveClass(/\blive\b/, { timeout: 20_000 });
  for (const id of ['art', 'how', 'specs', 'faq']) {
    await page.evaluate(() => scrollTo(0, 0));
    await page.locator(`.head nav a[href="#${id}"]`).click();
    await expect(page).toHaveURL(new RegExp(`#${id}$`));
    // (a smooth scroll: wait for it to arrive)
    const gap = () => page.evaluate((id) => document.querySelector(`#${id} .eyebrow`)!.getBoundingClientRect().top - document.querySelector('.head')!.getBoundingClientRect().bottom, id);
    await expect.poll(async () => { const g = await gap(); return g >= 0 && g <= 40; }, { message: id, timeout: 10_000 }).toBe(true);
    await page.waitForTimeout(400);
    const g = await gap();
    expect(g, id).toBeGreaterThanOrEqual(0);
    expect(g, id).toBeLessThanOrEqual(40);
    if (id === 'art') {
      // the frame pinned beside the headline, not on its way there
      // (the art stop is the journey's third: the cover, the centre, the art)
      const [y, art] = await page.evaluate(() => [scrollY, (window as any).__ff().stops[2]]);
      expect(y).toBeGreaterThanOrEqual(art[0]);
      expect(y).toBeLessThanOrEqual(art[1]);
      await expect(page.locator('#art h2')).toBeInViewport();
    }
  }
});

test('on a phone the links land under the slimmer head too', async ({ page }) => {
  await page.setViewportSize({ width: 393, height: 852 });
  await page.goto('/');
  // (the section links are hidden on a phone: a page opened at one lands the same way)
  for (const id of ['how', 'specs']) {
    await page.goto(`/#${id}`);
    await page.waitForLoadState('load');
    await expect.poll(async () => {
      const [nav, brow] = await Promise.all([page.locator('.head').evaluate((e) => e.getBoundingClientRect().bottom), page.locator(`#${id} .eyebrow`).evaluate((e) => e.getBoundingClientRect().top)]);
      return brow - nav >= 0 && brow - nav <= 40;
    }, { timeout: 10_000 }).toBe(true);
  }
});
