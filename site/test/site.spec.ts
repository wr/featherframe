import { expect, test } from '@playwright/test';

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
