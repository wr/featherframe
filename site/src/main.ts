// featherframe.app's first-paint script: the cardinal's song, the Keep me posted form, the wall's Color / B&W
// switch, and — when WebGL is there and motion is welcome — the 3D frame, loaded after the page has painted. On a
// desktop the frame travels down the page (choreo.ts); on a phone it stays in the cover.
import type { SiteData } from './card';
import { startSheen } from './sheen';
import { startSeasons } from './seasons';
import { startNight } from './night';
import { startLightbox } from './lightbox';

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
// …and any of its frames opens large on a click.
if (!wall) startLightbox(reduced);

// IV. The seasons' row, sliding sideways as the page scrolls (desktop, motion welcome).
if (!reduced && !wall) startSeasons();
// …and the page turns to night around it.
if (!wall) startNight();

// III. The detections: each species' recording plays itself, muted, while its video is on screen, its
// spectrogram laid over the video with a playhead crossing it; when one ends the next is heard — the video
// changes to that species, the card on the video says so (New detection: it stays up, and its name changes
// with each detection), and the frame on the table repaints to it (choreo.ts follows <html data-detected>). Unmute plays the recording from
// the start with sound, and the ones after it too, until Mute. With reduced motion nothing plays by itself:
// the button reads Play the song and plays the one on screen. The spectrogram itself is the same switch, both
// ways (aria-pressed: with sound). The card is a notification on the video (BirdNET's, not the book's).
// With reduced motion the video does not play: its poster frame shows.
const DETECTIONS = [
  { slug: 'cardinal', name: 'Northern Cardinal', audio: 'audio/cardinal-song.mp3', spectrogram: 'img/spectrogram.webp', video: 'video/cardinal', credit: 'Video by Paul Danese, Wikimedia Commons' },
  { slug: 'blue-jay', name: 'Blue Jay', audio: 'audio/blue-jay-song.mp3', spectrogram: 'img/spectrogram-blue-jay.webp', video: 'video/blue-jay', credit: 'Video by Paul Danese, Wikimedia Commons' },
  { slug: 'goldfinch', name: 'American Goldfinch', audio: 'audio/goldfinch-song.mp3', spectrogram: 'img/spectrogram-goldfinch.webp', video: 'video/goldfinch', credit: 'Video by teyi 徐, Pexels' },
];
const song = document.getElementById('song') as HTMLAudioElement;
const ph = document.querySelector<HTMLElement>('#how .ph')!;
const video = document.getElementById('bird') as HTMLVideoElement;
const credit = document.querySelector<HTMLElement>('#how .credit')!;
const spectro = ph.querySelector<HTMLElement>('.spectro')!;
const spectroImg = spectro.querySelector('img')!;
const sg = spectro.querySelector<HTMLElement>('.sg')!;
const playhead = spectro.querySelector<HTMLElement>('.playhead')!;
const unmute = ph.querySelector<HTMLButtonElement>('.unmute')!;
const mute = ph.querySelector<HTMLButtonElement>('.mute')!;
const toast = document.querySelector<HTMLElement>('#how .toast')!;
const toastName = toast.querySelector<HTMLElement>('.nm')!;
const table = document.getElementById('table-slot');
let heard = 0, sound = false, inView = false, follow = 0;
if (reduced) unmute.textContent = 'Play the song';
const playVideo = () => {
  if (reduced || !inView) return;
  if (video.preload !== 'auto') video.preload = 'auto';
  video.play().catch(() => { /* refused: the poster stays */ });
};
/** The video of detection `i`: its poster at once, then (in view, motion welcome) its loop. */
const showVideo = (i: number) => {
  const d = DETECTIONS[i];
  if (video.dataset.species === d.slug) return;
  video.dataset.species = d.slug;
  video.setAttribute('aria-label', d.name);
  credit.textContent = d.credit;
  const sources = video.querySelectorAll('source');
  sources[0].src = `${d.video}.webm`;
  sources[1].src = `${d.video}.mp4`;
  video.poster = `${d.video}.webp`;
  video.load();
  playVideo();
};
video.dataset.species = DETECTIONS[0].slug;

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
  showVideo(i);
  // the card comes in once and stays; each detection after that changes its name, animated
  const was = toast.classList.contains('on');
  toastName.textContent = d.name;
  toast.classList.add('on');
  if (was) {
    toast.classList.remove('swap');
    void toast.offsetWidth;
    toast.classList.add('swap');
  }
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
    sg.setAttribute('aria-pressed', 'false');
    return;
  }
  const next = (heard + 1) % DETECTIONS.length;
  load(next);
  detect(next);
  if (inView) play();
});
const withSound = (focus: boolean) => {
  sound = true;
  song.currentTime = 0;
  if (song.paused || reduced) detect(heard);
  play();
  unmute.hidden = true;
  mute.hidden = false;
  sg.setAttribute('aria-pressed', 'true');
  if (focus) mute.focus();
};
const withoutSound = (focus: boolean) => {
  sound = false;
  song.muted = true;
  if (reduced) song.pause();
  mute.hidden = true;
  unmute.hidden = false;
  sg.setAttribute('aria-pressed', 'false');
  if (focus) unmute.focus();
};
unmute.addEventListener('click', () => withSound(true));
mute.addEventListener('click', () => withoutSound(true));
const toggle = () => (sound ? withoutSound(false) : withSound(false));
sg.addEventListener('click', toggle);
sg.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
});
new IntersectionObserver(([e]) => {
  inView = e.isIntersecting;
  if (reduced) return;
  if (inView) {
    playVideo();
    if (song.paused) {
      if (song.preload !== 'auto') { song.preload = 'auto'; }
      if (!song.currentTime && !toast.classList.contains('on')) detect(heard);
      play();
    }
  } else {
    if (!video.paused) video.pause();
    if (!song.paused) song.pause();
  }
}, { threshold: 0.4 }).observe(ph);

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
