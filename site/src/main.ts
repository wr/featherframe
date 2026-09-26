// featherframe.app's first-paint script: the cardinal's song, the Keep me posted form, the wall's Color / B&W
// switch, and — when WebGL is there and motion is welcome — the 3D frame, loaded after the page has painted. On a
// desktop the frame travels down the page (choreo.ts); a phone keeps the cover's poster (the 3D frame's model and
// screens cost megabytes for a picture a few hundred pixels wide).
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
let running: { dispose(): void; landing?(section: string): [number, number] | null } | undefined;
let generation = 0;
async function mount() {
  if (!data || reduced || !webgl) return;
  const mine = ++generation;
  running?.dispose();
  running = undefined;
  root.classList.toggle('choreo', journey());
  if (phone.matches && !wall) return;
  // data-shown is a test hook (site.spec.ts): the index the frame last settled on.
  const onShown = (i: number) => { stage.dataset.shown = String(i); };
  try {
    const m = await import('./choreo');
    if (wall) return await m.startWallRender(data.sizes[size], wall);
    const r = await m.startPage(data, size, { holdMs, onShown, poster: params.has('poster') });
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

// III. The detections, on a clock: while the video is on screen each species' detection lasts as long as its
// recording (DURATION_MS), its spectrogram laid over the video with a playhead crossing it in time; then the next is
// heard — the video changes to that species, the card on the video says so (New detection: it stays up, and its
// name changes with each detection), and the frame on the table repaints to it (choreo.ts follows
// <html data-detected>). Nothing is played to drive it: a phone refuses a muted <audio> that starts by itself, so
// the clock is the page's own, and the recording is heard only after Unmute, which plays it from the start with
// sound — and the ones after it too — until a click on the video (or its spectrogram) mutes it again. With reduced
// motion nothing moves by itself: the button reads Play the song and plays the one on screen. The spectrogram is
// the switch for the keyboard, both ways (aria-pressed: with sound; its label says what a press will do). The card is a notification on the video (BirdNET's, not the book's). With reduced motion the
// video does not play: its poster frame shows. ?rate= runs the clock faster (tests).
const DETECTIONS = [
  { slug: 'cardinal', name: 'Northern Cardinal', audio: 'audio/cardinal-song.mp3', spectrogram: 'img/spectrogram.webp', video: 'video/cardinal', credit: 'Video by Courtney Celley, U.S. Fish and Wildlife Service' },
  { slug: 'eastern-bluebird', name: 'Eastern Bluebird', audio: 'audio/eastern-bluebird-song.mp3', spectrogram: 'img/spectrogram-eastern-bluebird.webp', video: 'video/eastern-bluebird', credit: 'Video by Paul Danese, Wikimedia Commons' },
  { slug: 'goldfinch', name: 'American Goldfinch', audio: 'audio/goldfinch-song.mp3', spectrogram: 'img/spectrogram-goldfinch.webp', video: 'video/goldfinch', credit: 'Video by teyi 徐, Pexels' },
];
/** Each recording's length (the spectrogram spans it), ms. */
const DURATION_MS = [11000, 11000, 11000];
const rate = Math.max(0.1, Number(params.get('rate')) || 1);
const song = document.getElementById('song') as HTMLAudioElement;
const ph = document.querySelector<HTMLElement>('#how .ph')!;
const video = document.getElementById('bird') as HTMLVideoElement;
const credit = document.querySelector<HTMLElement>('#how .t2 .credit')!;
const spectro = ph.querySelector<HTMLElement>('.spectro')!;
const spectroImg = spectro.querySelector('img')!;
const sg = spectro.querySelector<HTMLElement>('.sg')!;
const playhead = spectro.querySelector<HTMLElement>('.playhead')!;
const unmute = ph.querySelector<HTMLButtonElement>('.unmute')!;
const toast = document.querySelector<HTMLElement>('#how .toast')!;
const toastName = toast.querySelector<HTMLElement>('.nm')!;
const table = document.getElementById('table-slot');
const tableStill = table?.querySelector<HTMLImageElement>('.still');
let heard = 0, sound = false, inView = false, follow = 0, timer = 0;
/** The clock: when the detection on screen started (performance.now), and how far it had got when it last paused. */
let started = 0, elapsed = 0, ticking = false;
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

const duration = () => DURATION_MS[heard] / rate;
const track = () => {
  const t = ticking ? elapsed + (performance.now() - started) : elapsed;
  // with sound, the playhead follows the recording itself
  const f = sound && !song.paused && song.duration ? song.currentTime / song.duration : t / duration();
  playhead.style.left = `${Math.min(1, f) * 100}%`;
  if (ticking || !song.paused) follow = requestAnimationFrame(track);
};
const detect = (i: number) => {
  heard = i;
  const d = DETECTIONS[i];
  root.dataset.detected = d.slug;
  if (table) table.dataset.species = d.slug;
  if (tableStill) tableStill.alt = `The frame showing the ${d.name} from The Birds of America`;
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
  song.src = d.audio;
  spectroImg.src = d.spectrogram;
  spectroImg.alt = `A spectrogram of ${/^[AEIOU]/.test(d.name) ? 'an' : 'a'} ${d.name}’s song`;
};
const playSong = () => {
  song.muted = false;
  song.playbackRate = rate;
  song.play().catch(() => { /* refused: the clock goes on without it */ });
};
/** Run the clock from where it was. */
const run = () => {
  if (ticking || reduced) return;
  ticking = true;
  started = performance.now();
  playhead.hidden = false;
  clearTimeout(timer);
  timer = window.setTimeout(next, Math.max(0, duration() - elapsed));
  cancelAnimationFrame(follow);
  track();
};
const pause = () => {
  if (!ticking) return;
  elapsed += performance.now() - started;
  ticking = false;
  clearTimeout(timer);
};
/** The next detection, from its start. */
function next() {
  ticking = false;
  elapsed = 0;
  const i = (heard + 1) % DETECTIONS.length;
  load(i);
  detect(i);
  if (sound) playSong();
  if (inView) run();
}
song.addEventListener('play', () => {
  playhead.hidden = false;
  cancelAnimationFrame(follow);
  track();
});
song.addEventListener('ended', () => {
  if (!reduced) return;
  // nothing plays by itself: stand ready to play again
  cancelAnimationFrame(follow);
  playhead.hidden = true;
  song.currentTime = 0;
  sound = false;
  unmute.hidden = false;
  pressed(false);
});
const pressed = (on: boolean) => {
  sg.setAttribute('aria-pressed', String(on));
  sg.setAttribute('aria-label', on ? 'Mute the song' : 'Play the song with sound');
};
const withSound = (focus: boolean) => {
  sound = true;
  // this detection again, from its start, with its recording
  pause();
  elapsed = 0;
  if (!toast.classList.contains('on') || reduced) detect(heard);
  song.currentTime = 0;
  playSong();
  if (inView) run();
  unmute.hidden = true;
  pressed(true);
  if (focus) sg.focus();
};
const withoutSound = (focus: boolean) => {
  sound = false;
  song.pause();
  unmute.hidden = false;
  pressed(false);
  if (focus) unmute.focus();
};
unmute.addEventListener('click', () => withSound(true));
const toggle = () => (sound ? withoutSound(false) : withSound(false));
// a click anywhere on the video, the spectrogram included, is the same switch as the spectrogram's key
ph.addEventListener('click', (e) => {
  if ((e.target as Element).closest('.unmute')) return;
  toggle();
});
sg.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
});
new IntersectionObserver(([e]) => {
  inView = e.isIntersecting;
  if (reduced) return;
  if (inView) {
    playVideo();
    if (!toast.classList.contains('on')) detect(heard);
    if (sound && song.paused && song.currentTime > 0 && !song.ended) playSong();
    run();
  } else {
    pause();
    if (!video.paused) video.pause();
    if (!song.paused) song.pause();
  }
}, { threshold: 0.4 }).observe(ph);

