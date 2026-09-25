// featherframe.app's first-paint script: the cardinal's song, the Keep me posted form, the wall's Color / B&W
// switch, and — when WebGL is there and motion is welcome — the 3D frame, loaded after the page has painted. On a
// desktop the frame travels down the page (choreo.ts); on a phone it stays in the cover.
import type { SiteData } from './card';
import { startSheen } from './sheen';
import { startSeasons } from './seasons';

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
      ? await m.startPage(data, size, { holdMs, onShown, poster: params.has('poster') })
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

// II. The wall's Color / B&W switch: the 13-inch in colour, or the 10-inch in sixteen grays — drawn to
// scale, so the change is a change of size too — remembered. <html data-tone> says which; the journey
// (choreo.ts) follows it with an ff-tone event.
const TONE_KEY = 'featherframe.wall';
const tones = [...document.querySelectorAll<HTMLButtonElement>('.tone button')];
const setTone = (tone: string, keep: boolean) => {
  if (tone !== '13' && tone !== '10') return;
  for (const b of tones) b.setAttribute('aria-pressed', String(b.dataset.tone === tone));
  for (const img of document.querySelectorAll<HTMLImageElement>('.wall img[data-still]')) {
    img.src = `img/wall/${tone}-${img.dataset.still}.webp`;
  }
  root.classList.toggle('tone-anim', keep);
  root.dataset.tone = tone;
  document.dispatchEvent(new Event('ff-tone'));
  if (keep) try { localStorage.setItem(TONE_KEY, tone); } catch { /* private mode: this visit only */ }
};
let stored: string | null = null;
try { stored = localStorage.getItem(TONE_KEY); } catch { /* storage blocked: colour */ }
setTone(stored ?? '13', false);
for (const b of tones) b.addEventListener('click', () => setTone(b.dataset.tone!, true));
if (!reduced) startSheen([...document.querySelectorAll<HTMLElement>('.wall .cat .im')]);

// IV. The seasons' row, sliding sideways as the page scrolls (desktop, motion welcome).
if (!reduced && !wall) startSeasons();

// III. The detections: each species' recording plays itself, muted, while its spectrogram is on screen, a
// playhead crossing it; when one ends the next is heard — a card by the song says so (New detection), and the
// frame on the table repaints to it (choreo.ts follows <html data-detected>). Unmute plays the recording from
// the start with sound, and the ones after it too, until Mute. With reduced motion nothing plays by itself:
// the button reads Play the song and plays the one on screen.
const DETECTIONS = [
  { slug: 'cardinal', name: 'Northern Cardinal', audio: 'audio/cardinal-song.mp3', spectrogram: 'img/spectrogram.webp' },
  { slug: 'blue-jay', name: 'Blue Jay', audio: 'audio/blue-jay-song.mp3', spectrogram: 'img/spectrogram-blue-jay.webp' },
  { slug: 'goldfinch', name: 'American Goldfinch', audio: 'audio/goldfinch-song.mp3', spectrogram: 'img/spectrogram-goldfinch.webp' },
];
const TOAST_MS = 4500;
const song = document.getElementById('song') as HTMLAudioElement;
const spectro = document.querySelector<HTMLElement>('.spectro')!;
const spectroImg = spectro.querySelector('img')!;
const playhead = spectro.querySelector<HTMLElement>('.playhead')!;
const unmute = spectro.querySelector<HTMLButtonElement>('.unmute')!;
const mute = spectro.querySelector<HTMLButtonElement>('.mute')!;
const toast = spectro.querySelector<HTMLElement>('.toast')!;
const table = document.getElementById('table-slot');
let heard = 0, sound = false, inView = false, follow = 0, toastTimer = 0;
if (reduced) unmute.textContent = 'Play the song';

const track = () => {
  if (song.duration) playhead.style.left = `${(song.currentTime / song.duration) * 100}%`;
  follow = requestAnimationFrame(track);
};
const detect = (i: number) => {
  heard = i;
  const d = DETECTIONS[i];
  root.dataset.detected = d.slug;
  if (table) table.dataset.species = d.slug;
  document.dispatchEvent(new Event('ff-detect'));
  toast.querySelector('.nm')!.textContent = d.name;
  toast.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove('on'), TOAST_MS);
};
const load = (i: number) => {
  const d = DETECTIONS[i];
  const rate = song.playbackRate; // (tests speed it up)
  song.src = d.audio;
  song.playbackRate = rate;
  spectroImg.src = d.spectrogram;
  spectroImg.alt = `A spectrogram of a ${d.name}'s song`;
};
const play = () => {
  song.muted = !sound;
  song.play().catch(() => { /* autoplay refused: the button still plays it */ });
};
song.addEventListener('play', () => {
  playhead.hidden = false;
  cancelAnimationFrame(follow);
  track();
});
song.addEventListener('pause', () => { cancelAnimationFrame(follow); });
song.addEventListener('ended', () => {
  cancelAnimationFrame(follow);
  playhead.hidden = true;
  if (reduced) {
    // nothing plays by itself: stand ready to play again
    song.currentTime = 0;
    sound = false;
    unmute.hidden = false;
    mute.hidden = true;
    return;
  }
  const next = (heard + 1) % DETECTIONS.length;
  load(next);
  detect(next);
  if (inView) play();
});
unmute.addEventListener('click', () => {
  sound = true;
  song.currentTime = 0;
  if (song.paused || reduced) detect(heard);
  play();
  unmute.hidden = true;
  mute.hidden = false;
  mute.focus();
});
mute.addEventListener('click', () => {
  sound = false;
  song.muted = true;
  if (reduced) song.pause();
  mute.hidden = true;
  unmute.hidden = false;
  unmute.focus();
});
new IntersectionObserver(([e]) => {
  inView = e.isIntersecting;
  if (reduced) return;
  if (inView) {
    if (song.paused) {
      if (song.preload !== 'auto') { song.preload = 'auto'; }
      if (!song.currentTime && !toast.classList.contains('on')) detect(heard);
      play();
    }
  } else if (!song.paused) song.pause();
}, { threshold: 0.4 }).observe(spectro);

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
