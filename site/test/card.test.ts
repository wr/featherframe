import { test } from 'node:test';
import assert from 'node:assert/strict';
import { heardText, isLongName } from '../src/card.ts';

test('heardText names the part of the day', () => {
  assert.equal(heardText('08:14'), 'Heard at 8:14 this morning');
  assert.equal(heardText('12:00'), 'Heard at 12:00 this afternoon');
  assert.equal(heardText('13:05'), 'Heard at 1:05 this afternoon');
  assert.equal(heardText('19:30'), 'Heard at 7:30 this evening');
  assert.equal(heardText('00:40'), 'Heard at 12:40 last night');
});

test('isLongName flags names that need the smaller label size', () => {
  assert.equal(isLongName('Common Nighthawk'), false);
  assert.equal(isLongName('American Goldfinch'), false);
  assert.equal(isLongName('Black-capped Chickadee'), true);
});
