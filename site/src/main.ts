// featherframe.app's first-paint script: the tag beside the hero's frame, the
// cardinal's song, the Keep me posted form, and — when WebGL is there and
// motion is welcome — the 3D frame, loaded after the page has painted.
import { heardText, isLongName, type SiteData } from './card';

const params = new URLSearchParams(location.search);
const holdMs = params.has('hold') ? Number(params.get('hold')) : undefined;
// The hero shows the 13-inch; ?size=10 shows the 10-inch (and renders its poster).
const size: '13' | '10' = params.get('size') === '10' ? '10' : '13';

const stage = document.getElementById('stage')!;
const poster = stage.querySelector<HTMLImageElement>('.poster')!;
const label = document.getElementById('label')!;
const nameEl = label.querySelector('.label-name')!;
const heardEl = label.querySelector('.label-heard')!;

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const webgl = (() => {
  try { return !!document.createElement('canvas').getContext('webgl2'); } catch { return false; }
})();

let data: SiteData | undefined;
/** The species the tag names now (or is changing to). */
let showing = 0;
let swap = 0;

/** Change the tag to species `i`. The frame calls this twice per change: as
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
    heardEl.textContent = heardText(s.heard);
    label.classList.remove('changing');
  }, 350);
}

async function mount() {
  if (!data || reduced || !webgl) return;
  const { startViewer } = await import('./viewer');
  try {
    await startViewer(stage, data.sizes[size], {
      holdMs,
      onArriving: showSpecies,
      // data-shown is a test hook (site.spec.ts): the index the frame last settled on.
      onShown: (i) => { stage.dataset.shown = String(i); showSpecies(i); },
      poster: params.has('poster'),
    });
  } catch (e) {
    console.warn('featherframe: 3D frame unavailable', e);
  }
}

const start = async () => {
  try {
    data = await (await fetch('species.json')).json();
    poster.src = data!.sizes[size].poster;
    void mount();
  } catch (e) {
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
