// Builds featherframe.app into dist/: public/ copied as is, src/main.ts bundled
// (the viewer is a lazy chunk), every file under models/ renamed with a hash of
// its contents (models are cached for a week, so a new model needs a new name),
// and the Web Analytics beacon injected when analytics.json ({"token": "…"})
// exists.
import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { cpSync, existsSync, readdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';

const here = new URL('.', import.meta.url).pathname;
rmSync(`${here}dist`, { recursive: true, force: true });
cpSync(`${here}public`, `${here}dist`, { recursive: true });

// models/a/b.glb → models/a/b.1f2e3d4c.glb, and species.json points at the new names.
const renames = {};
for (const rel of readdirSync(`${here}dist/models`, { recursive: true })) {
  const path = `${here}dist/models/${rel}`;
  if (!/\.(glb|jpg|webp|png)$/.test(rel)) continue;
  const hash = createHash('sha256').update(readFileSync(path)).digest('hex').slice(0, 8);
  const hashed = rel.replace(/(\.[a-z]+)$/, `.${hash}$1`);
  renameSync(path, `${here}dist/models/${hashed}`);
  renames[`models/${rel}`] = `models/${hashed}`;
}
const speciesPath = `${here}dist/species.json`;
let species = readFileSync(speciesPath, 'utf8');
for (const [from, to] of Object.entries(renames)) species = species.split(`"${from}"`).join(`"${to}"`);
writeFileSync(speciesPath, species);

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
