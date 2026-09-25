// featherframe.app's first-paint script: the cardinal's song, the Keep me posted form, the wall's Color / B&W
// switch, and — when WebGL is there and motion is welcome — the 3D frame, loaded after the page has painted. On a
// desktop the frame travels down the page (choreo.ts); on a phone it stays in the cover.
import type { SiteData } from './card';

const params = new URLSearchParams(location.search);
const holdMs = params.has('hold') ? Number(params.get('hold')) : undefined;
// The hero shows the 13-inch; ?size=10 shows the 10-inch (and renders its poster).
const size: '13' | '10' = params.get('size') === '10' ? '10' : '13';
// ?wall=<index>|table renders one of the page's stills (scripts/wall.mjs).
const wall = params.get('wall');

const root = document.documentElement;
const stage = document.getElementById('stage')!;
const poster = stage.querySelector<HTMLImageElement>('.poster')!;

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const phone = matchMedia('(max-width: 820px)');
const webgl = (() => {
  try { return !!document.createElement('canvas').getContext('webgl2'); } catch { return false; }
})();
// The journey hides the stills it replaces from the first paint, so none flashes up and away.
const journey = () => webgl && !reduced && !phone.matches && !wall;
root.classList.toggle('choreo', journey());
if (wall) root.classList.add('still-render');

let data: SiteData | undefined;
let running: { dispose(): void } | undefined;
let generation = 0;
async function mount() {
  if (!data || reduced || !webgl) return;
  const mine = ++generation;
  running?.dispose();
  running = undefined;
  root.classList.toggle('choreo', journey());
  // data-shown is a test hook (site.spec.ts): the index the frame last settled on.
  const onShown = (i: number) => { stage.dataset.shown = String(i); };
  try {
    const m = await import('./choreo');
    if (wall) return await m.startWallRender(data.sizes[size], wall);
    const r = journey()
      ? await m.startPage(data.sizes[size], { holdMs, onShown, poster: params.has('poster') })
      : await m.startStage(stage, data.sizes[size], { holdMs, onShown });
    if (mine === generation) running = r;
    else r.dispose();
  } catch (e) {
    if (mine === generation) root.classList.remove('choreo');
    console.warn('featherframe: 3D frame unavailable', e);
  }
}
phone.addEventListener('change', () => void mount());

const start = async () => {
  try {
    data = await (await fetch('species.json')).json();
    poster.src = data!.sizes[size].poster;
    void mount();
  } catch (e) {
    root.classList.remove('choreo');
    console.warn('featherframe: species data unavailable', e);
  }
};
if (document.readyState === 'complete') void start();
else addEventListener('load', () => void start(), { once: true });

// III. The cardinal's song, with a playhead across its spectrogram.
const song = document.getElementById('song') as HTMLAudioElement;
const play = document.querySelector<HTMLButtonElement>('.play')!;
const playhead = document.querySelector<HTMLElement>('.playhead')!;
let tick = 0;
const follow = () => {
  if (song.duration) playhead.style.left = `${(song.currentTime / song.duration) * 100}%`;
  tick = requestAnimationFrame(follow);
};
const setPlaying = (on: boolean) => {
  play.textContent = on ? 'Pause' : 'Play the song';
  play.setAttribute('aria-pressed', String(on));
  playhead.hidden = !on;
  cancelAnimationFrame(tick);
  if (on) follow();
};
play.addEventListener('click', () => {
  if (song.paused) song.play().catch(() => setPlaying(false));
  else song.pause();
});
song.addEventListener('play', () => setPlaying(true));
song.addEventListener('pause', () => setPlaying(false));
song.addEventListener('ended', () => { song.currentTime = 0; setPlaying(false); });

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
