// The e-paper section's pair (index.html #pair): a timer under each frame and the Refresh button, for whichever
// draws the frames (choreo.ts on a desktop's journey, flat-pair.ts everywhere else). A timer runs while its frame
// refreshes, in the panel's own time, and stops at the refresh's length: "1.0 s" beside "15.5 s".
export type Model = '10' | '13';
export const MODELS: Model[] = ['10', '13'];

interface Progress { ms: number; total: number }

export interface Stopwatch {
  /** A refresh has been asked for: 0.0 s until it starts. */
  start(): void;
  /** Each frame, the refresh's progress() (null between refreshes). */
  update(p: Progress | null, now: number): void;
  readonly running: boolean;
}

export interface PairUI {
  readonly el: HTMLElement;
  readonly watch: Record<Model, Stopwatch>;
  /** Whether Refresh can be pressed. */
  ready(on: boolean): void;
  /** Refresh pressed; returns the undo. */
  onRefresh(cb: () => void): () => void;
  /** The pair is drawn live: the timers and the button show. */
  live(on: boolean): void;
}

const fmt = (ms: number) => `${(ms / 1000).toFixed(1)} s`;
/** A refresh asked for that has not started by then (its picture never loaded) is given up. */
const STALE_MS = 10_000;

export function pairUI(): PairUI | null {
  const el = document.getElementById('pair');
  const button = el?.querySelector<HTMLButtonElement>('.run button');
  if (!el || !button) return null;
  const watch = {} as Record<Model, Stopwatch>;
  for (const m of MODELS) {
    const out = el.querySelector<HTMLElement>(`.ep.s${m} .timer`)!;
    let running = false, last: Progress | null = null, since = 0, text = out.textContent ?? '';
    const show = (t: string) => { if (t !== text) out.textContent = text = t; };
    watch[m] = {
      start() { running = true; last = null; since = performance.now(); show(fmt(0)); },
      update(p, now) {
        if (!running) return;
        if (p) { last = p; show(fmt(p.ms)); return; }
        if (last) show(fmt(last.total));
        else if (now - since < STALE_MS) return;
        else show('');
        running = false;
      },
      get running() { return running; },
    };
  }
  return {
    el,
    watch,
    ready(on) { if (button.disabled === on) button.disabled = !on; },
    onRefresh(cb) {
      button.addEventListener('click', cb);
      return () => button.removeEventListener('click', cb);
    },
    live(on) { el.classList.toggle('live', on); },
  };
}

/** The two pictures the pair refreshes between, in each frame's own drawing: the Wild Turkey (the art spread's) and
 *  the Cedar Waxwing (the cover's first). */
export const pairPictures = (size: { wall: string[]; screens: string[] }) => [size.wall[0], size.screens[0]] as const;
