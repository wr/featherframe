// featherframe.app's first-paint script: the label card, the size switch, the
// Keep me posted form, and — when WebGL is there and motion is welcome — the
// 3D frame, loaded after the page has painted.
import { heardText, type SiteData } from './card';

const params = new URLSearchParams(location.search);
const holdMs = params.has('hold') ? Number(params.get('hold')) : undefined;

const stage = document.getElementById('stage')!;
const poster = stage.querySelector<HTMLImageElement>('.poster')!;
const label = document.getElementById('label')!;
const nameEl = label.querySelector('.label-name')!;
const latinEl = label.querySelector('.label-latin')!;
const heardEl = label.querySelector('.label-heard')!;
const buttons = [...label.querySelectorAll<HTMLButtonElement>('.sizes-switch button')];

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const webgl = (() => {
  try { return !!document.createElement('canvas').getContext('webgl2'); } catch { return false; }
})();

let data: SiteData | undefined;
let size: '13' | '10' = '13';
let viewer: { dispose(): void } | undefined;
let generation = 0;

function showSpecies(i: number) {
  const s = data!.species[i];
  label.classList.add('changing');
  window.setTimeout(() => {
    nameEl.textContent = s.name;
    latinEl.textContent = s.latin;
    heardEl.textContent = heardText(s.heard);
    label.classList.remove('changing');
  }, 350);
}

async function mount() {
  const mine = ++generation;
  viewer?.dispose();
  viewer = undefined;
  if (!data || reduced || !webgl) return;
  const { startViewer } = await import('./viewer');
  if (mine !== generation) return;
  try {
    const v = await startViewer(stage, data.sizes[size], { holdMs, onShown: showSpecies, poster: params.has('poster') });
    if (mine !== generation) v.dispose(); else viewer = v;
  } catch (e) {
    console.warn('featherframe: 3D frame unavailable', e);
  }
}

function choose(next: '13' | '10') {
  if (next === size) return;
  size = next;
  for (const b of buttons) b.setAttribute('aria-pressed', String(b.dataset.size === size));
  if (data) poster.src = data.sizes[size].poster;
  if (data) showSpecies(0);
  void mount();
}

for (const b of buttons) b.addEventListener('click', () => choose(b.dataset.size as '13' | '10'));

const start = async () => {
  data = await (await fetch('species.json')).json();
  const want = params.get('size');
  if (want === '10') choose('10'); else void mount();
};
if (document.readyState === 'complete') void start();
else addEventListener('load', () => void start(), { once: true });
