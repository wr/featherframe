// Builds featherframe.app into dist/: public/ copied as is, src/main.ts bundled
// (the viewer is a lazy chunk), every file under models/ renamed with a hash of
// its contents (models are cached for a week, so a new model needs a new name),
// and the Web Analytics beacon injected when analytics.json ({"token": "…"})
// exists. The sitemap is dated by git.
import { build } from 'esbuild';
import { execFileSync } from 'node:child_process';
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

// The share image keeps its name, but the platforms cache a preview by the image's URL: og:image and
// twitter:image carry a hash of the file, so a new image is a new URL to them.
{
  const page = `${here}dist/index.html`;
  const og = createHash('sha256').update(readFileSync(`${here}dist/img/og.jpg`)).digest('hex').slice(0, 8);
  writeFileSync(page, readFileSync(page, 'utf8').replaceAll('https://featherframe.app/img/og.jpg"', `https://featherframe.app/img/og.jpg?v=${og}"`));
}

// The sitemap's lastmod is the day index.html last changed in git, never the build's: Google ignores a
// date that moves on every deploy (W-961). Outside a git checkout the sitemap goes without one.
{
  let day = '';
  try {
    day = execFileSync('git', ['log', '-1', '--format=%cs', '--', 'public/index.html'], { cwd: here, encoding: 'utf8' }).trim();
  } catch {}
  const sitemap = `${here}dist/sitemap.xml`;
  const home = '<loc>https://featherframe.app/</loc>';
  if (day) writeFileSync(sitemap, readFileSync(sitemap, 'utf8').replace(home, `${home}<lastmod>${day}</lastmod>`));
}

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
