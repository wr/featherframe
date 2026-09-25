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
  await expect(page.locator('.wordmark').first()).toHaveText('Featherframe');
  expect(errors).toEqual([]);
});

test('every section and its key copy is there', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('h1')).toHaveText('The species heard near your home, as Audubon painted them.');
  await expect(page.locator('h1')).toHaveCount(1);
  for (const id of ['how', 'art', 'sizes', 'faq', 'build']) await expect(page.locator(`#${id}`)).toBeVisible();
  await expect(page.locator('.promise h3')).toHaveText(['Real art', 'No glow', 'No subscription']);
  await expect(page.locator('#how h2')).toHaveText('How it works');
  await expect(page.locator('#art h2')).toHaveText('The art');
  await expect(page.locator('#sizes h2')).toHaveText('Two sizes');
  await expect(page.locator('#faq h2')).toHaveText('Questions');
  await expect(page.locator('#faq details')).toHaveCount(6);
  await expect(page.locator('#build h3')).toHaveText(['Pre-order Featherframe', 'Build your own']);
  await expect(page.locator('figcaption')).toHaveText([
    'Fig. 1 — The frame, on its kickstand', "Fig. 2 — What's inside", 'Fig. 3 — Four of the 435',
    "Fig. 4 — A day's collage", 'Fig. 5 — Both sizes, to scale',
  ]);
  await expect(page.locator('#label .label-kicker')).toHaveText('Heard near your home');
  await expect(page.locator('#label p.label-name')).toHaveText('Common Nighthawk');
  await expect(page.locator('#label .label-latin')).toHaveText('Chordeiles minor');
  await expect(page.locator('#label .label-heard')).toHaveText('Heard at 8:14 this morning');
  await expect(page.locator('#label .sizes-switch button')).toHaveText(['13-inch', '10-inch']);
  const preorder = page.getByRole('link', { name: 'Pre-order' }).first();
  await expect(preorder).toHaveAttribute('href', 'https://shop.wells.ee/products/featherframe/');
  await expect(page.getByRole('link', { name: 'Sign in' }).first()).toHaveAttribute('href', 'https://app.featherframe.app/');
  await expect(page.locator('img[src*="robin"], img[src*="carolina-wren"]')).toHaveCount(0);
  const body = (await page.locator('body').innerText()).toLowerCase();
  for (const banned of ['plate', 'on the wall', 'on the glass', 'hand-coloured', 'colour']) expect(body).not.toContain(banned);
  // Eyebrows are the section numbers only; the titles are not repeated over themselves.
  await expect(page.locator('.section-head .eyebrow, .faq-head .eyebrow')).toHaveText(['01', '02', '03', '04']);
});

test('the script and engraved faces appear only in the wordmark and the label', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const stray = await page.evaluate(() => {
    const out: string[] = [];
    for (const el of document.body.querySelectorAll<HTMLElement>('*')) {
      const own = [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && n.textContent!.trim());
      if (!own || !el.getClientRects().length) continue;
      const family = getComputedStyle(el).fontFamily;
      if (/Pinyon|IM Fell/i.test(family) && !el.closest('.brand, #label')) out.push(`${el.tagName}.${el.className}: ${family}`);
    }
    return out;
  });
  expect(stray).toEqual([]);
  expect(await page.locator('#label .label-name').evaluate((e) => getComputedStyle(e).fontFamily)).toMatch(/Pinyon/);
  expect(await page.locator('#label .label-latin').evaluate((e) => getComputedStyle(e).fontFamily)).toMatch(/IM Fell/);
  expect(await page.locator('h1').evaluate((e) => getComputedStyle(e).fontFamily)).toMatch(/^Inter/);
});