// The running head's links: each lands its section with the eyebrow just under the running head — and, where the
// journey holds the frame for a section (the art, pinned beside its headline), inside that hold.
const GAP = 24;
const landing = (sec: HTMLElement) => {
  const nav = document.querySelector('.head')?.getBoundingClientRect().height ?? 0;
  const brow = sec.querySelector('.eyebrow') ?? sec;
  let y = brow.getBoundingClientRect().top + scrollY - nav - GAP;
  const hold = running?.landing?.(sec.id);
  if (hold) y = Math.min(Math.max(y, hold[0] + 1), hold[1] - 1);
  return Math.max(0, Math.round(y));
};
for (const a of document.querySelectorAll<HTMLAnchorElement>('a[href^="#"]')) {
  a.addEventListener('click', (e) => {
    const sec = document.getElementById(a.hash.slice(1));
    if (!sec) return;
    e.preventDefault();
    scrollTo({ top: landing(sec), behavior: reduced ? 'auto' : 'smooth' });
    history.pushState(null, '', a.hash);
  });
}
// a page opened at a section's link (#specs) lands the same way, once it has laid out
addEventListener('load', () => {
  const sec = location.hash && document.getElementById(location.hash.slice(1));
  if (sec) void document.fonts.ready.then(() => scrollTo({ top: landing(sec) }));
}, { once: true });

// the wall's folio paints its paper only while it is stuck under the running head (styles.css .wall .folio.stuck)
const folio = document.querySelector<HTMLElement>('.wall .folio');
const head = document.querySelector<HTMLElement>('.head');
if (folio && head) {
  let raf = 0;
  const paint = () => {
    raf = 0;
    const stuck = getComputedStyle(folio).position === 'sticky' && folio.getBoundingClientRect().top <= head.getBoundingClientRect().bottom + 0.5;
    if (folio.classList.contains('stuck') !== stuck) folio.classList.toggle('stuck', stuck);
  };
  const request = () => { if (!raf) raf = requestAnimationFrame(paint); };
  addEventListener('scroll', request, { passive: true });
  addEventListener('resize', request);
  request();
}

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
    if (out.ok) { note.textContent = "Thanks. We'll write once, when the frames ship."; form.reset(); }
    else note.textContent = out.error || "That didn't go through. Try again.";
  } catch {
    note.textContent = "That didn't go through. Try again.";
  } finally {
    button.disabled = false;
  }
});
