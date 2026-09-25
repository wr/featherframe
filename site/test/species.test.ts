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

test('the art and drawings exist', () => {
  for (const f of ['img/exploded.webp', 'img/ww.svg', 'img/og.jpg', 'favicon.png',
    'img/art/nighthawk.webp', 'img/art/cardinal.webp', 'img/art/blue-jay.webp',
    'img/art/goldfinch.webp', 'img/art/robin.webp', 'img/art/carolina-wren.webp',
    'img/art/collage.webp']) {
    assert.ok(existsSync(root + f), f);
  }
});
