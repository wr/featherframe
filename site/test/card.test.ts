import { test } from 'node:test';
import assert from 'node:assert/strict';
import { heardText, isLongName } from '../src/card.ts';

test('heardText names the part of the day', () => {
  assert.equal(heardText('08:14'), 'This morning at 08:14');
  assert.equal(heardText('04:59'), 'Last night at 04:59');
  assert.equal(heardText('05:00'), 'This morning at 05:00');
  assert.equal(heardText('12:00'), 'This afternoon at 12:00');
  assert.equal(heardText('13:05'), 'This afternoon at 13:05');
  assert.equal(heardText('17:00'), 'This evening at 17:00');
  assert.equal(heardText('19:30'), 'This evening at 19:30');
  assert.equal(heardText('00:40'), 'Last night at 00:40');
});

test('isLongName flags names that need the smaller label size', () => {
  assert.equal(isLongName('Common Nighthawk'), false);
  assert.equal(isLongName('American Goldfinch'), false);
  assert.equal(isLongName('Black-capped Chickadee'), true);
});
