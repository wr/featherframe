import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker, { helpTarget, slug } from '../src/worker.ts';

const env = { ASSETS: { fetch: async (r: Request) => new Response(`asset ${new URL(r.url).pathname}`) } };

test('www redirects to the apex, keeping the path and query', async () => {
  const res = await worker.fetch(new Request('https://www.featherframe.app/img/og.jpg?x=1'), env);
  assert.equal(res.status, 301);
  assert.equal(res.headers.get('Location'), 'https://featherframe.app/img/og.jpg?x=1');
});

test('the apex is served from the assets', async () => {
  const res = await worker.fetch(new Request('https://featherframe.app/'), env);
  assert.equal(await res.text(), 'asset /');
});

const WIKI = 'https://github.com/wr/featherframe/wiki';

test('/help goes to the wiki, a topic to its page and heading', async () => {
  for (const [path, to] of [
    ['/help', `${WIKI}/Home`],
    ['/help/', `${WIKI}/Home`],
    ['/help/setup', `${WIKI}/Set-up-your-frame`],
    ['/HELP/Setup/', `${WIKI}/Set-up-your-frame`],
    ['/help/wifi', `${WIKI}/Troubleshooting#the-frame-cant-join-your-wi-fi`],
    ['/help/move', `${WIKI}/Troubleshooting#move-give-away-or-reset-a-frame`],
    ['/help/birdnet-go', `${WIKI}/Detection-sources#birdnet-go`],
  ]) {
    const res = await worker.fetch(new Request(`https://featherframe.app${path}`), env);
    assert.equal(res.status, 302, path);
    assert.equal(res.headers.get('Location'), to, path);
  }
});

test('an unknown topic goes to the wiki\'s home, never a 404', async () => {
  assert.equal(helpTarget('/help/no-such-thing'), `${WIKI}/Home`);
  assert.equal(helpTarget('/helpful'), null);
  assert.equal(helpTarget('/'), null);
});

test('anchors are GitHub\'s', () => {
  assert.equal(slug("The frame can't join your Wi-Fi"), 'the-frame-cant-join-your-wi-fi');
  assert.equal(slug('Move, give away, or reset a frame'), 'move-give-away-or-reset-a-frame');
  assert.equal(slug('BirdNET-Pi'), 'birdnet-pi');
});
