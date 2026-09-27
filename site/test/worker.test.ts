import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/worker.ts';

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