test('a long species name stays inside the label card, words kept whole', async ({ page }) => {
  await page.route('**/species.json', async (route) => {
    const res = await route.fetch();
    const data = await res.json();
    data.species[1] = { name: 'Northern Rough-winged Swallow', latin: 'Stelgidopteryx serripennis', heard: '09:02' };
    await route.fulfill({ response: res, json: data });
  });
  for (const width of [1440, 1000, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/?hold=300');
    await expect(page.locator('#label .label-name')).toHaveText('Northern Rough-winged Swallow', { timeout: 20_000 });
    await expect(page.locator('#label .label-name')).toHaveClass(/label-name--long/);
    const fit = await page.evaluate(() => {
      const card = document.querySelector('#label')!.getBoundingClientRect();
      const words = [...document.querySelectorAll('#label .label-name .word')].map((w) => w.getClientRects().length);
      const r = document.createRange();
      r.selectNodeContents(document.querySelector('#label .label-name')!);
      const t = r.getBoundingClientRect();
      return { inside: t.left >= card.left && t.right <= card.right - 16, whole: words.every((n) => n === 1) };
    });
    expect(fit).toEqual({ inside: true, whole: true });
  }
});

test('the nav gains its hairline only once the page scrolls', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.nav')).not.toHaveClass(/\bscrolled\b/);
  await page.evaluate(() => window.scrollTo(0, 400));
  await expect(page.locator('.nav')).toHaveClass(/\bscrolled\b/);
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.locator('.nav')).not.toHaveClass(/\bscrolled\b/);
});

test('the label card is not announced (it cycles on its own)', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#label')).not.toHaveAttribute('aria-live');
});

test('no horizontal scroll on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(sw).toBeLessThanOrEqual(cw);
});

test('the card follows the frame from one species to the next', async ({ page }) => {
  await page.goto('/?hold=300');
  await expect(page.locator('#stage canvas')).toHaveCount(1, { timeout: 20_000 });
  await expect(page.locator('#label .label-name')).toHaveText('Northern Cardinal', { timeout: 20_000 });
  await expect(page.locator('#label .label-heard')).toHaveText('Heard at 9:02 this morning');
  // It changed as the Cardinal arrived, before the refresh settled on it…
  expect(await page.locator('#stage').getAttribute('data-shown')).toBeNull();
  // …and the settle that follows leaves it where it is.
  await expect(page.locator('#stage')).toHaveAttribute('data-shown', '1', { timeout: 20_000 });
  await expect(page.locator('#label .label-name')).toHaveText('Northern Cardinal');
});

test('the phone menu opens the section links and closes', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const button = page.getByRole('button', { name: 'Menu' });
  const menu = page.locator('#menu');
  await expect(page.locator('.links')).toBeHidden();
  await expect(button).toHaveAttribute('aria-expanded', 'false');
  await expect(menu).toBeHidden();
  await button.click();
  await expect(button).toHaveAttribute('aria-expanded', 'true');
  await expect(menu.getByRole('link')).toHaveText(['How it works', 'The art', 'Sizes', 'FAQ', 'Sign in']);
  await page.keyboard.press('Escape');
  await expect(menu).toBeHidden();
  await expect(button).toBeFocused();
  await button.click();
  await menu.getByRole('link', { name: 'Sizes' }).click();
  await expect(menu).toBeHidden();
  await expect(button).toHaveAttribute('aria-expanded', 'false');
  await expect(page).toHaveURL(/#sizes$/);
});

test('the menu button is only on narrow screens', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Menu' })).toBeHidden();
  await expect(page.locator('.links')).toBeVisible();
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

test('a size chosen before species.json loads still shows its poster', async ({ page }) => {
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
  await page.goto('/');
  await page.getByRole('button', { name: '10-inch' }).click();
  await expect(page.locator('#stage .poster')).toHaveAttribute('src', 'img/poster-10.webp', { timeout: 5000 });
});

test('a frame superseded while loading leaves the poster showing', async ({ page }) => {
  // The 13-inch model arrives late, after the 10-inch has been chosen, and the
  // 10-inch model never arrives: the stage must fall back to its poster.
  await page.route('**/models/featherframe-13.glb', async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });
  await page.route('**/models/featherframe.glb', (route) => route.abort());
  await page.goto('/?hold=300');
  await page.getByRole('button', { name: '10-inch' }).click();
  await page.waitForTimeout(4000);
  await expect(page.locator('#stage canvas')).toHaveCount(0);
  await expect(page.locator('#stage')).not.toHaveClass(/\blive\b/);
  expect(await page.locator('#stage .poster').evaluate((e) => getComputedStyle(e).opacity)).toBe('1');
});
