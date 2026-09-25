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
  for (const id of ['how', 'art', 'sizes', 'faq', 'build']) await expect(page.locator(`#${id}`)).toBeVisible();
  await expect(page.locator('.promise')).toHaveText('No camera. No app. No subscription. Real paintings by a human hand, not AI.');
  await expect(page.locator('#label .label-kicker')).toHaveText('Heard near your home');
  await expect(page.locator('#label .label-name')).toHaveText('Common Nighthawk');
  await expect(page.locator('#label .label-heard')).toHaveText('Heard at 8:14 this morning');
  const preorder = page.getByRole('link', { name: 'Pre-order' }).first();
  await expect(preorder).toHaveAttribute('href', 'https://shop.wells.ee/products/featherframe/');
  await expect(page.getByRole('link', { name: 'Sign in' }).first()).toHaveAttribute('href', 'https://app.featherframe.app/');
  const body = (await page.locator('body').innerText()).toLowerCase();
  for (const banned of ['plate', 'on the wall', 'on the glass']) expect(body).not.toContain(banned);
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
