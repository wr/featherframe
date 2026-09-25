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
/** The species the card names now (or is changing to). */
let showing = 0;
let swap = 0;

/** Change the card to species `i`. The frame calls this twice per change: as
 *  the new picture arrives mid-refresh, and again once it settles (a no-op
 *  unless the first call was missed). */
function showSpecies(i: number) {
  if (i === showing) return;
  showing = i;
  const s = data!.species[i];
  label.classList.add('changing');
  window.clearTimeout(swap);
  swap = window.setTimeout(() => {
    // Each word keeps together, so a long name breaks between words, never at a hyphen.
    const text = document.createElement('span');
    text.className = 'name-text';
    text.append(...s.name.split(' ').flatMap((w, i) => {
      const span = document.createElement('span');
      span.className = 'word';
      span.textContent = w;
      return i ? [' ', span] : [span];
    }));
    nameEl.replaceChildren(text);
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
  delete stage.dataset.shown;
  if (!data || reduced || !webgl) return;
  const { startViewer } = await import('./viewer');
  if (mine !== generation) return;
  try {
    const v = await startViewer(stage, data.sizes[size], {
      holdMs,
      onArriving: showSpecies,
      // data-shown is a test hook (site.spec.ts): the index the frame last settled on.
      onShown: (i) => { stage.dataset.shown = String(i); showSpecies(i); },
      poster: params.has('poster'),
    });
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

// The phone menu: the section links and Sign in, under the nav.
const nav = document.querySelector<HTMLElement>('.nav')!;
const menuBtn = nav.querySelector<HTMLButtonElement>('.menu-btn')!;
const menu = document.getElementById('menu')!;
const setMenu = (open: boolean) => {
  menu.hidden = !open;
  menuBtn.setAttribute('aria-expanded', String(open));
  nav.classList.toggle('open', open);
};
document.documentElement.classList.add('js'); // the menu button needs this script
menuBtn.addEventListener('click', () => setMenu(menu.hidden));
// Close when focus leaves the menu and its button, or a click lands outside them.
const inMenu = (n: EventTarget | null) => n instanceof Node && (menu.contains(n) || menuBtn.contains(n));
for (const el of [menu, menuBtn]) {
  el.addEventListener('focusout', (e) => { if (!menu.hidden && e.relatedTarget && !inMenu(e.relatedTarget)) setMenu(false); });
}
document.addEventListener('pointerdown', (e) => { if (!menu.hidden && !inMenu(e.target)) setMenu(false); });
menu.addEventListener('click', (e) => { if ((e.target as Element).closest('a')) setMenu(false); });
addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !menu.hidden) { setMenu(false); menuBtn.focus(); }
});
matchMedia('(min-width: 821px)').addEventListener('change', (e) => { if (e.matches) setMenu(false); });

// The nav's hairline appears once the page has scrolled.
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
