// featherframe.app's first-paint script: the label card, the size switch, the
// Keep me posted form, and — when WebGL is there and motion is welcome — the
// 3D frame, loaded after the page has painted.
import { heardText, isLongName, type SiteData } from './card';

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
    // Each word keeps together, so a long name breaks between words, never at a hyphen.
    nameEl.replaceChildren(...s.name.split(' ').flatMap((w, i) => {
      const span = document.createElement('span');
      span.className = 'word';
      span.textContent = w;
      return i ? [' ', span] : [span];
    }));
    nameEl.classList.toggle('label-name--long', isLongName(s.name));
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
  try {
    data = await (await fetch('species.json')).json();
    poster.src = data!.sizes[size].poster;
    const want = params.get('size');
    if (want === '10') choose('10'); else void mount();
  } catch (e) {
    console.warn('featherframe: species data unavailable', e);
  }
};
if (document.readyState === 'complete') void start();
else addEventListener('load', () => void start(), { once: true });

// The nav's hairline appears once the page has scrolled.
const nav = document.querySelector('.nav')!;
const onScroll = () => nav.classList.toggle('scrolled', scrollY > 8);
addEventListener('scroll', onScroll, { passive: true });
onScroll();

const form = document.getElementById('keep-posted') as HTMLFormElement;
const note = form.querySelector('.form-note')!;
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = (form.elements.namedItem('email') as HTMLInputElement).value.trim();
  const button = form.querySelector('button')!;
  button.disabled = true;
  try {
    const res = await fetch(form.action, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const out = await res.json() as { ok: boolean; error?: string };
    if (out.ok) { note.textContent = "Thanks. We'll write when there's news."; form.reset(); }
    else note.textContent = out.error || "That didn't go through. Try again.";
  } catch {
    note.textContent = "That didn't go through. Try again.";
  } finally {
    button.disabled = false;
  }
});
