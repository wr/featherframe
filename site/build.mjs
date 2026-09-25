// Builds featherframe.app into dist/: public/ copied as is, src/main.ts bundled
// (the viewer is a lazy chunk), and the Web Analytics beacon injected when
// analytics.json ({"token": "…"}) exists.
import { build } from 'esbuild';
import { cpSync, existsSync, readFileSync, rmSync, writeFileSync } from 'node:fs';

const here = new URL('.', import.meta.url).pathname;
rmSync(`${here}dist`, { recursive: true, force: true });
cpSync(`${here}public`, `${here}dist`, { recursive: true });

await build({
  entryPoints: [`${here}src/main.ts`],
  bundle: true,
  splitting: true,
  format: 'esm',
  minify: true,
  target: 'es2020',
  outdir: `${here}dist`,
  logLevel: 'warning',
});

const cfg = `${here}analytics.json`;
if (existsSync(cfg)) {
  const { token } = JSON.parse(readFileSync(cfg, 'utf8'));
  const page = `${here}dist/index.html`;
  const beacon = `<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='{"token": "${token}"}'></script>`;
  writeFileSync(page, readFileSync(page, 'utf8').replace('</body>', `${beacon}\n</body>`));
}
