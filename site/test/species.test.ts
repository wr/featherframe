import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const root = new URL('../public/', import.meta.url).pathname;
const data = JSON.parse(readFileSync(`${root}species.json`, 'utf8'));

test('every size has one screen per species, and every file exists', () => {
  assert.ok(data.species.length >= 4);
  for (const key of ['13', '10']) {
    const s = data.sizes[key];
    assert.equal(s.screens.length, data.species.length, `size ${key}`);
    for (const f of [s.model, ...s.screens]) assert.ok(existsSync(root + f), f);
  }
});

test('heard times are HH:MM', () => {
  for (const s of data.species) assert.match(s.heard, /^\d{2}:\d{2}$/);
});

test('every file the page and its stylesheet name exists', () => {
  const page = readFileSync(`${root}index.html`, 'utf8');
  const css = readFileSync(`${root}styles.css`, 'utf8');
  const local = [...page.matchAll(/(?:src|href)="([^"#:]+)"/g), ...css.matchAll(/url\('([^']+)'\)/g)]
    .map((m) => m[1]).filter((f) => f !== '/' && f !== 'main.js');
  assert.ok(local.length > 20);
  for (const f of [...local, 'img/og.jpg', 'favicon.png', 'fonts/OFL-EBGaramond.txt']) assert.ok(existsSync(root + f), f);
});
